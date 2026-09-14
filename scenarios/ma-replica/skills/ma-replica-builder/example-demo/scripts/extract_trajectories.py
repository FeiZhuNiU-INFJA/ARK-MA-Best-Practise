"""extract_trajectories.py —— 从客户自研 Agent 的若干条运行轨迹抽取 MA 重放对比所需素材。

输入：--traj-dir 下的全部 *.json（每个是一次运行，顶层 messages[]）。
输出（写到 --out-dir）：
  - system_prompt.txt   : system prompt 全文（取第一条轨迹作静态基准）
  - system_sections.md  : 若 system prompt 含 <<<BEGIN/END SECTION: X>>> 标记则拆段，否则整体
  - skill_bodies.json   : "skill 加载器"类工具（默认 skill_invoke）返回的正文（转 SKILL.md 用，不进 mock）
  - replay_map.json     : 业务/工具类调用（默认 api_call + current_time）的回放键值对
  - queries.json        : 每条轨迹的 user query（重跑对比的输入样本）
  - coverage.json       : 覆盖矩阵原始数据（每条轨迹触发了哪些 skill / api / object）
  - tool_calls.jsonl    : 逐步 tool_call 记录（含每步 duration，用于耗时基线）
  - summary.md          : 人读的抽取摘要

工具面识别（★ 客户特有，用 --skill-loader / --api-tool / --time-tool 覆盖）：
  * skill 加载器工具：把其返回抽到 skill_bodies（渐进式披露的技能正文）。
  * 业务网关工具：把其调用抽到 replay_map（严格回放）。
  * 取时间工具：固定时间戳，保证"距今多少天"可复现。
  * 其余工具：仍抽进 replay_map（按 name+args 哈希键），保证"所有工具调用都被 mock"。

自包含：只读轨迹 JSON + 同目录 replay_lib，不 import 主项目。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FROZEN = HERE.parents[1] / "scripts"          # ma-replica-builder/scripts（冻结层）
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FROZEN))
from case_paths import CasePaths  # noqa: E402  (冻结层：case 目录布局)
from replay_lib import api_call_key  # noqa: E402


def skill_key(args: dict, doc_field: str) -> str:
    parts = [args.get("skill_code") or args.get("code") or ""]
    if args.get(doc_field):
        parts.append(args[doc_field])
    return "|".join(parts)


def other_tool_key(name: str, args: dict) -> str:
    return name + "|" + hashlib.sha1(
        json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="从轨迹抽取 MA 重放对比素材")
    # case 布局（推荐）：轨迹从 <case>/trajectories 读，产物写 <case>/shared。
    ap.add_argument("--case-dir", help="case 工作目录（项目根 ma-cases/<case>）；轨迹取 trajectories/、产物落 shared/")
    # 旧口径（向后兼容）：显式指定轨迹目录与输出目录。
    ap.add_argument("--traj-dir", help="轨迹 JSON 目录（内含若干 *.json）；不给则用 <case>/trajectories")
    ap.add_argument("--out-dir", help="输出目录；不给则用 <case>/shared")
    ap.add_argument("--glob", default="*.json", help="轨迹文件通配（默认 *.json）")
    ap.add_argument("--skill-loader", default="skill_invoke",
                    help="skill 加载器工具名（其返回转 SKILL.md，不进 mock）")
    ap.add_argument("--doc-field", default="document_key",
                    help="skill 加载器里表示子文档的参数名")
    ap.add_argument("--api-tool", default="api_call", help="业务网关工具名（严格回放）")
    ap.add_argument("--time-tool", default="current_time", help="取时间工具名（固定时间戳）")
    return ap.parse_args()


def resolve_io(a: argparse.Namespace) -> tuple[Path, Path]:
    """把 --case-dir / --traj-dir / --out-dir 解析成 (轨迹目录, 产物目录)。

    --case-dir 下默认取 <case>/trajectories 与 <case>/shared，可被显式 --traj-dir/--out-dir 覆盖。
    """
    if a.case_dir:
        cp = CasePaths(a.case_dir).ensure()
        traj_dir = Path(a.traj_dir) if a.traj_dir else cp.trajectories
        out = Path(a.out_dir) if a.out_dir else cp.shared
        return traj_dir, out
    if not (a.traj_dir and a.out_dir):
        raise SystemExit("需要 --case-dir，或同时给 --traj-dir 与 --out-dir。")
    return Path(a.traj_dir), Path(a.out_dir)


def main() -> None:
    a = parse_args()
    traj_dir, out = resolve_io(a)
    out.mkdir(parents=True, exist_ok=True)

    files = sorted(glob.glob(str(traj_dir / a.glob)))
    assert files, f"未找到轨迹文件于 {traj_dir}/{a.glob}"

    replay_map: dict[str, dict] = {}
    skill_bodies: dict[str, dict] = {}
    steps = []
    queries = []
    collisions = []
    coverage: dict[str, dict] = {}

    for f in files:
        name = Path(f).name
        data = json.loads(Path(f).read_text())
        msgs = data["messages"]
        results = {m.get("tool_call_id"): m.get("content") for m in msgs if m.get("role") == "tool"}

        user_msg = next((m for m in msgs if m.get("role") == "user"), None)
        queries.append({"trajectory": name, "query": user_msg["content"] if user_msg else None})

        cov = {"skills": set(), "skill_docs": set(), "apis": set(), "objects": set(), "other_tools": set()}

        for i, m in enumerate(msgs):
            if m.get("role") != "assistant":
                continue
            rc = m.get("reasoning_content") or ""
            dur = (m.get("_meta") or {}).get("duration")
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                tname = fn.get("name")
                try:
                    args = json.loads(fn.get("arguments"))
                except Exception:
                    args = fn.get("arguments")
                if not isinstance(args, dict):
                    args = {}
                cid = tc.get("id")
                result = results.get(cid, "")
                is_error = isinstance(result, str) and result.startswith("[tool_error]")
                rlen = len(result) if isinstance(result, str) else 0

                if tname == a.skill_loader:
                    k = skill_key(args, a.doc_field)
                    cov["skills"].add(args.get("skill_code") or args.get("code"))
                    if args.get(a.doc_field):
                        cov["skill_docs"].add(f"{args.get('skill_code') or args.get('code')}/{args[a.doc_field]}")
                    prev = skill_bodies.get(k)
                    if prev is None or (not is_error and (prev.get("is_error") or rlen > len(prev.get("result", "")))):
                        skill_bodies[k] = {
                            "skill_code": args.get("skill_code") or args.get("code"),
                            "document_key": args.get(a.doc_field),
                            "result": result, "is_error": is_error, "source": name,
                        }
                    key = "skill:" + k

                elif tname == a.api_tool:
                    key = api_call_key(args)
                    cov["apis"].add(args.get("api_code"))
                    p = args.get("params", {}) or {}
                    if "object_code" in p:
                        cov["objects"].add(p["object_code"])
                    for oc in (p.get("object_code_list") or []):
                        cov["objects"].add(oc)
                    if key in replay_map and replay_map[key]["result"] != result:
                        collisions.append({"key": key, "traj": name})
                    prev = replay_map.get(key)
                    if prev is None or (not is_error and (prev.get("is_error") or rlen > len(prev.get("result", "")))):
                        replay_map[key] = {"name": tname, "args": args, "result": result,
                                           "is_error": is_error, "source": name}

                elif tname == a.time_tool:
                    key = a.time_tool
                    if key not in replay_map:
                        replay_map[key] = {"name": tname, "args": args, "result": result,
                                           "is_error": is_error, "source": name}

                else:
                    # 其余工具也 mock 掉，保证"所有工具调用都可回放"
                    key = other_tool_key(tname, args)
                    cov["other_tools"].add(tname)
                    prev = replay_map.get(key)
                    if prev is None or (not is_error and (prev.get("is_error") or rlen > len(prev.get("result", "")))):
                        replay_map[key] = {"name": tname, "args": args, "result": result,
                                           "is_error": is_error, "source": name}

                steps.append({
                    "trajectory": name, "msg_index": i, "name": tname, "key": key,
                    "result_len": rlen, "is_error": is_error,
                    "reasoning_len": len(rc), "duration_s": dur,
                })

        coverage[name] = {kk: sorted(x for x in v if x is not None) for kk, v in cov.items()}

    # ---- 写出 ----
    (out / "replay_map.json").write_text(json.dumps(replay_map, ensure_ascii=False, indent=2))
    (out / "skill_bodies.json").write_text(json.dumps(skill_bodies, ensure_ascii=False, indent=2))
    (out / "queries.json").write_text(json.dumps(queries, ensure_ascii=False, indent=2))
    (out / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2))

    sp = json.loads(Path(files[0]).read_text())["messages"][0]["content"]
    (out / "system_prompt.txt").write_text(sp)
    (out / "system_sections.md").write_text(decompose_system(sp))

    with (out / "tool_calls.jsonl").open("w") as fh:
        for s in steps:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")

    api_keys = [k for k, v in replay_map.items() if v["name"] == a.api_tool]
    lines = [f"# 轨迹抽取摘要（{len(files)} 条）\n",
             f"- 轨迹：{[Path(f).name for f in files]}",
             f"- api 回放键：{len(api_keys)}（工具名={a.api_tool}）",
             f"- time 工具（{a.time_tool}）：{'有' if a.time_tool in replay_map else '无'}",
             f"- skill 正文/子文档：{len(skill_bodies)} 份（加载器={a.skill_loader}）",
             f"- 键冲突（同键不同返回，取最长成功版）：{len(collisions)}"]
    err_keys = [k for k, v in replay_map.items() if v.get("is_error")]
    lines.append(f"- 仍为错误返回的回放键：{len(err_keys)} {err_keys}")
    lines.append("\n## skill 正文清单\n")
    for k, v in sorted(skill_bodies.items()):
        rlen = len(v["result"]) if isinstance(v["result"], str) else 0
        flag = " ⚠️ERROR" if v["is_error"] else ""
        lines.append(f"- `{k}` -> {rlen} chars（来自 {v['source']}）{flag}")
    lines.append("\n## 覆盖矩阵（每条轨迹触发）\n")
    for tname, cov in coverage.items():
        lines.append(f"### {tname}")
        for kk in ("skills", "skill_docs", "apis", "objects", "other_tools"):
            lines.append(f"- {kk}: {cov[kk]}")
        lines.append("")
    (out / "summary.md").write_text("\n".join(lines))

    print(f"OK 合并 {len(files)} 条轨迹 -> {out}")
    print(f"  api 回放键={len(api_keys)}  skill 正文={len(skill_bodies)}  "
          f"time={'✓' if a.time_tool in replay_map else '✗'}  冲突={len(collisions)}  错误键={len(err_keys)}")


SECTION_BEGIN = re.compile(r"<<<BEGIN SECTION: ([^>]+)>>>")
SECTION_END_TMPL = "<<<END SECTION: {}>>>"


def decompose_system(sp: str) -> str:
    """若 system prompt 含 <<<BEGIN/END SECTION: X>>> 标记则按段拆解，否则整体作静态 system。"""
    begins = list(SECTION_BEGIN.finditer(sp))
    if not begins:
        return ("# system prompt 组件拆解\n\n"
                "未发现 `<<<BEGIN SECTION: X>>>` 类结构标记。\n"
                "→ 按整体处理：全文作为 MA 的静态 system prompt。\n\n"
                f"（全文 {len(sp)} 字符，见 system_prompt.txt）\n")
    out = ["# system prompt 组件拆解\n",
           f"发现 {len(begins)} 个 section 标记。各 section 归属需人工判断"
           "（静态 system / 运行期动态注入 如 DYNAMIC_USER_CONTEXT、MEMORY）。\n"]
    for m in begins:
        nm = m.group(1).strip()
        e = re.search(re.escape(SECTION_END_TMPL.format(nm)), sp)
        body = sp[m.end():e.start()].strip() if e else "(未找到匹配的 END 标记)"
        out.append(f"## SECTION: {nm}  ({len(body)} 字符)\n")
        preview = body[:300].replace("\n", " ")
        out.append(f"> {preview}{' …' if len(body) > 300 else ''}\n")
    return "\n".join(out)


if __name__ == "__main__":
    main()
