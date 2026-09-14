"""run.py —— 客户样板（本例：Acme 客服平台）：在 MA 上重跑轨迹 + 出对比报告。

这是「客户特异」的一层，示范怎么把冻结引擎（../../scripts/ma_runtime.py、report.py）
用起来。换客户时**复制本文件改这里**，冻结层不动：
  * build_custom_tools() —— Acme 的 api_call / current_time 工具声明（描述、input_schema 都是客户特有）。
  * make_resolve()       —— 把一次工具调用路由到录制数据（严格回放），客户接口语义在此。
  * make_self_side()     —— 从 Acme 轨迹结构（messages[]._meta.duration）数自研侧耗时/步数。

两种 mock 模式（对应 SKILL.md 的两套方案）：
  * --mock files（默认）：不注册 custom tool，把 mocks-skill 一起上传、附录拼进 system，模型 read 读文件。
  * --mock custom       ：注册 custom tool，客户端事件循环严格回放（保真度高，性能对比首选）。
  * --mock both         ：files 与 custom **串行各跑一遍**，各出一份报告（最终交付建议）。

重复与并发（最终对比口径）：
  * --repeats N     ：每条轨迹**并发**重复 N 次（最终对比建议 5）；轨迹之间**串行**。
  * --concurrency C ：同一轨迹内并发上限（默认=repeats，全并发）。
  * 统计时**失败的重复不计入耗时/token 均值**，但单独统计失败率作参考。

工作目录（case 布局，推荐）：--case-dir 指向 <项目>/ma-cases/<case>，脚本自动认：
  * shared/       —— 读 extract/build 的抽取产物（replay_map/system_prompt/skills…）
  * <mode>-mode/<轨迹stem>/rep<i>.json + run.json —— 每条轨迹每次重复明细 + 聚合（files/custom 分开）
  * reports/comparison-<mode>.md    —— 每模式一份对比报告
兼容旧口径：也可直接 --out-dir <dir>（产物平铺在该目录，report 落 <dir>/comparison-<mode>.md）。

用法（cwd = skill 根 scenarios/ma-replica/skills/ma-replica-builder/）：
    export ARK_API_KEY=...
    python example-demo/scripts/run.py --case-dir ../../ma-cases/demo                 # 默认 files 模式，跑第一条
    python example-demo/scripts/run.py --case-dir ../../ma-cases/demo --mock custom --all
    python example-demo/scripts/run.py --case-dir ../../ma-cases/demo --mock both --all --repeats 5   # 最终交付口径
    python example-demo/scripts/run.py --out-dir ../../data                          # 旧口径（平铺目录）
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FROZEN = HERE.parents[1] / "scripts"          # ma-replica-builder/scripts（冻结层）
sys.path.insert(0, str(HERE))                 # 本地 replay_lib
sys.path.insert(0, str(FROZEN))               # 冻结 ark_min / ma_runtime / report
from ark_min import ArkMin, ArkMinError       # noqa: E402  (冻结层)
from case_paths import CasePaths              # noqa: E402  (冻结层：case 目录布局)
from equivalence import (build_equivalence,   # noqa: E402  (冻结层：等效性评估)
                         collect_product_pairs)
from ma_runtime import (build_agent_config, run_session,      # noqa: E402
                        upload_skills)
from report import build_report               # noqa: E402  (冻结层)
from replay_lib import api_call_key, strict_lookup  # noqa: E402  (Acme 特有键逻辑)

DEFAULT_MODEL = "doubao-seed-evolving"


def build_custom_tools(replay_map: dict, api_tool: str, time_tool: str) -> list[dict]:
    """★客户特有：按录制到的工具名声明 custom tool。描述/schema 是 Acme 工单系统网关的形态。"""
    tools: list[dict] = []
    names = {rec.get("name") for rec in replay_map.values()}
    if api_tool in names:
        tools.append({
            "type": "custom", "name": api_tool,
            "description": ("调用客户内部业务系统的统一网关。入参 api_code 指定接口、params 指定查询条件。"
                            "本次为离线严格回放：仅返回录制轨迹中出现过的调用结果。"),
            "input_schema": {
                "type": "object",
                "properties": {
                    "api_code": {"type": "string", "description": "接口编码"},
                    "params": {"type": "object", "description": "接口参数"},
                },
                "required": ["api_code"],
            },
        })
    if time_tool in names:
        tools.append({
            "type": "custom", "name": time_tool,
            "description": "获取当前时间（离线回放下返回录制时的固定时间戳，保证相对时间可复现）。",
            "input_schema": {"type": "object", "properties": {}},
        })
    for nm in sorted(n for n in names if n not in {api_tool, time_tool} and n):
        tools.append({"type": "custom", "name": nm,
                      "description": f"客户自研工具 {nm}（离线严格回放）。",
                      "input_schema": {"type": "object"}})
    return tools


def make_resolve(replay_map: dict, api_tool: str, time_tool: str):
    """★客户特有：返回 resolve(name, args) -> (输出文本, 是否命中)，喂给冻结引擎。"""
    def resolve(name: str, args: dict):
        if name == api_tool:
            return strict_lookup(replay_map, args.get("api_code", ""), args.get("params", {}) or {})
        if name == time_tool:
            rec = replay_map.get(time_tool)
            if rec is not None:
                return rec["result"], True
            return "[tool_error] 未录制 current_time", False
        key = name + "|" + hashlib.sha1(
            json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
        rec = replay_map.get(key)
        if rec is not None:
            return rec["result"], True
        return "[tool_error] " + json.dumps(
            {"code": "REPLAY_MISS", "message": f"未录制的调用: {name}"}, ensure_ascii=False), False
    return resolve


def make_self_side(traj_dir: Path):
    """★客户特有：Acme 轨迹结构 —— messages[]._meta.duration 求和 + assistant 步数。"""
    def self_side(traj_name: str):
        p = traj_dir / traj_name
        if not p.exists():
            return None, None
        msgs = json.loads(p.read_text()).get("messages", [])
        total = 0.0
        steps = 0
        for m in msgs:
            dur = (m.get("_meta") or {}).get("duration")
            if isinstance(dur, (int, float)):
                total += dur
            if m.get("role") == "assistant":
                steps += 1
        return round(total, 3), steps
    return self_side


# ── 等效性抽取（★客户特有：业务调用键规则 + 最终产物在轨迹/事件里长什么样）──
# 只把「业务网关调用」（api_call）纳入对齐——skill 的加载方式两侧不同（原轨迹用 skill_invoke，
# MA 用文件 read），不可直接比，故等效性只核对可比的业务接口调用。键复用 replay_lib.api_call_key，
# 保证与"回放命中"完全同一口径。

def _extract_final_text(texts: list[str]) -> str:
    """从若干 assistant 文本里取"最终交付物"：优先最后一段像盘点卡片的（含 markdown 标题），
    否则退回最后一段非空文本。"""
    for t in reversed(texts):
        if t and ("\n#" in t or t.lstrip().startswith("#") or "##" in t):
            return t
    return next((t for t in reversed(texts) if t and t.strip()), "")


def make_orig_calls(api_tool: str):
    """原轨迹（OpenAI messages 格式）→ 业务调用序列。只收 api_tool 的调用，键同回放口径。"""
    def orig_calls(traj_path: Path) -> list[dict]:
        msgs = json.loads(traj_path.read_text()).get("messages", [])
        calls = []
        for m in msgs:
            if m.get("role") != "assistant":
                continue
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function", {}) or {}
                if fn.get("name") != api_tool:
                    continue
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                key = api_call_key(args)
                calls.append({"key": key, "name": api_tool, "label": key})
        return calls
    return orig_calls


def make_ma_calls(api_tool: str):
    """MA rep 事件流 → 业务调用序列。custom 模式下业务调用是 agent.custom_tool_use（name=api_tool）。

    files 模式没注册 custom tool，此处返回空（等效性模块据此判为"不可测"）。
    """
    def ma_calls(events_path: Path) -> list[dict]:
        calls = []
        with events_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "agent.custom_tool_use" and ev.get("name") == api_tool:
                    inp = ev.get("input") or {}
                    key = api_call_key(inp)
                    calls.append({"key": key, "name": api_tool, "label": key})
        return calls
    return ma_calls


def orig_product(traj_path: Path) -> str:
    """原轨迹最终产物：最后一段 assistant 文本（content 可能是 str 或 block 列表）。"""
    msgs = json.loads(traj_path.read_text()).get("messages", [])
    texts = []
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, str):
            texts.append(c)
        elif isinstance(c, list):
            texts.append("".join(b.get("text", "") for b in c
                                 if isinstance(b, dict) and b.get("type") == "text"))
    return _extract_final_text(texts)


def ma_product(events_path: Path) -> str:
    """MA rep 最终产物：事件流里 agent.message 文本，取最终交付段。"""
    texts = []
    with events_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "agent.message":
                texts.append("".join(b.get("text", "") for b in (ev.get("content") or [])
                                     if isinstance(b, dict) and b.get("type") == "text"))
    return _extract_final_text(texts)


_JUDGE_SYS = (
    "你是资深评测员。给你两份「销售用户盘点卡片」：A=客户原系统交付、B=在方舟MA上重放交付。"
    "判断 B 与 A 在**业务结论**上是否等效——关注：用户画像/意向等级、关键卡点与动阻力、"
    "跟进诊断、下一步策略与话术方向是否一致；忽略排版差异与措辞。"
    "只输出一个 JSON：{\"score\": 0-100 整数, \"verdict\": \"等效|基本等效|部分偏差|明显不等效\", "
    "\"notes\": \"一句话关键差异\"}。score=100 表示业务结论完全一致。"
)


async def judge_products(ark: ArkMin, model: str, pairs: list[dict]) -> dict:
    """对每条轨迹的 (原产物, MA产物) 做 LLM 语义判分，返回 {traj: {score, verdict, notes}}。

    judge 失败（网络/解析）不阻塞报告：该轨迹标注失败原因、score=None。
    """
    out: dict[str, dict] = {}
    for pr in pairs:
        user = (f"A（客户原交付）：\n{pr['orig_text'][:3500]}\n\n"
                f"B（MA重放交付）：\n{pr['ma_text'][:3500]}\n\n只输出 JSON。")
        try:
            raw = await ark.chat(model, [
                {"role": "system", "content": _JUDGE_SYS},
                {"role": "user", "content": user},
            ], temperature=0.0, max_tokens=400)
            verdict = _parse_judge(raw)
        except (ArkMinError, ValueError) as e:
            verdict = {"score": None, "verdict": "判分失败", "notes": str(e)[:120]}
        out[pr["traj"]] = verdict
    return out


def _parse_judge(raw: str) -> dict:
    """从模型回复里抠出 JSON 判分（容忍 ```json 包裹与前后闲话）。"""
    s = raw.strip()
    if "```" in s:
        s = s.split("```")[1]
        if s.startswith("json"):
            s = s[4:]
    l, r = s.find("{"), s.rfind("}")
    if l >= 0 and r > l:
        s = s[l:r + 1]
    d = json.loads(s)
    return {"score": d.get("score"), "verdict": d.get("verdict", ""), "notes": d.get("notes", "")}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Acme 客户样板：MA 重跑 + 对比（冻结引擎 + 客户特有工具面）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--case-dir", help="case 工作目录（项目根 ma-cases/<case>）；读 shared/、写 <mode>-mode/、reports/")
    g.add_argument("--out-dir", help="旧口径：抽取产物平铺目录（report 落其 comparison.md）")
    ap.add_argument("--traj-dir", default=None,
                    help="原轨迹目录（对比自研侧耗时用）；--case-dir 下默认取 <case>/trajectories")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--mock", choices=["files", "custom", "both"], default="files",
                    help="mock 模式：files=静态文件(默认)；custom=custom tool 动态回放；both=两个都跑(串行)")
    ap.add_argument("--api-tool", default="api_call")
    ap.add_argument("--time-tool", default="current_time")
    ap.add_argument("--all", action="store_true", help="跑全部 query（默认只跑第一条）")
    ap.add_argument("--repeats", type=int, default=1,
                    help="每条轨迹并发重复次数（最终对比建议 5）")
    ap.add_argument("--concurrency", type=int, default=None,
                    help="同一轨迹内的并发上限（默认=repeats，即全并发）")
    ap.add_argument("--keep", action="store_true", help="跑完不删 agent/env/session")
    ap.add_argument("--report-only", action="store_true",
                    help="不实跑 MA，仅用已落盘的 <mode>-mode/*/rep*.events.jsonl 重出对比报告")
    ap.add_argument("--no-record-events", action="store_true",
                    help="不落盘原始事件流（默认每次重复都写 rep<i>.events.jsonl 作 MA 侧新轨迹原料）")
    ap.add_argument("--no-equivalence", action="store_true",
                    help="跳过等效性评估（默认开：业务调用对齐 + 产物结构/语义对比）")
    ap.add_argument("--no-judge", action="store_true",
                    help="等效性里跳过 LLM 语义判分，只出结构指标（省一次对话模型调用）")
    ap.add_argument("--judge-model", default=None,
                    help="产物语义判分用的对话模型 id（默认同 --model）")
    ap.add_argument("--base-url", default=os.environ.get(
        "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"))
    return ap.parse_args()


