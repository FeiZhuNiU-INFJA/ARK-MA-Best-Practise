#!/usr/bin/env python3
"""阶段 1：事件性判定（第一道 Gate）。

三分类 EVENT / NON_EVENT / UNCERTAIN + reason_code。**只判定，不剥壳**——
剥壳已经在阶段 00 按平台做完，本阶段是全平台共用的一套标准。

必须用强模型，不得为省成本降级：实测廉价模型的高置信 NON_EVENT 有 49% 被翻案。
confidence 只落盘，本阶段不参与任何路由。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import (Relay, ids_hash, make_batches, parse_json, read_jsonl, report,
                   require_full_coverage, run_batches, write_csv, write_jsonl)

SYSTEM = """你是热点内容的事件性判定专家。判断每条内容是否指向一个现实中具体发生、即将发生或状态发生变化的事项。

判定标准不是"这句话像不像新闻句式"，而是"它背后是否有一个现实触发点"。

EVENT — 有明确现实触发点：
- 官方动作、回应、通报、处置
- 作品上线、定档、开播、发布
- 产品发布、商业合作、价格调整
- 具名活动、具名赛事、具名营销活动（含平台官方发起的具名话题活动）
- 赛事进展、赛果
- 公共争议、事故、纠纷
- 对某个具体事件的解读讨论（"如何看待XX"里的 XX 是有时间点、官方确认或已被广泛报道的事件时，算 EVENT）

NON_EVENT — 没有现实触发点：
- 纯知识科普、教程、攻略、选购建议、健康建议
- 纯观点征集、纯情绪抒发
- 纯审美展示（穿搭、妆容、风景）、个人日常、游记
- 只有一个宽泛概念或行业名、没有任何具体动作
- 残缺到无法恢复语义

UNCERTAIN — 有事件迹象但信息不足以确认（如仅有轮廓无细节、来源不明、时效不清）。

五条硬性约束：
1. 表述口语化、带情绪、带表情符号，都不影响判定。看的是背后有没有事，不是文字体面不体面。
2. 判不准时用 UNCERTAIN，不要硬判 NON_EVENT。只有 NON_EVENT 会被真正丢弃。
3. underlying_event 是剥掉"如何看待"、"你怎么看"这类外壳后的事件本体。判为 NON_EVENT 时留空字符串。
4. 热搜标签式短文本（如"某某 回应""某某 官宣"）不以完整句式为前提，根据已知语境判定，能还原事件即不算残缺。
5. underlying_event 只能用原文已有的信息重写，不许补充原文没有的内容。具体禁止：
   - 不许加括号补注（写成 `某剧开播（改编自同名小说）` 这类一律不行）
   - 不许补原文没写的届次、年份、编号、赛事全名
   - 不许把"讨论"、"引发关注"当成动作填进去凑句子
   实测这类自行补充会污染跨平台匹配——同一件事在别的平台是 10 来字的短标题，
   补出来的内容会让两条对不上。宁可短，不许猜。

只输出 JSON，不要任何解释。"""

REASON_CODES = {
    "SPECIFIC_STATE_CHANGE", "OFFICIAL_ACTION", "RELEASE_OR_LAUNCH", "NAMED_ACTIVITY",
    "PUBLIC_DISPUTE", "EVENT_COMMENTARY", "GENERIC_KNOWLEDGE", "OPINION_SOLICIT",
    "EMOTION_OR_DISPLAY", "ENTITY_ONLY", "TRUNCATED", "INSUFFICIENT_INFO",
}
EVENTNESS = {"EVENT", "NON_EVENT", "UNCERTAIN"}

USER_TMPL = """判定以下 {n} 条内容的事件性。

{lines}

输出格式：
{{"records":[{{"record_id":"","eventness":"EVENT|NON_EVENT|UNCERTAIN","underlying_event":"","reason_code":"","confidence":0.0}}]}}

