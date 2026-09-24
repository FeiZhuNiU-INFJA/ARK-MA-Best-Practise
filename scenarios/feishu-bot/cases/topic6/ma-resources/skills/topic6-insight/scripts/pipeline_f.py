#!/usr/bin/env python3
"""
Phase F Step 0 · 合并四版块洞察为完整报告(MA 版)

读取 {project_dir}/06_洞察/v{N}/e1_v{N}.md ~ e4_v{N}.md,拼接成一份完整周报。
只做 Step 0(合并),不含自校验/飞书发布步骤。

MA 适配:删掉 PROJECT_ROOT.parents[1] 上溯,--project-dir 支持绝对/相对(相对以 /workspace 为根)。

用法:
    python pipeline_f.py --project-dir "/workspace/Projects/W35_20260824-20260830" \\
        [--version N] [--period-label 2026-W35] [--date-start 2026-08-24] [--date-end 2026-08-30]

输出:{project_dir}/07_报告/热点报告_{period_label}_v{N}.md
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = Path("/workspace")

SECTION_IDS = ["e1", "e2", "e3", "e4"]


def latest_insight_round(insight_dir: Path) -> int | None:
    versions = []
    for d in insight_dir.glob("v*"):
        if d.is_dir():
            m = re.match(r"v(\d+)$", d.name)
            if m:
                versions.append(int(m.group(1)))
    return max(versions) if versions else None


def resolve_period_info(proj: Path, args: argparse.Namespace) -> tuple[str, str, str]:
    """解析 period_label / date_start / date_end。优先级:
    1. CLI 显式传参
    2. run_config.yaml 的 period 块
    3. 项目目录名解析(W{N}_{YYYYMMDD}-{YYYYMMDD})
    三者都取不到时报错退出(不编造)。"""
    period_label, date_start, date_end = args.period_label, args.date_start, args.date_end
    if period_label and date_start and date_end:
        return period_label, date_start, date_end

    cfg_path = proj / "run_config.yaml"
    if cfg_path.exists():
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        period = cfg.get("period", {})
        period_label = period_label or period.get("period_label")
        date_start = date_start or period.get("date_start")
        date_end = date_end or period.get("date_end")
        if period_label and date_start and date_end:
            return period_label, date_start, date_end

    m = re.match(r"(W\d+)[^\d]*(\d{8})-(\d{8})", proj.name)
    if m:
        week_id, start_raw, end_raw = m.groups()
        date_start = date_start or f"{start_raw[:4]}-{start_raw[4:6]}-{start_raw[6:]}"
        date_end = date_end or f"{end_raw[:4]}-{end_raw[4:6]}-{end_raw[6:]}"
        period_label = period_label or f"{date_start[:4]}-{week_id}"
        return period_label, date_start, date_end

    print(
        f"[pipeline_f] 无法解析周期信息:{proj.name} 不匹配 W{{N}}_{{YYYYMMDD}}-{{YYYYMMDD}},"
        "且未提供 run_config.yaml 或 --period-label/--date-start/--date-end",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="合并四版块洞察为完整报告(Phase F Step 0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-dir", required=True, help="项目目录(绝对路径,或相对 /workspace)")
    parser.add_argument("--version", type=int, default=None, help="要合并的洞察轮次;不传时自动取06_洞察/下最新一轮")
    parser.add_argument("--period-label", default=None, help="覆盖周期标签,如 2026-W35")
    parser.add_argument("--date-start", default=None, help="覆盖起始日期,如 2026-08-24")
    parser.add_argument("--date-end", default=None, help="覆盖结束日期,如 2026-08-30")
    args = parser.parse_args()

    proj = Path(args.project_dir)
    if not proj.is_absolute():
        proj = WORKSPACE_ROOT / proj
    if not proj.exists():
        print(f"[pipeline_f] 项目目录不存在:{proj}", file=sys.stderr)
        return 1

    insight_dir = proj / "06_洞察"
    version = args.version if args.version is not None else latest_insight_round(insight_dir)
    if version is None:
        print(f"[pipeline_f] {insight_dir} 下没有任何 v{{N}}/ 子目录,先跑 pipeline_e.py", file=sys.stderr)
        return 1

    round_dir = insight_dir / f"v{version}"
    section_files = {sid: round_dir / f"{sid}_v{version}.md" for sid in SECTION_IDS}
    missing = [str(p) for p in section_files.values() if not p.exists() or p.stat().st_size == 0]
    if missing:
        print(f"[pipeline_f] 以下文件缺失或为空:{missing}", file=sys.stderr)
        return 1

    print(f"[pipeline_f] 合并轮次:v{version}({round_dir})")

    period_label, date_start, date_end = resolve_period_info(proj, args)
    print(f"[pipeline_f] 周期:{period_label}({date_start} ~ {date_end})")

    sections = [section_files[sid].read_text(encoding="utf-8").strip() for sid in SECTION_IDS]

    report = "\n\n".join([
        f"# 社媒热点周刊 · {period_label}",
        f"> 数据范围:微博、知乎、抖音、B站\n> 数据周期:{period_label}({date_start} ~ {date_end})",
        "\n\n---\n\n".join(sections),
    ])

    out_dir = proj / "07_报告"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"热点报告_{period_label}_v{version}.md"
    out_path.write_text(report + "\n", encoding="utf-8")

    print(f"[pipeline_f] 已写入:{out_path}")
    print(f"[pipeline_f] 完成,共 {len(report)} 字符")
    return 0


if __name__ == "__main__":
    sys.exit(main())
