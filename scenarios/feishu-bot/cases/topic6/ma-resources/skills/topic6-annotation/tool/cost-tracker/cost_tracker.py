#!/usr/bin/env python3
"""
cost_tracker.py — Topic6 双轨成本台账 (MA 版)

MA 环境改动:
  - _cost_dir(): 客户版靠 __file__.parent.parent.parent 上溯到项目根,
    MA 环境下 skill 挂载到 /mnt/skills/topic6-annotation/tool/, 上溯行不通。
    改为直接把 --project-dir 当作项目绝对路径使用 (支持相对路径时补 /workspace/)。
  - 移除 io.TextIOWrapper 包裹, 改用 sys.stdout.reconfigure (客户源已同步这个修补方向)。

存储:
  {project_dir}/costs/cost_tracker.jsonl
  {project_dir}/costs/本周成本汇总.md

CLI:
  # append
  python cost_tracker.py --project-dir /workspace/Projects/W35 append \\
    --phase C --task c0_基础事实标注 --round 1 --prompt-version v1 \\
    --model-id gpt-4o-mini --platform OpenAI \\
    --input-tokens 450000 --output-tokens 50000 \\
    --raw-cost 0.075 --currency USD --row-count 500 --mode test

  # finalize
  python cost_tracker.py --project-dir /workspace/Projects/W35 finalize \\
    --usd-to-cny 7.2
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

COST_DIR = "costs"
JSONL_FILE = "cost_tracker.jsonl"
REPORT_FILE = "本周成本汇总.md"

USD_PLATFORMS: set[str] = {"OpenAI", "Anthropic", "Google", "Meta", "Mistral"}

PHASE_LABELS: dict[str, str] = {
    "C": "Phase C 数据标注 (DataHub)",
    "E": "Phase E 洞察生成 (直调 LLM)",
    "F": "Phase F 报告自校验 (直调 LLM)",
}


def _resolve_project_dir(project_dir: str) -> Path:
    p = Path(project_dir)
    if not p.is_absolute():
        p = Path("/workspace") / project_dir
    return p


def _cost_dir(project_dir: str) -> Path:
    return _resolve_project_dir(project_dir) / COST_DIR


def append_record(record: dict) -> None:
    cost_dir = _cost_dir(record["project_dir"])
    cost_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = cost_dir / JSONL_FILE

    usd_to_cny = float(record.get("usd_to_cny", 7.2))
    currency = record.get("currency", "USD")
    raw_cost = float(record.get("raw_cost", 0.0))
    input_tok = int(record.get("input_tokens", 0))
    output_tok = int(record.get("output_tokens", 0))

    if currency == "USD":
        cost_usd = round(raw_cost, 6)
        cost_cny = round(raw_cost * usd_to_cny, 4)
    else:
        cost_cny = round(raw_cost, 4)
        cost_usd = round(raw_cost / usd_to_cny, 6)

    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "phase": record.get("phase", "?"),
        "task": record.get("task", "?"),
        "round": int(record.get("round", 1)),
        "prompt_version": record.get("prompt_version", ""),
        "mode": record.get("mode", ""),
        "model_id": record.get("model_id", ""),
        "platform": record.get("platform", ""),
        "currency": currency,
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "total_tokens": input_tok + output_tok,
        "raw_cost": raw_cost,
        "cost_usd": cost_usd,
        "cost_cny": cost_cny,
        "usd_to_cny": usd_to_cny,
        "row_count": int(record.get("row_count", 0)),
    }

    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(
        f"[cost_tracker] 已记录 {entry['phase']}/{entry['task']} "
        f"round={entry['round']} {entry['total_tokens']:,} tokens "
        f"¥{entry['cost_cny']:.4f} (≈ ${entry['cost_usd']:.6f})"
    )


def finalize(project_dir: str, usd_to_cny: float = 7.2, period_label: str = "") -> None:
    cost_dir = _cost_dir(project_dir)
    jsonl_path = cost_dir / JSONL_FILE
    report_path = cost_dir / REPORT_FILE

    if not jsonl_path.exists():
        print("[cost_tracker] 无成本记录 (cost_tracker.jsonl 不存在)")
        return

    records: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    if not records:
        print("[cost_tracker] 无有效记录")
        return

    title_label = period_label or project_dir.rsplit("/", 1)[-1]
    lines = [
        f"# 本周成本汇总 — {title_label}", "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 汇率参考: 1 USD = {usd_to_cny} CNY (来自 run_config.yaml `cost.usd_to_cny`)",
        "",
    ]

    by_phase: dict[str, list] = defaultdict(list)
    for r in records:
        by_phase[r.get("phase", "?")].append(r)

    grand_cny = 0.0
    grand_usd_native = 0.0

    for phase in sorted(by_phase.keys()):
        recs = by_phase[phase]
        label = PHASE_LABELS.get(phase, f"Phase {phase}")
        lines += [
            f"## {label}", "",
            "| 任务 | 轮次 | 模式 | Prompt | 模型 | 平台 | 计费 | Input Tok | Output Tok | 费用 |",
            "|------|------|------|--------|------|------|------|-----------|------------|------|",
        ]

        phase_cny = 0.0
        for r in recs:
            currency = r.get("currency", "USD")
            raw = float(r.get("raw_cost", 0))
            c_cny = float(r.get("cost_cny", 0))
            c_usd = float(r.get("cost_usd", 0))
            mode_str = f"({r.get('mode')})" if r.get("mode") else ""

            if currency == "USD":
                cost_str = f"${raw:.4f} ≈ ¥{c_cny:.4f}"
                grand_usd_native += raw
            else:
                cost_str = f"¥{raw:.4f} ≈ ${c_usd:.6f}"

            phase_cny += c_cny
            grand_cny += c_cny

            lines.append(
                f"| {r.get('task','-')} | R{r.get('round',1)} | {mode_str} "
                f"| {r.get('prompt_version','-')} | {r.get('model_id','-')} "
                f"| {r.get('platform','-')} | {currency} "
                f"| {r.get('input_tokens',0):,} | {r.get('output_tokens',0):,} "
                f"| {cost_str} |"
            )
        lines += ["", f"**{label} 小计: ¥{phase_cny:.4f}**", ""]

    lines += [
        "---", "", "## 项目总成本", "",
        "| 科目 | 金额 |",
        "|------|------|",
        f"| 汇率 (USD/CNY) | 1 USD = {usd_to_cny} CNY |",
        f"| USD 原生费用 | ${grand_usd_native:.4f} |",
        f"| **折算总成本 (CNY)** | **¥{grand_cny:.4f}** |",
        "",
    ]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[cost_tracker] 报告: {report_path}")
    print(f"  总成本: ¥{grand_cny:.4f} (USD 原生 ${grand_usd_native:.4f})")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Topic6 成本台账 (MA 版)")
    p.add_argument("--project-dir", required=True,
                   help="项目目录 (支持绝对路径或相对 /workspace/ 的相对路径)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ap = sub.add_parser("append", help="追加一条成本记录")
    ap.add_argument("--phase", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--prompt-version", default="")
    ap.add_argument("--mode", default="")
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--platform", required=True)
    ap.add_argument("--input-tokens", type=int, required=True)
    ap.add_argument("--output-tokens", type=int, required=True)
    ap.add_argument("--raw-cost", type=float, required=True)
    ap.add_argument("--currency", choices=["USD", "CNY"], required=True)
    ap.add_argument("--usd-to-cny", type=float, default=7.2)
    ap.add_argument("--row-count", type=int, default=0)

    fp = sub.add_parser("finalize", help="汇总生成 md 报告")
    fp.add_argument("--usd-to-cny", type=float, default=7.2)
    fp.add_argument("--period-label", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if args.cmd == "append":
        append_record({
            "project_dir": args.project_dir,
            "phase": args.phase, "task": args.task, "round": args.round,
            "prompt_version": args.prompt_version, "mode": args.mode,
            "model_id": args.model_id, "platform": args.platform,
            "currency": args.currency,
            "input_tokens": args.input_tokens, "output_tokens": args.output_tokens,
            "raw_cost": args.raw_cost,
            "usd_to_cny": args.usd_to_cny, "row_count": args.row_count,
        })
    elif args.cmd == "finalize":
        finalize(args.project_dir, args.usd_to_cny, args.period_label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