def resolve_layout(a: argparse.Namespace, mock_mode: str) -> tuple[Path, Path, Path, Path]:
    """把 --case-dir / --out-dir + 具体模式解析成 (抽取产物目录, 实跑落盘目录, 报告文件, 原轨迹目录)。

    - --case-dir：shared/ 读产物；<mode>-mode/ 落实跑；reports/comparison-<mode>.md 出报告；
      原轨迹默认 <case>/trajectories（可被 --traj-dir 覆盖）。
    - --out-dir ：旧平铺口径，产物/实跑/报告都在该目录，原轨迹回退到仓库 docs 目录。
    """
    if a.case_dir:
        cp = CasePaths(a.case_dir).ensure()
        runs_dir = cp.mode_dir(mock_mode)
        report_md = cp.reports / f"comparison-{mock_mode}.md"
        traj_dir = Path(a.traj_dir) if a.traj_dir else cp.trajectories
        return cp.shared, runs_dir, report_md, traj_dir
    out = Path(a.out_dir)
    traj_dir = Path(a.traj_dir) if a.traj_dir else (HERE.parents[3] / "context/trajectory")
    return out, out / f"ma_runs_{mock_mode}", out / f"comparison-{mock_mode}.md", traj_dir


async def run_one_mode(ark: ArkMin, a: argparse.Namespace, mock_mode: str) -> None:
    """在给定 mock 模式下：上传 skills → 建 agent → 每条轨迹并发重复 → 出报告。"""
    out, runs_dir, report_md, traj_dir = resolve_layout(a, mock_mode)

    replay_map = json.loads((out / "replay_map.json").read_text())
    system_prompt = (out / "system_prompt.txt").read_text()
    queries = json.loads((out / "queries.json").read_text())
    index = json.loads((out / "index.json").read_text()) if (out / "index.json").exists() else {}

    resolve = make_resolve(replay_map, a.api_tool, a.time_tool)
    skill_index = dict(index)
    if mock_mode == "files":
        appendix = out / "mock_system_appendix.md"
        if appendix.exists():
            system_prompt = system_prompt + "\n\n" + appendix.read_text()
        mocks_skill = out / "mocks-skill"
        if mocks_skill.exists():
            # 把 mocks-skill 临时纳入上传集：ma_runtime 按 skills/<code> 找，这里直接单独传
            rec = await ark.upload_skill(mocks_skill, display_title="offline-mock-data")
            print(f"  上传 mocks-skill -> {rec['id']}")
            extra_ref = [{"type": "custom", "skill_id": rec["id"]}]
        else:
            extra_ref = []
        custom_tools = []          # files 模式不注册 custom tool
    else:
        extra_ref = []
        custom_tools = build_custom_tools(replay_map, a.api_tool, a.time_tool)

    skill_refs = await upload_skills(ark, out / "skills", skill_index)
    skill_refs += extra_ref

    agent_cfg = build_agent_config(
        name=f"ma-replica-demo-{mock_mode}", model=a.model, system=system_prompt,
        custom_tools=custom_tools, skill_refs=skill_refs, with_agent_toolset=True)

    print(f"=== 模式 {mock_mode}：run_all={a.all} repeats={a.repeats} ===")
    await run_session(
        ark, agent_config=agent_cfg, queries=queries, resolve=resolve,
        runs_dir=runs_dir, run_all=a.all, keep=a.keep,
        repeats=a.repeats, concurrency=a.concurrency,
        record_events=not a.no_record_events)

    await emit_report(ark, a, runs_dir, report_md, traj_dir)


