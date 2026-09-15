#!/usr/bin/env python3
"""lark-channel-sdk 文档合集同步脚本 (self-contained)。

从官方仓库 `larksuite/channel-sdk-python` 的 `docs/` 目录拉取全部 Markdown
文件，按路径排序拼接为一个 Markdown 合集文件。对齐同目录 `volc-docs-sync`
的做法：外部文档 → 抓成快照进 `common/docs/` 纯文件，而不是挂 git submodule。

用法::

    # 用内置默认配置更新合集（main 分支 docs/ -> common/docs/lark_channel_sdk_docs.md）
    python3 update_docs.py

    # 只抓取并打印诊断（每个文件路径 / 字节数），不写文件
    python3 update_docs.py --dry-run

    # 自定义仓库 / 分支 / 输出路径
    python3 update_docs.py --repo larksuite/channel-sdk-python --ref main \\
        --out ../docs/lark_channel_sdk_docs.md

设计目标（对齐用户偏好）：
  * 高度自包含：只依赖 Python 标准库；格式化用 `npx prettier`（可用时），
    不可用时自动降级为「不格式化」并给出提示，不会硬失败。
  * 路径自适配：默认输出路径基于脚本自身位置解析，换机器也能开箱即用。
  * 幂等：重复运行只反映上游真实内容变化，不产生格式抖动。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# --- 默认配置 ---------------------------------------------------------------

DEFAULT_REPO = "larksuite/channel-sdk-python"
DEFAULT_REF = "main"
DOCS_PREFIX = "docs/"

TREE_TMPL = "https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
RAW_TMPL = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"

# 脚本相对 common/ 目录的默认输出（common/skills/lark-channel-docs-sync/ -> common/ -> docs/...）。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_COMMON_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
DEFAULT_OUT = os.path.join(_COMMON_ROOT, "docs", "lark_channel_sdk_docs.md")

TITLE = "# lark-channel-sdk · 官方文档合集"

USER_AGENT = "Mozilla/5.0 (compatible; lark-channel-docs-sync/1.0)"


# --- 数据抓取 ---------------------------------------------------------------


def _http_get(url: str, accept: str, retries: int = 3, timeout: int = 30) -> bytes:
    """GET 一个 URL，失败自动重试。返回原始 bytes。"""
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": accept}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(1.0 * attempt)
    raise RuntimeError(f"GET 失败（已重试 {retries} 次）: {url}\n  {last_err}")


def list_doc_paths(repo: str, ref: str) -> list[str]:
    """列出仓库 docs/ 目录下所有 Markdown 文件路径（按路径升序）。"""
    payload = json.loads(_http_get(TREE_TMPL.format(repo=repo, ref=ref), "application/json"))
    tree = payload.get("tree")
    if not tree:
        raise RuntimeError(f"GitHub tree 接口未返回 tree（可能被限流）: {json.dumps(payload)[:300]}")
    paths = [
        t["path"]
        for t in tree
        if t.get("type") == "blob"
        and t["path"].startswith(DOCS_PREFIX)
        and t["path"].endswith(".md")
    ]
    return sorted(paths)


def fetch_doc(repo: str, ref: str, path: str) -> str:
    """拉取单个 Markdown 文件的原文。"""
    return _http_get(RAW_TMPL.format(repo=repo, ref=ref, path=path), "text/plain").decode("utf-8")


# --- 内容归一化 -------------------------------------------------------------


def anchor_for(path: str) -> str:
    """由 docs 路径生成稳定锚点，如 docs/release-notes/v1.0.0.md -> doc-release-notes-v1-0-0。"""
    rel = path[len(DOCS_PREFIX):] if path.startswith(DOCS_PREFIX) else path
    rel = rel[:-3] if rel.endswith(".md") else rel  # 去 .md
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", rel).strip("-").lower()
    return f"doc-{slug}"


def demote_headings(md: str) -> str:
    """把正文里的一级标题 `# x` 降为二级，避免与合集里每页的 `##` 抢层级。

    只处理行首的 `# `（一级），更深的层级保持不变。代码围栏内的 `#` 不受影响
    （围栏内不会以「# 」+ 文字这种 Markdown 标题形态出现的概率极低，且 Prettier
    也不会把它当标题；这里用简单启发式，够用即可）。
    """
    out_lines = []
    in_fence = False
    for line in md.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if not in_fence and re.match(r"^#\s+\S", line):
            line = "#" + line  # 一级 -> 二级
        out_lines.append(line)
    return "\n".join(out_lines)


def normalize_body(md: str, path_to_anchor: dict[str, str], cur_path: str) -> str:
    """把指向 docs/ 内其它 md 的相对链接改写为合集内锚点跳转。

    上游文档之间用相对链接互引（如 `[quickstart](./quickstart.md)`、
    `[reference](reference.md)`）。合并成单文件后这些链接会失效，这里把它们
    改写成 `#doc-xxx` 锚点。无法解析的相对链接原样保留。
    """
    cur_dir = os.path.dirname(cur_path)  # e.g. "docs" or "docs/release-notes"

    def repl(m: re.Match) -> str:
        label, target = m.group(1), m.group(2)
        raw = target.split("#", 1)[0]  # 去掉目标里自带的 #frag
        if not raw or raw.startswith(("http://", "https://", "#", "mailto:")):
            return m.group(0)
        # 相对路径规范化到仓库根下的 docs/... 形态
        joined = os.path.normpath(os.path.join(cur_dir, raw))
        anchor = path_to_anchor.get(joined)
        if anchor:
            return f"[{label}](#{anchor})"
        return m.group(0)

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, md).strip()


def build_section(path: str, md: str, path_to_anchor: dict[str, str]) -> str:
    """拼接单个文件的 section：锚点 + 分隔线 + 标题（用相对路径）+ 来源 + 正文。"""
    rel = path[len(DOCS_PREFIX):]
    body = demote_headings(normalize_body(md, path_to_anchor, path))
    parts = [
        f'<a id="{path_to_anchor[path]}"></a>',
        "",
        "---",
        "",
        f"## {rel}",
        "",
        f"> 来源：`{path}`",
        "",
        body,
    ]
    return "\n".join(parts)


def build_header(paths: list[str], repo: str, ref: str, path_to_anchor: dict[str, str]) -> str:
    toc_lines = [
        f"- [{p[len(DOCS_PREFIX):]}](#{path_to_anchor[p]})" for p in paths
    ]
    return "\n".join(
        [
            TITLE,
            "",
            f"> 来源：`github.com/{repo}` 分支 `{ref}` 的 `docs/` 目录，逐文件拼接而成。",
            f"> 由 `common/skills/lark-channel-docs-sync/update_docs.py` 生成，请勿手工编辑。",
            "",
            "## 目录",
            "",
            *toc_lines,
        ]
    )


def assemble(docs: list[tuple[str, str]], repo: str, ref: str) -> str:
    """docs: list of (path, md)。返回整篇未格式化的 Markdown。"""
    paths = [p for p, _ in docs]
    path_to_anchor = {p: anchor_for(p) for p in paths}
    header = build_header(paths, repo, ref, path_to_anchor)
    sections = [build_section(p, md, path_to_anchor) for p, md in docs]
    return header + "\n\n" + "\n\n".join(sections) + "\n"


# --- 格式化 -----------------------------------------------------------------


def prettier_format(text: str) -> tuple[str, bool]:
    """用 Prettier 统一格式化。返回 (结果文本, 是否成功格式化)。

    Prettier 不可用时降级为原样返回，并返回 False 让调用方给出提示。
    """
    npx = shutil.which("npx")
    if not npx:
        return text, False
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [npx, "--yes", "prettier@3", "--parser", "markdown", "--prose-wrap", "preserve", tmp_path],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            sys.stderr.write(f"[warn] prettier 失败，输出未格式化：\n{proc.stderr}\n")
            return text, False
        return proc.stdout, True
    except (subprocess.TimeoutExpired, OSError) as exc:
        sys.stderr.write(f"[warn] 调用 prettier 异常，输出未格式化：{exc}\n")
        return text, False
    finally:
        os.unlink(tmp_path)


# --- 完整性自检 -------------------------------------------------------------


def _count_markers(text: str) -> dict[str, int]:
    return {"代码围栏 ```": text.count("```")}


def check_integrity(before: str, after: str) -> list[str]:
    """比较格式化前后关键标记数量，返回告警（为空表示无损）。"""
    warnings: list[str] = []
    b, a = _count_markers(before), _count_markers(after)
    for name in b:
        if a[name] < b[name]:
            warnings.append(f"格式化后「{name}」数量减少：{b[name]} -> {a[name]}（疑似代码块被吞，请检查）")
    return warnings


# --- 主流程 -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步 lark-channel-sdk 官方文档合集")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub 仓库 owner/name")
    parser.add_argument("--ref", default=DEFAULT_REF, help="分支或 tag（如 main / v1.4.0）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出 Markdown 文件路径")
    parser.add_argument("--no-format", action="store_true", help="跳过 Prettier 格式化")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印诊断，不写文件")
    parser.add_argument("--sleep", type=float, default=0.1, help="每次请求之间的间隔秒数")
    args = parser.parse_args(argv)

    print(f"列举 {args.repo}@{args.ref} 的 {DOCS_PREFIX} 文件…")
    paths = list_doc_paths(args.repo, args.ref)
    if not paths:
        print("[error] docs/ 下没有找到任何 .md 文件")
        return 1
    print(f"共 {len(paths)} 个文件")

    docs: list[tuple[str, str]] = []
    for i, path in enumerate(paths):
        md = fetch_doc(args.repo, args.ref, path)
        print(f"  {path:<40} bytes={len(md.encode('utf-8'))}")
        docs.append((path, md))
        if args.sleep and i < len(paths) - 1:
            time.sleep(args.sleep)

    text = assemble(docs, args.repo, args.ref)

    if not args.no_format:
        raw = text
        text, ok = prettier_format(text)
        if not ok:
            print("[warn] 未进行 Prettier 格式化（缺少 npx 或执行失败），输出为拼接原文。")
        else:
            for w in check_integrity(raw, text):
                print(f"[warn] {w}")

    if args.dry_run:
        print(f"\n[dry-run] 已生成 {len(text)} 字符，未写入。目标路径：{args.out}")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"\n已写入：{args.out}（{len(text)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
