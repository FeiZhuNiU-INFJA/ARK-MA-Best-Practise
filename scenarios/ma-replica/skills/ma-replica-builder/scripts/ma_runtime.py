"""ma_runtime.py —— 冻结层：MA 平台通用运行引擎（永不随客户变）。

分层原则（本 skill 的硬约束）：
  * 只依赖「MA 平台的形状」——上传 skill / 建 agent+env+session / 事件流 / 指标累计 / 清理。
  * **不含任何客户特异逻辑**：具体挂哪些 custom tool、每次调用返回什么，
    都由调用方（客户样板，如 example-demo/scripts/run.py）通过参数/回调传进来。

调用方要提供两样客户特异的东西：
  1. tools：custom tool 声明列表（type/name/description/input_schema）。
  2. resolve(name, args) -> (output_text, hit)：把一次工具调用路由到回放数据的回调。

这样「事件循环怎么跑、指标怎么采、资源怎么清」在这里冻结一次，换客户不用动。
自包含：只依赖同目录 ark_min（也是冻结层）。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from ark_min import ArkMin

# 内置工具集里，模型「绕过已注入工具、去沙箱瞎找」的抢戏信号（用于 bash 抢戏观测）。
# 这是 MA/doubao 平台侧的通用现象，不是客户特异，故留在冻结层。
BASH_SNOOP_HINTS = ("ls ", "find ", "grep ", "localhost", "127.0.0.1", "curl ",
                    "/rpc", "/sse", "plugins", "8900", "mcp")

# resolve 回调签名：给工具名 + 入参，返回 (输出文本, 是否命中录制)。
ResolveFn = Callable[[str, dict], "tuple[str, bool]"]


def _event_get(ev: dict, *keys):
    for k in keys:
        if k in ev:
            return ev[k]
    return None


def _extract_tool_args(ev: dict) -> dict:
    """从 custom_tool_use 事件里取入参。不同版本可能在 arguments / input / params 里。"""
    for k in ("arguments", "input", "params"):
        v = ev.get(k)
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                d = json.loads(v)
                if isinstance(d, dict):
                    return d
            except json.JSONDecodeError:
                pass
    return {}


def _text_of(ev: dict) -> str:
    parts = []
    for blk in ev.get("content") or []:
        if isinstance(blk, dict) and blk.get("type") == "text":
            parts.append(blk.get("text", ""))
    return "".join(parts)


async def upload_skills(ark: ArkMin, skills_root: Path, index: dict) -> list[dict]:
    """上传 <skills_root>/<code>/ 下每个 skill 目录，返回 agent 配置用的 skill 引用列表。

    index：{code: {title, ...}}（可空，用目录名兜底）。纯 MA 平台逻辑。
    """
    refs: list[dict] = []
    codes = sorted(index.keys()) if index else sorted(
        p.name for p in skills_root.iterdir() if p.is_dir()) if skills_root.exists() else []
    for code in codes:
        sk_dir = skills_root / code
        if not (sk_dir / "SKILL.md").exists():
            continue
        title = (index.get(code) or {}).get("title") or code
        rec = await ark.upload_skill(sk_dir, display_title=title)
        refs.append({"type": "custom", "skill_id": rec["id"]})
        print(f"  上传 skill {code} -> {rec['id']}")
    return refs


def build_agent_config(name: str, model: str, system: str,
                       custom_tools: list[dict], skill_refs: list[dict],
                       with_agent_toolset: bool = True) -> dict:
    """组装 create_agent 配置。custom_tools 由客户侧提供；agent_toolset 是否挂由参数控制。

    默认挂 agent_toolset_20260701（含 bash），用于观测 bash 抢戏——这是 MA 平台侧通用实验意图。
    """
    tools: list[dict] = []
    if with_agent_toolset:
        tools.append({"type": "agent_toolset_20260701"})
    tools += custom_tools
    cfg = {"name": name, "model": {"id": model}, "system": system, "tools": tools}
    if skill_refs:
        cfg["skills"] = skill_refs
    return cfg


async def run_query(ark: ArkMin, session_id: str, query: str, resolve: ResolveFn,
                    events_path: Optional[Path] = None) -> dict:
    """发一条 user query，跑完整事件循环，返回该条 query 的指标。

    事件路由（全部是 MA 平台事件，客户无关）：
      - agent.custom_tool_use → 调 resolve(name,args) 取结果 → user.custom_tool_result
      - agent.tool_use（内置）→ 记录（判断 bash 抢戏）
      - span.model_request_end → 累计 model_usage
      - agent.message → 收集最终文本
      - session.status_idle/terminated/error → 收尾

    events_path 非空时，把**原始事件流**逐条落盘为 JSONL（每行一个未加工的 MA 事件）。
    这是可回放、可做内容级 diff 的"MA 侧新轨迹"原料——现有精简指标 rep<i>.json 不受影响。
    采用边收边写：不在内存里囤全量事件，进程中断也能保留已发生的部分。
    """
    t0 = time.time()
    usages: list[dict] = []
    builtin_calls: list[dict] = []
    custom_calls: list[dict] = []
    misses: list[str] = []
    agent_msgs: list[str] = []
    stop_reason = None
    session_errored = False

    await ark.send_user_message(session_id, query)

    events_fh = events_path.open("w") if events_path else None
    try:
        async for ev in ark.stream_events(session_id):
            if events_fh is not None:      # 原始事件流：未加工原样落盘，作 MA 侧新轨迹的可回放原料
                events_fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
            et = ev.get("type", "")
            if et == "agent.custom_tool_use":
                name = _event_get(ev, "name", "tool_name") or ""
                tool_use_id = _event_get(ev, "tool_use_id", "id") or ""
                args = _extract_tool_args(ev)
                output, hit = resolve(name, args)
                custom_calls.append({"name": name, "hit": hit})
                if not hit:
                    misses.append(f"{name}:{args}")
                await ark.send_custom_tool_result(session_id, tool_use_id, output, is_error=not hit)
            elif et == "agent.tool_use":
                name = _event_get(ev, "name", "tool_name") or ""
                args = _extract_tool_args(ev)
                cmd = ""
                if isinstance(args, dict):
                    cmd = str(args.get("command") or args.get("cmd") or args.get("script") or "")
                snoop = name == "bash" and any(
                    h in json.dumps(ev, ensure_ascii=False).lower() for h in BASH_SNOOP_HINTS)
                rec = {"name": name, "snoop": snoop}
                if cmd:                       # 抓命令原文，作为 bash 抢戏的可核查证据
                    rec["command"] = cmd
                builtin_calls.append(rec)
            elif et == "span.model_request_end":
                mu = ev.get("model_usage")
                if isinstance(mu, dict):
                    usages.append(mu)
            elif et == "agent.message":
                agent_msgs.append(_text_of(ev))
            elif et in ("session.status_idle", "session.status_terminated"):
                sr = ev.get("stop_reason") or {}
                sr_type = sr.get("type") if isinstance(sr, dict) else sr
                # requires_action 的 idle 是"在等客户端回传 custom_tool_result"，不是收尾——
                # 回传后 session 会切回 running 继续跑，这里必须继续等事件，不能 break。
                if et == "session.status_idle" and sr_type == "requires_action":
                    continue
                stop_reason = sr_type or sr
                break
            elif et == "session.error":
                session_errored = True
                agent_msgs.append(f"[session.error] {json.dumps(ev, ensure_ascii=False)[:300]}")
    finally:
        if events_fh is not None:
            events_fh.close()

    def _sum(field: str) -> int:
        return sum(int(u.get(field, 0) or 0) for u in usages)

    # ok=本条 query 是否算「成功」：出过 session.error、stop_reason 为 error、
    # 或压根没产生任何模型请求（多半是启动即挂）都判失败——失败条不进耗时/token 统计。
    ok = (not session_errored
          and str(stop_reason or "").lower() not in ("error", "failed")
          and len(usages) > 0)

    return {
        "query": query,
        "ok": ok,
        "wall_clock_s": round(time.time() - t0, 3),
        "stop_reason": stop_reason,
        "model_requests": len(usages),
        "usage_total": {
            "input_tokens": _sum("input_tokens"),
            "output_tokens": _sum("output_tokens"),
            "cache_creation_input_tokens": _sum("cache_creation_input_tokens"),
            "cache_read_input_tokens": _sum("cache_read_input_tokens"),
        },
        "usage_per_request": usages,
        "custom_tool_calls": custom_calls,
        "custom_tool_misses": misses,
        "builtin_tool_calls": builtin_calls,
        "bash_snoop_detected": any(c["snoop"] for c in builtin_calls),
        "final_message": ("\n".join(m for m in agent_msgs if m))[-4000:],
    }


def _mean(xs: list[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 3) if xs else None


def aggregate_repeats(trajectory: str, reps: list[dict]) -> dict:
    """把某条轨迹的 N 次重复结果聚成一条汇总。

    关键口径（按用户要求）：**失败的 rep 不进耗时/token 统计**，但要统计失败比例作参考。
    - agg_ok：只对 ok=True 的 rep 求均值（耗时/模型请求数/四件套 token）。
    - fail_ratio：失败次数 / 总次数。
    - bash_snoop_any：任一 rep 检测到即为真（抢戏是"出现过就值得报"）。
    """
    ok_reps = [r for r in reps if r.get("ok")]
    fail = len(reps) - len(ok_reps)

    def _avg_usage(field: str) -> Optional[float]:
        return _mean([float(r["usage_total"].get(field, 0) or 0) for r in ok_reps])

    agg_ok = {
        "wall_clock_s": _mean([float(r["wall_clock_s"]) for r in ok_reps]),
        "model_requests": _mean([float(r["model_requests"]) for r in ok_reps]),
        "usage_total": {
            "input_tokens": _avg_usage("input_tokens"),
            "output_tokens": _avg_usage("output_tokens"),
            "cache_creation_input_tokens": _avg_usage("cache_creation_input_tokens"),
            "cache_read_input_tokens": _avg_usage("cache_read_input_tokens"),
        },
    } if ok_reps else None

    return {
        "trajectory": trajectory,
        "repeats": len(reps),
        "ok_count": len(ok_reps),
        "fail_count": fail,
        "fail_ratio": round(fail / len(reps), 4) if reps else 0.0,
        "custom_miss_total": sum(len(r.get("custom_tool_misses", [])) for r in reps),
        "bash_snoop_any": any(r.get("bash_snoop_detected") for r in reps),
        "agg_ok": agg_ok,                 # 仅成功 rep 的均值；全失败时为 None
        # 每个 rep 留一条精简记录（完整明细在同目录 rep<i>.json）
        "rep_summaries": [
            {"rep": i + 1, "ok": r.get("ok"), "wall_clock_s": r.get("wall_clock_s"),
             "model_requests": r.get("model_requests"),
             "stop_reason": r.get("stop_reason"),
             "session_id": r.get("session_id"),
             "custom_miss": len(r.get("custom_tool_misses", [])),
             "bash_snoop": r.get("bash_snoop_detected")}
            for i, r in enumerate(reps)
        ],
    }


async def _run_one_repeat(ark: ArkMin, agent_id: str, env_id: str, query: str,
                          trajectory: Optional[str], resolve: ResolveFn,
                          rep_idx: int, keep: bool,
                          events_path: Optional[Path] = None) -> dict:
    """单次重复：单开 session → 跑事件循环 → 收尾删 session。供并发调用。

    events_path 非空时，本次重复的原始事件流落盘到该 JSONL（MA 侧新轨迹原料）。
    """
    session_id = await ark.create_session(agent_id, env_id)
    res = await run_query(ark, session_id, query, resolve, events_path=events_path)
    res["trajectory"] = trajectory
    res["session_id"] = session_id
    res["rep"] = rep_idx
    if events_path is not None:
        res["events_file"] = events_path.name
    if not keep:
        await ark.delete_session(session_id)
    return res


async def run_session(ark: ArkMin, *, agent_config: dict, queries: list[dict],
                      resolve: ResolveFn, runs_dir: Path,
                      run_all: bool = False, keep: bool = False,
                      repeats: int = 1, concurrency: Optional[int] = None,
                      networking: str = "unrestricted",
                      record_events: bool = True,
                      on_result: Optional[Callable[[dict], Awaitable[None]]] = None) -> list[dict]:
    """端到端：建 agent/env → **逐条轨迹串行**，**每条轨迹并发跑 repeats 次** → 落盘 → 清理。

    queries：[{"trajectory": <名>, "query": <文本>}, ...]。
    resolve：客户侧提供的工具结果路由回调。
    repeats：每条轨迹重复次数（默认 1）；concurrency：同一轨迹内并发上限（默认=repeats，全并发）。
    record_events：True（默认）时把每次重复的**原始事件流**落盘为
        runs_dir/<轨迹stem>/rep<i>.events.jsonl —— 可回放、可做内容级 diff 的"MA 侧新轨迹"原料。
    落盘：runs_dir/<轨迹stem>/rep<i>.json（每次重复精简指标）+ rep<i>.events.jsonl（原始事件流，可关）
    + runs_dir/<轨迹stem>/run.json（聚合，失败 rep 不计入耗时/token 均值，但统计 fail_ratio）。返回每条轨迹的聚合列表。
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency or repeats or 1)
    suffix = str(int(time.time()))
    agent_id = await ark.create_agent(agent_config)
    print(f"  agent={agent_id}  model={agent_config.get('model', {}).get('id')}  "
          f"repeats={repeats}  concurrency={concurrency or repeats}")

    env_id = await ark.create_environment(f"ma-replica-env-{suffix}", networking)
    aggregates: list[dict] = []
    try:
        todo = queries if run_all else queries[:1]
        for qi, q in enumerate(todo):
            query = q.get("query")
            if not query:
                continue
            trajectory = q.get("trajectory")
            stem = Path(trajectory or f"q{qi}").stem
            run_out = runs_dir / stem
            run_out.mkdir(parents=True, exist_ok=True)
            print(f"  轨迹 {trajectory} → 并发 {repeats} 次")

            async def _guarded(rep_idx: int) -> dict:
                async with sem:            # 限制同一轨迹内的并发度
                    ev_path = run_out / f"rep{rep_idx}.events.jsonl" if record_events else None
                    return await _run_one_repeat(
                        ark, agent_id, env_id, query, trajectory, resolve, rep_idx, keep,
                        events_path=ev_path)

            reps = await asyncio.gather(*[_guarded(i + 1) for i in range(max(1, repeats))])
            reps = list(reps)

            # 每次重复的完整明细各落一个文件
            for r in reps:
                (run_out / f"rep{r['rep']}.json").write_text(
                    json.dumps(r, ensure_ascii=False, indent=2))
                flag = "" if r.get("ok") else "  [FAIL]"
                print(f"    rep{r['rep']}: {r['wall_clock_s']}s  reqs={r['model_requests']}  "
                      f"in/out/cache_r={r['usage_total']['input_tokens']}/"
                      f"{r['usage_total']['output_tokens']}/"
                      f"{r['usage_total']['cache_read_input_tokens']}  "
                      f"miss={len(r['custom_tool_misses'])}  snoop={r['bash_snoop_detected']}{flag}")

            agg = aggregate_repeats(trajectory, reps)
            (run_out / "run.json").write_text(json.dumps(agg, ensure_ascii=False, indent=2))
            aggregates.append(agg)
            print(f"    汇总: ok={agg['ok_count']}/{agg['repeats']} "
                  f"失败率={agg['fail_ratio']:.0%} "
                  f"均耗时={agg['agg_ok']['wall_clock_s'] if agg['agg_ok'] else 'N/A'}s "
                  f"bash抢戏={'是' if agg['bash_snoop_any'] else '否'}")
            if on_result:
                await on_result(agg)
    finally:
        if not keep:
            await ark.delete_environment(env_id)
            await ark.delete_agent(agent_id)
    return aggregates