async def emit_report(ark: ArkMin, a: argparse.Namespace,
                      runs_dir: Path, report_md: Path, traj_dir: Path) -> None:
    """出对比报告：性能（自研侧耗时用 Acme 轨迹结构）+ 等效性（业务调用对齐 + 产物对比）。

    等效性默认开；需要事件流（rep*.events.jsonl）才能核对 MA 侧业务调用/产物——
    故建议实跑时不加 --no-record-events。judge 走对话模型（--judge-model，默认同 --model）。
    """
    report_md.parent.mkdir(parents=True, exist_ok=True)
    equiv = None
    if not a.no_equivalence:
        judgments: dict = {}
        if not a.no_judge:
            pairs = collect_product_pairs(
                trajectories_dir=traj_dir, runs_dir=runs_dir,
                orig_product=orig_product, ma_product=ma_product)
            if pairs:
                print(f"  等效性：LLM 语义判分 {len(pairs)} 条产物…")
                judgments = await judge_products(ark, a.judge_model or a.model, pairs)
        judge_fn = (lambda traj, o, m: judgments.get(traj, {})) if judgments else None
        equiv = build_equivalence(
            trajectories_dir=traj_dir, runs_dir=runs_dir,
            orig_calls=make_orig_calls(a.api_tool), ma_calls=make_ma_calls(a.api_tool),
            orig_product=orig_product, ma_product=ma_product, judge=judge_fn)
    build_report(runs_dir, report_md, self_side=make_self_side(traj_dir), equiv=equiv)


async def amain() -> None:
    a = parse_args()
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        raise SystemExit("缺少 ARK_API_KEY（live 实跑必需）。export ARK_API_KEY=... 后重试。")

    modes = ["files", "custom"] if a.mock == "both" else [a.mock]
    async with ArkMin(api_key, a.base_url) as ark:
        for mode in modes:                # both：两个模式串行跑（各自独立建 agent/env）
            if a.report_only:             # 只用已落盘的 rep*.events.jsonl 重出报告，不再实跑 MA
                _, runs_dir, report_md, traj_dir = resolve_layout(a, mode)
                print(f"=== 模式 {mode}：仅重出报告（report-only）===")
                await emit_report(ark, a, runs_dir, report_md, traj_dir)
            else:
                await run_one_mode(ark, a, mode)


def main() -> None:
    try:
        asyncio.run(amain())
    except ArkMinError as e:
        raise SystemExit(f"[方舟错误] {e}")


if __name__ == "__main__":
    main()
