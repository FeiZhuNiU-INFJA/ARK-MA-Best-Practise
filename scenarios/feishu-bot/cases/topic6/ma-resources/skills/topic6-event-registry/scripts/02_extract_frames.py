#!/usr/bin/env python3
"""阶段 2：Event Frame 抽取。

只处理 EVENT 与 UNCERTAIN。不强制 time / location——实测只有标题时 location
填充率仅 11.6%，强制要求只会诱导模型编造。
information_score 由本脚本计算，不让模型自己打分。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import (Relay, make_batches, parse_json, read_jsonl, report,
                   require_full_coverage, run_batches, write_jsonl)

SYSTEM = """你从热点内容中抽取事件框架。

字段说明：
actor       事件主体，可多个。type 从 PERSON GROUP ORG BRAND WORK PRODUCT PLACE EVENT_ENTITY OTHER 里选
action      主体做了什么 / 发生了什么，用动词短语
object      动作作用的对象
event_type  事件类型，用简短英文大写下划线命名，如 BOX_OFFICE、CONCERT、TOURNAMENT_MATCH
parent_hint 这条内容可能属于哪个更大的事件。只是线索，不必精确，不确定时留 null
stage       从 ANNOUNCEMENT TICKETING LAUNCH ONGOING UPDATE RESPONSE AFTERMATH DISPUTE RESULT 里选
time        原文出现的时间，没有就 null。不要推断
location    原文出现的地点，没有就 null。不要推断
key_facts   原文里的可核查事实，键用简短英文，值保留原文表述

三条硬性约束：
1. 只抽原文里有的信息。time 和 location 大多数内容里没有，留 null 是正确答案，编造是严重错误。
2. parent_hint 用自然语言写"这属于哪个更大的事"，不要写成分类标签。
3. 不要给 information_score 打分，这个字段由程序计算。

只输出 JSON，不要任何解释。"""

USER_TMPL = """抽取以下 {n} 条内容的事件框架。

{lines}

输出格式：
{{"frames":[{{"record_id":"","actor":[{{"name":"","type":""}}],"action":"","object":"","event_type":"","parent_hint":null,"stage":null,"time":null,"location":null,"key_facts":{{}}}}]}}

