#!/usr/bin/env python3
"""火山方舟文档合集同步脚本 (self-contained)。

从火山方舟文档中心的内部接口 `getDocDetail` 拉取每个页面自带的
「复制 Markdown」原文 (MDContent 字段)，做统一的链接归一化后拼接为一个
Markdown 合集文件，并用 Prettier 统一格式（表格对齐 / 锚点空行 / CJK 强调间距）。

用法::

    # 用内置默认配置更新蔚来 MA 迁移 Demo 的方舟文档合集
    python3 update_docs.py

    # 自定义 ID 区间与输出路径
    python3 update_docs.py --start 2553713 --end 2553730 \\
        --out ../docs/火山方舟_ManagedAgents_docs.md

    # 只拉取、打印诊断，不写文件
    python3 update_docs.py --dry-run

设计目标（对齐用户偏好）：
  * 高度自包含：只依赖 Python 标准库；格式化用 `npx prettier`（可用时），
    不可用时自动降级为「不格式化」并给出提示，不会硬失败。
  * 路径自适配：默认输出路径基于脚本自身位置解析，换机器也能开箱即用。
  * 幂等：重复运行只反映线上真实内容变化，不产生格式抖动。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# --- 默认配置 ---------------------------------------------------------------

API_TMPL = "https://docs.volcengine.com/api/doc/getDocDetail?DocumentID={doc_id}&lang=zh"
# 文档中心的卡片库 ID（用于拼「来源」链接 https://.../docs/{LIBRARY_ID}/{doc_id}）。
LIBRARY_ID = 82379
DEFAULT_START = 2553713
DEFAULT_END = 2553731  # 闭区间

# 脚本相对 common/ 目录的默认输出（common/skills/volc-docs-sync/ -> common/ -> docs/...）。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
DEFAULT_OUT = os.path.join(_REPO_ROOT, "docs", "火山方舟_ManagedAgents_docs.md")

TITLE = "# 火山方舟 · Managed Agents 文档合集"

USER_AGENT = "Mozilla/5.0 (compatible; volc-docs-sync/1.0)"


# --- 数据抓取 ---------------------------------------------------------------


def fetch_doc(doc_id: int, retries: int = 3, timeout: int = 30) -> dict:
    """拉取单个文档的 getDocDetail Result，失败自动重试。"""
    url = API_TMPL.format(doc_id=doc_id)
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            result = payload.get("Result")
            meta = payload.get("ResponseMetadata", {})
            if not result or "MDContent" not in result:
                err = meta.get("Error") or payload
                raise RuntimeError(f"接口未返回 MDContent: {err}")
            return result
        except (urllib.error.URLError, RuntimeError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(1.0 * attempt)
    raise RuntimeError(f"拉取 {doc_id} 失败（已重试 {retries} 次）: {last_err}")


def peek_doc(doc_id: int, timeout: int = 10) -> dict | None:
    """探测用的轻量请求：无效 ID / 他库文档 直接返回 None，不抛错。"""
    url = API_TMPL.format(doc_id=doc_id)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None
    result = payload.get("Result")
    if not result or not result.get("Title"):
        return None
    return result


# --- 内容归一化 -------------------------------------------------------------


def normalize_body(md: str) -> str:
    """对正文做与既有合集一致的链接归一化。

    既有合集里正文中的文档链接统一用 www.volcengine.com（对外规范域名），
    只有「来源」行保留 docs.volcengine.com。这里把正文里的
    docs.volcengine.com/docs 全部换成 www.volcengine.com/docs。
    """
    body = md.replace("docs.volcengine.com/docs", "www.volcengine.com/docs")
    return body.strip()


def source_line(doc_id: int) -> str:
    url = f"https://docs.volcengine.com/docs/{LIBRARY_ID}/{doc_id}?lang=zh"
    return f"> 来源：[{url}]({url})"


def build_section(doc_id: int, title: str, md: str) -> str:
    """拼接单页 section：锚点 + 分隔线 + 标题 + 来源 + 正文。"""
    parts = [
        f'<a id="doc-{doc_id}"></a>',
        "",
        "---",
        "",
        f"## {title}",
        "",
        source_line(doc_id),
        "",
        normalize_body(md),
    ]
    return "\n".join(parts)


def build_header(docs: list[tuple[int, str]], start: int, end: int) -> str:
    toc_lines = [
        f"- [{title}](#doc-{doc_id}) · `{doc_id}`" for doc_id, title in docs
    ]
    return "\n".join(
        [
            TITLE,
            "",
            f"> 来源：`docs.volcengine.com/docs/{LIBRARY_ID}/{{{start}..{end}}}?lang=zh`",
            "> 由页面自带的「复制 Markdown」原文拼接而成，未做二次改写。",
            "",
            "## 目录",
            "",
            *toc_lines,
        ]
    )


def assemble(docs: list[tuple[int, str, str]], start: int, end: int) -> str:
    """docs: list of (doc_id, title, md). 返回整篇未格式化的 Markdown。"""
    header = build_header([(d, t) for d, t, _ in docs], start, end)
    sections = [build_section(d, t, md) for d, t, md in docs]
    return header + "\n\n" + "\n\n".join(sections) + "\n"


# --- 格式化 -----------------------------------------------------------------


def prettier_format(text: str) -> tuple[str, bool]:
    """用 Prettier 统一格式化。返回 (结果文本, 是否成功格式化)。

    Prettier 不可用时降级为原样返回，并返回 False 让调用方给出提示。
    """
    npx = shutil.which("npx")
    if not npx:
        return text, False
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [
                npx,
                "--yes",
                "prettier@3",
                "--parser",
                "markdown",
                "--prose-wrap",
                "preserve",
                tmp_path,
            ],
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
    """统计易被格式化破坏的关键结构标记数量。"""
    return {
        "代码围栏 ```": text.count("```"),
        "<Tabs>": text.count("<Tabs>"),
        "<TabTitle>": text.count("<TabTitle>"),
    }


def check_integrity(before: str, after: str) -> list[str]:
    """比较格式化前后的关键标记数量，返回告警信息列表（为空表示无损）。

    Prettier 处理 <Tabs> 等自定义 HTML 块时，历史上出现过吞掉内部代码围栏、
    导致多语言 SDK 示例整段丢失的情况。这里做一次数量核对，任何减少都报警。
    """
    warnings: list[str] = []
    b, a = _count_markers(before), _count_markers(after)
    for name in b:
        if a[name] < b[name]:
            warnings.append(
                f"格式化后「{name}」数量减少：{b[name]} -> {a[name]}"
                "（疑似代码块/Tab 内容被吞，请检查）"
            )
    return warnings


# --- 主流程 -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="同步火山方舟 Managed Agents 文档合集"
    )
    parser.add_argument("--start", type=int, default=DEFAULT_START, help="起始 DocumentID（闭区间）")
    parser.add_argument("--end", type=int, default=DEFAULT_END, help="结束 DocumentID（闭区间）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出 Markdown 文件路径")
    parser.add_argument("--no-format", action="store_true", help="跳过 Prettier 格式化")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印诊断，不写文件")
    parser.add_argument("--sleep", type=float, default=0.2, help="每次请求之间的间隔秒数")
    parser.add_argument(
        "--probe-radius",
        type=int,
        default=5,
        help="在 [start-N, end+N] 两侧探测本库未纳入的页面；0 表示跳过",
    )
    args = parser.parse_args(argv)

    if args.end < args.start:
        parser.error("--end 不能小于 --start")

    ids = list(range(args.start, args.end + 1))
    print(f"拉取 {len(ids)} 个页面：{args.start}..{args.end}")

    docs: list[tuple[int, str, str]] = []
    updated_times: list[tuple[int, str, str]] = []  # (doc_id, title, UpdatedTime)
    parent_codes: set[str] = set()  # 用于边界探测时判定"同库同父节点"
    for i, doc_id in enumerate(ids):
        result = fetch_doc(doc_id)
        title = result["Title"]
        md = result.get("MDContent") or ""
        updated = result.get("UpdatedTime", "?")
        parent_code = result.get("ParentCode")
        if parent_code:
            parent_codes.add(parent_code)
        print(f"  {doc_id}  {title:<18}  md_len={len(md):<6} updated={updated}")
        docs.append((doc_id, title, md))
        updated_times.append((doc_id, title, updated))
        if args.sleep and i < len(ids) - 1:
            time.sleep(args.sleep)

    text = assemble(docs, args.start, args.end)

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
        _print_atlas_hint(args.out, updated_times)
        _probe_boundary(args.start, args.end, args.probe_radius, parent_codes, args.sleep)
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"\n已写入：{args.out}（{len(text)} 字符）")

    _print_atlas_hint(args.out, updated_times)
    _probe_boundary(args.start, args.end, args.probe_radius, parent_codes, args.sleep)
    return 0


# --- 边界探测 ---------------------------------------------------------------


def _probe_boundary(
    start: int,
    end: int,
    radius: int,
    known_parent_codes: set[str],
    sleep: float,
) -> None:
    """在 [start-radius, start-1] 和 [end+1, end+radius] 两侧探测本库未纳入的页面。

    DocumentID 是火山引擎全局递增、跨产品共享的，不保证连续。相邻 ID 很可能是其
    它库（如 cr/Cloudphone）。判定"属于本次合集范围"的特征是 LibraryCode == 'ark'
    且 ParentCode ∈ 已收集的父节点集合（本次 2553713..2553730 对应 Managed Agents
    的「入门」与「进阶能力」两组）。命中就打印疑似漏页，不自动改区间。
    """
    if radius <= 0:
        return
    print(f"\n[probe] 边界探测半径 ±{radius}（判定特征：LibraryCode=ark 且 ParentCode ∈ {sorted(known_parent_codes)}）")
    hits: list[tuple[int, str, str, str]] = []
    probes = list(range(start - radius, start)) + list(range(end + 1, end + radius + 1))
    for doc_id in probes:
        if doc_id <= 0:
            continue
        r = peek_doc(doc_id)
        if sleep:
            time.sleep(sleep)
        if not r:
            continue
        if r.get("LibraryCode") != "ark":
            continue
        pc = r.get("ParentCode") or ""
        title = r.get("Title") or "?"
        if pc in known_parent_codes:
            hits.append((doc_id, title, pc, r.get("UpdatedTime", "?")))
    if not hits:
        print("  未发现疑似漏页。")
        return
    print(f"  发现 {len(hits)} 个疑似漏页（同库同父节点），建议扩区间：")
    for doc_id, title, pc, updated in hits:
        print(f"    - {doc_id}  {title}  (ParentCode={pc}, updated={updated})")


# --- 下游联动提示 -----------------------------------------------------------

# atlas 文件相对 Markdown 合集所在目录的位置（docs/ 同级）。
_ATLAS_FILENAME = "managed-agents-architecture-atlas.html"


def _print_atlas_hint(
    out_path: str, updated_times: list[tuple[int, str, str]]
) -> None:
    """提醒检查下游 atlas 文档是否需要同步更新（兜底提示，不中断执行）。"""
    atlas_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), _ATLAS_FILENAME)
    print("\n[hint] 下游联动检查：", _ATLAS_FILENAME)
    if not os.path.exists(atlas_path):
        print(f"  atlas 文件未找到：{atlas_path}（如仍维护此文档，请手动确认）")
        return
    atlas_mtime = os.path.getmtime(atlas_path)
    atlas_mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(atlas_mtime))
    newer: list[tuple[int, str, str]] = []
    for doc_id, title, updated in updated_times:
        try:
            ts = time.mktime(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        except (ValueError, TypeError):
            continue
        if ts > atlas_mtime:
            newer.append((doc_id, title, updated))
    print(f"  atlas 最后修改：{atlas_mtime_str}")
    if newer:
        print(f"  有 {len(newer)} 个页面在 atlas 之后更新，建议人工审阅 atlas：")
        for doc_id, title, updated in newer:
            print(f"    - {doc_id} {title}  updated={updated}")
    else:
        print("  所有页面均不晚于 atlas，无需同步。")


if __name__ == "__main__":
    raise SystemExit(main())