reason_code 从以下按 eventness 选一个，不许跨档位取值：
  EVENT 侧：SPECIFIC_STATE_CHANGE · OFFICIAL_ACTION · RELEASE_OR_LAUNCH · NAMED_ACTIVITY · PUBLIC_DISPUTE · EVENT_COMMENTARY
  NON_EVENT 侧：GENERIC_KNOWLEDGE · OPINION_SOLICIT · EMOTION_OR_DISPLAY · ENTITY_ONLY · TRUNCATED
  UNCERTAIN 侧：INSUFFICIENT_INFO

confidence 为 0.0–1.0 的浮点数。
参考锚点：≥0.85 高确定 / 0.60–0.84 中等 / <0.60 低确定

每个 record_id 必须出现且仅出现一次。"""


def normalize(raw: dict, ids: list[str]) -> list[dict]:
    """本地补齐与校验。不改模型的语义判断，只兜底缺失与非法枚举。"""
    seen: set[str] = set()
    out = []
    for row in raw.get("records", []):
        rid = str(row.get("record_id", "")).strip()
        if rid not in ids or rid in seen:
            continue
        seen.add(rid)
        eventness = str(row.get("eventness", "")).upper().strip()
        if eventness not in EVENTNESS:
            eventness = "UNCERTAIN"
        code = str(row.get("reason_code", "")).upper().strip()
        try:
            conf = float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        out.append({
            "record_id": rid,
            "eventness": eventness,
            "reason_code": code if code in REASON_CODES else "",
            "model_confidence": conf,
        })
    # 漏返的一律兜底 UNCERTAIN，绝不丢数据，也绝不擅自判为 NON_EVENT
    for rid in require_full_coverage(ids, [r["record_id"] for r in out], "eventness"):
        out.append({"record_id": rid, "eventness": "UNCERTAIN", "reason_code": "",
                    "model_confidence": 0.0, "fallback": True})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--batch-size", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=16000)
    args = ap.parse_args()

    src = args.run_dir / "work" / "clean_titles.jsonl"
    if not src.exists():
        raise SystemExit(f"缺少 {src}，先跑 00_clean_titles.py。"
                         "判定的输入必须是清洗后的标题，不接受原始 CSV——"
                         "两条入口会让「这一趟到底判的是哪版标题」无从追查")
    records = list(read_jsonl(src))
    print(f"读入 {len(records)} 条，"
          f"input_hash={ids_hash([r['record_id'] for r in records])}")

    relay = Relay(args.model, args.max_tokens, SYSTEM)
    batches = make_batches(records, args.batch_size, tag="EV")

    def worker(batch: dict) -> dict:
        lines = "\n".join(f"{r['record_id']}\t{r['clean_title']}" for r in batch["items"])
        text = relay.call(USER_TMPL.format(n=len(batch["items"]), lines=lines))
        return {"batch_id": batch["batch_id"],
                "records": normalize(parse_json(text), batch["ids"])}

    results, failures = run_batches(batches, worker, args.run_dir / "raw" / "eventness",
                                    args.concurrency, "eventness")
    if failures:
        print(f"有 {len(failures)} 批失败，重跑本脚本即可续跑：{failures}", file=sys.stderr)

    rows = [r for payload in results for r in payload["records"]]
    by_id = {r["record_id"]: r for r in rows}
    merged = [{**r, **by_id.get(r["record_id"], {})} for r in records]

    write_jsonl(args.run_dir / "work" / "eventness.jsonl", merged)
    write_csv(args.run_dir / "out" / "01_eventness.csv", merged,
              ["record_id", "platform", "title", "clean_title", "heat", "eventness",
               "reason_code", "model_confidence"])

    dist: dict[str, int] = {}
    for row in merged:
        dist[row.get("eventness", "MISSING")] = dist.get(row.get("eventness", "MISSING"), 0) + 1
    print("事件性分布：" + " ".join(f"{k}={v}" for k, v in sorted(dist.items())))
    report(relay, "01_eventness", args.run_dir / "run_manifest.json",
           {"records": len(merged), "failed_batches": len(failures)})

    if failures:
        return 1
    print("\n下一步：抽检 NON_EVENT，确认没有误删具名活动、在映在更新作品的剧情讨论、"
          "事件解读三类，再跑下一阶段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