每个 record_id 必须出现且仅出现一次。"""

ACTOR_TYPES = {"PERSON", "GROUP", "ORG", "BRAND", "WORK", "PRODUCT", "PLACE",
               "EVENT_ENTITY", "OTHER"}
STAGES = {"ANNOUNCEMENT", "TICKETING", "LAUNCH", "ONGOING", "UPDATE", "RESPONSE",
          "AFTERMATH", "DISPUTE", "RESULT"}
# \d+ 会把 1.3版本 拆成 1 和 3，必须带小数部分
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
WORK_RE = re.compile(r"[《【\"“](.+?)[》】\"”]")


def information_score(frame: dict, title: str) -> int:
    """可解释公式。只用于挑 canonical anchor，不参与合并判断。"""
    actors = frame.get("actor") or []
    key_facts = frame.get("key_facts") or {}
    numeric = any(NUM_RE.search(str(v)) for v in key_facts.values()) or bool(NUM_RE.search(title))
    return (3 * bool(actors)
            + 3 * bool(frame.get("action"))
            + 2 * bool(frame.get("object"))
            + len(actors)
            + 2 * bool(numeric)
            + 2 * bool(WORK_RE.search(title))
            + min(len(title) // 10, 3))


def normalize(raw: dict, items: list[dict]) -> list[dict]:
    by_id = {r["record_id"]: r for r in items}
    seen: set[str] = set()
    out = []
    for row in raw.get("frames", []):
        rid = str(row.get("record_id", "")).strip()
        if rid not in by_id or rid in seen:
            continue
        seen.add(rid)
        actors = []
        for actor in row.get("actor") or []:
            if isinstance(actor, str):
                actors.append({"name": actor.strip(), "type": "OTHER"})
            elif isinstance(actor, dict) and str(actor.get("name") or "").strip():
                atype = str(actor.get("type") or "").upper().strip()
                actors.append({"name": str(actor["name"]).strip(),
                               "type": atype if atype in ACTOR_TYPES else "OTHER"})
        stage = str(row.get("stage") or "").upper().strip()
        facts = row.get("key_facts")
        frame = {
            "record_id": rid,
            "actor": actors,
            "action": str(row.get("action") or "").strip(),
            "object": str(row.get("object") or "").strip(),
            "event_type": str(row.get("event_type") or "").upper().strip(),
            "parent_hint": (str(row.get("parent_hint")).strip()
                            if row.get("parent_hint") else None),
            "stage": stage if stage in STAGES else None,
            "time": (str(row.get("time")).strip() if row.get("time") else None),
            "location": (str(row.get("location")).strip() if row.get("location") else None),
            "key_facts": facts if isinstance(facts, dict) else {},
            "title": by_id[rid]["title"],
            "raw_title": by_id[rid].get("raw_title", ""),
            "heat": by_id[rid].get("heat", 0.0),
            "platform": by_id[rid].get("platform", ""),
        }
        frame["information_score"] = information_score(frame, frame["title"])
        out.append(frame)
    for rid in require_full_coverage(list(by_id), [f["record_id"] for f in out], "frames"):
        title = by_id[rid]["title"]
        out.append({"record_id": rid, "actor": [], "action": "", "object": "",
                    "event_type": "", "parent_hint": None, "stage": None, "time": None,
                    "location": None, "key_facts": {}, "title": title,
                    "raw_title": by_id[rid].get("raw_title", ""),
                    "heat": by_id[rid].get("heat", 0.0),
                    "platform": by_id[rid].get("platform", ""),
                    "information_score": min(len(title) // 10, 3), "fallback": True})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--batch-size", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=24000)
    ap.add_argument("--include-uncertain", action="store_true", default=True)
    args = ap.parse_args()

    src = args.run_dir / "work" / "eventness.jsonl"
    if not src.exists():
        raise SystemExit(f"缺少 {src}，先跑 01_eventness.py")
    keep = {"EVENT", "UNCERTAIN"} if args.include_uncertain else {"EVENT"}
    records = []
    for r in read_jsonl(src):
        if r.get("eventness") not in keep:
            continue
        # 抽取用清洗后的标题；原标题保留下来供人工核对
        records.append({"record_id": r["record_id"],
                        "title": r.get("clean_title") or r["title"],
                        "raw_title": r["title"],
                        "heat": r.get("heat", 0.0),
                        "platform": r.get("platform", "")})
    # 按热度降序：让信息量最大的记录先建立事件定义
    records.sort(key=lambda r: -float(r.get("heat") or 0))
    print(f"待抽取 {len(records)} 条（EVENT + UNCERTAIN）")

    relay = Relay(args.model, args.max_tokens, SYSTEM)
    batches = make_batches(records, args.batch_size, tag="FR")

    def worker(batch: dict) -> dict:
        lines = [f"{r['record_id']}\t{r['title']}" for r in batch["items"]]
        text = relay.call(USER_TMPL.format(n=len(batch["items"]), lines="\n".join(lines)))
        return {"batch_id": batch["batch_id"],
                "frames": normalize(parse_json(text), batch["items"])}

    results, failures = run_batches(batches, worker, args.run_dir / "raw" / "frames",
                                    args.concurrency, "frames")
    if failures:
        print(f"有 {len(failures)} 批失败，重跑即可续跑：{failures}", file=sys.stderr)

    frames = [f for payload in results for f in payload["frames"]]
    write_jsonl(args.run_dir / "work" / "frames.jsonl", frames)

    total = len(frames) or 1
    for field in ("actor", "action", "object", "event_type", "parent_hint",
                  "stage", "time", "location"):
        filled = sum(1 for f in frames if f.get(field))
        print(f"  {field:12s} 填充率 {filled / total:6.1%}")
    weak = sum(1 for f in frames if not f.get("actor") and not f.get("action"))
    print(f"actor 与 action 都为空：{weak} 条（应接近 0，否则查输入或提示词）")
    report(relay, "02_extract_frames", args.run_dir / "run_manifest.json",
           {"frames": len(frames), "failed_batches": len(failures)})
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
