"""build_skill_bundle.py —— 冻结契约转换器：把 skill 正文转成 Claude Skills 格式的 SKILL.md 树。

冻结层定位：本脚本**只吃中间产物** `skill_bodies.json`（见 references/00-frozen-vs-client.md
的「中间产物契约」：`{key: {skill_code, document_key, result, is_error}}`），不碰原始轨迹、
不认识客户接口语义。唯一的客户启发式是"剥掉加载器返回正文的头部"（`strip_meta_header`
识别 `[skill_invoke_result]` / `skill_code=` 等前缀行）——但它在头部缺省时优雅退化（整体当正文），
所以跨客户可复用、不用改。换客户时变的是"怎么生成 skill_bodies"（客户层 extract），不是本脚本。

输入（--out-dir，即 extract 的 --out-dir）：
  - skill_bodies.json : skill 加载器返回的正文（顶层技能 + 已披露子文档）。

输出（写到 --out-dir/skills/）：
  - <skill_code>/SKILL.md            : YAML frontmatter(name+description) + 去掉加载器头部的正文
  - <skill_code>/references/<doc>.md : 该技能正文里引用到的子文档
        * 轨迹里已披露 → 写真实内容
        * 轨迹里未披露 → 留空并显式标注（补更多轨迹可补全）
  - index.json                       : skill_code -> {dir, docs[], missing[]}

自包含：只读 --out-dir 下的 JSON；不 import 客户层，也不 import 主项目。
"""
from __future__ import annotations

import argparse
import json
import re

from case_paths import add_case_args, resolve_shared_dir

# skill 加载器返回正文的头部形如：
#   [skill_invoke_result]
#   skill_code=ticket_triage
#   version=1
#   title=工单分诊
#   <空行>
#   # 正文……
_HEADER_LINE = re.compile(r"^(?:\[skill_invoke_result\]|skill_code=|version=|title=|document_key=)")
_DOC_REF = re.compile(r"[A-Za-z0-9_\-]+\.md")
_UNDISCLOSED = ("> ⚠️ 该子文档在提供的轨迹中未被披露，内容留空；"
                "补更多覆盖到它的轨迹即可补全。\n")


def strip_meta_header(body) -> tuple[dict, str]:
    """剥掉加载器头部，返回 (meta, 正文)。meta 含 title/skill_code 等。"""
    if not isinstance(body, str):
        return {}, ""
    lines = body.splitlines()
    meta: dict[str, str] = {}
    i = 0
    while i < len(lines) and _HEADER_LINE.match(lines[i]):
        if "=" in lines[i]:
            k, _, v = lines[i].partition("=")
            meta[k.strip()] = v.strip()
        i += 1
    # 跳过头部后的空行
    while i < len(lines) and not lines[i].strip():
        i += 1
    return meta, "\n".join(lines[i:]).strip()


def first_summary_line(body: str) -> str:
    """取正文里第一段可读文字（优先 blockquote 摘要，其次首个非标题段），供 description 用。"""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(">"):
            return s.lstrip("> ").strip()
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


def make_description(skill_code: str, title: str, body: str) -> str:
    summary = first_summary_line(body)
    parts = [p for p in (title, summary) if p]
    base = "；".join(parts) if parts else skill_code
    return f"{base}（依据客户 Agent 轨迹可见内容重建，用于 MA 重放对比实验）"


def yaml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def slug_name(code: str) -> str:
    """把 skill_code 规范成 MA 要求的 frontmatter name：^[a-z0-9-]{1,64}$。

    这是「MA 平台形状」的约束（CreateSkill 校验 name 只允许小写字母/数字/中横线），
    故随冻结转换器一起固定：小写化、下划线/空格转中横线、去掉其余非法字符、去重中横线、限长 64。
    目录名与 index key 仍用原始 code（内部引用不受此约束），只有落盘 frontmatter 用本函数。
    """
    s = code.strip().lower()
    s = re.sub(r"[_\s]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s or "skill")[:64]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="skill_bodies.json -> Claude Skills SKILL.md 树")
    add_case_args(ap)
    return ap.parse_args()


def main() -> None:
    a = parse_args()
    out = resolve_shared_dir(a)
    bodies = json.loads((out / "skill_bodies.json").read_text())

    # 分组：顶层技能 vs 已披露子文档
    top: dict[str, dict] = {}
    subdocs: dict[str, dict[str, dict]] = {}
    for rec in bodies.values():
        code = rec.get("skill_code") or ""
        doc = rec.get("document_key")
        if doc:
            subdocs.setdefault(code, {})[doc] = rec
        else:
            top[code] = rec

    skills_root = out / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}

    for code, rec in sorted(top.items()):
        meta, body = strip_meta_header(rec.get("result", ""))
        title = meta.get("title", code)
        desc = make_description(code, title, body)

        sk_dir = skills_root / code
        (sk_dir / "references").mkdir(parents=True, exist_ok=True)
        fm = (f"---\nname: {slug_name(code)}\n"
              f"description: \"{yaml_escape(desc)}\"\n---\n\n")
        (sk_dir / "SKILL.md").write_text(fm + body + "\n")

        # 正文引用到的子文档（去掉自引用）
        refs = sorted(r for r in set(_DOC_REF.findall(body)))
        captured = subdocs.get(code, {})
        docs_written, missing = [], []
        for ref in refs:
            dst = sk_dir / "references" / ref
            if ref in captured:
                _, sub_body = strip_meta_header(captured[ref].get("result", ""))
                dst.write_text(sub_body + "\n")
                docs_written.append(ref)
            else:
                dst.write_text(f"# {ref}\n\n{_UNDISCLOSED}")
                missing.append(ref)
        # 已披露但正文没引用到的子文档，也落盘（避免丢信息）
        for doc, srec in captured.items():
            if doc not in refs:
                _, sub_body = strip_meta_header(srec.get("result", ""))
                (sk_dir / "references" / doc).write_text(sub_body + "\n")
                docs_written.append(doc)

        index[code] = {
            "dir": str(sk_dir.relative_to(out)),
            "title": title,
            "docs": sorted(set(docs_written)),
            "missing": missing,
        }

    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2))

    total_missing = sum(len(v["missing"]) for v in index.values())
    print(f"OK 生成 {len(index)} 个 skill 到 {skills_root}")
    for code, v in sorted(index.items()):
        flag = f"  ⚠️缺 {len(v['missing'])} 子文档: {v['missing']}" if v["missing"] else ""
        print(f"  - {code}: {len(v['docs'])} 子文档{flag}")
    print(f"共 {total_missing} 个未披露子文档（已留空标注）")


if __name__ == "__main__":
    main()
