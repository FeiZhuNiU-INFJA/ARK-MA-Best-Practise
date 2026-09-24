#!/usr/bin/env python3
"""
merge_annotations.py — 七路标注宽表合并 (C0 + R1~R5 + C2 + C3)

MA 精简版:
  1. 输出 28 列宽表 (基础字段 9 列 + C0 7 列 + [提取节点, 事件簇名] + R1~R5 各 2 列)
  2. C3 只保留过滤后的 "提取节点"; C2 只保留 "事件簇名"
  3. 单路缺失只告警不中断, 用 health_summary md 汇总
  4. 去掉客户版对 patch/retry 相关字段的引用

输入 (相对 project_dir):
  02_标准化/hot_topics_normalized.xlsx
  03_抽样/sample_500.xlsx                     (test 模式过滤 row_id)
  04_标注/{C0,R1..R5,C3}_*/{task}_postprocess_r{run_id}.xlsx
  04_标注/C2_事件归档/c2_event_result_r{run_id}.xlsx

输出:
  05_合并/wide_table_{mode}_r{run_id}.xlsx
  05_合并/health_summary_{mode}_r{run_id}.md

CLI:
  python merge_annotations.py --project-dir /workspace/Projects/W35 \\
    --mode test --run-id 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

TASK_MERGE_SPECS: dict[str, dict] = {
    "c0": {
        "subdir": "C0_基础事实",
        "raw_cols": [
            "c0_商业实体", "c0_热点驱动词", "c0_行业归属", "c0_营销触发方式",
            "c0_营销维度", "c0_是否营销可用", "c0_判断说明",
        ],
        "rename": {
            "c0_商业实体": "商业实体", "c0_热点驱动词": "热点驱动词", "c0_行业归属": "行业归属",
            "c0_营销触发方式": "营销触发方式", "c0_营销维度": "营销维度",
            "c0_是否营销可用": "是否营销可用", "c0_判断说明": "判断说明",
        },
    },
    "r1": {"subdir": "R1_平台借势", "raw_cols": ["r1_是否平台玩法", "r1_判断说明"],
           "rename": {"r1_是否平台玩法": "是否平台玩法", "r1_判断说明": "平台玩法说明"}},
    "r2": {"subdir": "R2_商业合作", "raw_cols": ["r2_是否商业合作", "r2_判断说明"],
           "rename": {"r2_是否商业合作": "是否商业合作", "r2_判断说明": "合作动态说明"}},
    "r3": {"subdir": "R3_风险预警", "raw_cols": ["r3_是否风险预警", "r3_判断说明"],
           "rename": {"r3_是否风险预警": "是否风险预警", "r3_判断说明": "风险判断说明"}},
    "r4": {"subdir": "R4_创意借鉴", "raw_cols": ["r4_是否营销发现", "r4_判断说明"],
           "rename": {"r4_是否营销发现": "是否营销发现", "r4_判断说明": "营销发现说明"}},
    "r5": {"subdir": "R5_消费者行为", "raw_cols": ["r5_是否消费者行为", "r5_判断说明"],
           "rename": {"r5_是否消费者行为": "是否消费者行为", "r5_判断说明": "消费者行为说明"}},
}
TASK_ORDER = ["c0", "r1", "r2", "r3", "r4", "r5"]

C3_SOURCE_COLS = ["提取节点", "是否节日营销", "是否窗口期内", "c3_parse_error"]

BASE_RENAME = {
    "row_id": "序号", "platform": "平台", "title": "热点标题",
    "hottopic_desc": "热点描述", "hot_index": "平台热度值",
    "heat_score": "标准化热度分", "hotpost_time": "发布时间",
    "hottopic_url": "链接", "week_num": "周数",
}


def _resolve_project_dir(project_dir: str) -> Path:
    p = Path(project_dir)
    if not p.is_absolute():
        p = Path("/workspace") / project_dir
    return p


def _read_or_warn(file_path: Path, label: str) -> pd.DataFrame | None:
    if not file_path.exists():
        print(f"[merge] ⚠️ {label} 不存在: {file_path}", file=sys.stderr)
        return None
    df = pd.read_excel(file_path)
    print(f"[merge] {label}: {len(df)} 行 × {len(df.columns)} 列")
    return df


def _filter_c3_nodes(row: pd.Series) -> str | float:
    """按位置过滤: 只保留 是否节日营销=是 且 是否窗口期内=是 的节点。"""
    nodes_raw = row.get("提取节点")
    if pd.isna(nodes_raw):
        return float("nan")
    nodes = str(nodes_raw).split("|")
    festival = str(row.get("是否节日营销") or "").split("|")
    window = str(row.get("是否窗口期内") or "").split("|")
    kept = [
        n for i, n in enumerate(nodes)
        if n and i < len(festival) and i < len(window)
        and festival[i].strip() == "是" and window[i].strip() == "是"
    ]
    return "|".join(kept) if kept else float("nan")


def run(project_dir: str, mode: str, run_id: int) -> dict:
    proj = _resolve_project_dir(project_dir)
    ann_dir = proj / "04_标注"
    merge_dir = proj / "05_合并"
    merge_dir.mkdir(parents=True, exist_ok=True)
    print(f"[merge] run_id=r{run_id} mode={mode}")

    base_file = proj / "02_标准化" / "hot_topics_normalized.xlsx"
    if not base_file.exists():
        raise FileNotFoundError(f"基础表不存在: {base_file}")
    df_base = pd.read_excel(base_file)
    base_rows = len(df_base)
    print(f"[merge] 基础表: {base_rows} 行")

    if mode == "test":
        sample_file = proj / "03_抽样" / "sample_500.xlsx"
        if sample_file.exists():
            sample_ids = set(pd.read_excel(sample_file, usecols=["row_id"])["row_id"])
            df_base = df_base[df_base["row_id"].isin(sample_ids)].copy()
            base_rows = len(df_base)
            print(f"[merge] test 模式过滤至 {base_rows} 行")

    df = df_base.copy()
    missing: dict[str, bool] = {}
    valid_rates: dict[str, float | None] = {}

    for task in TASK_ORDER:
        spec = TASK_MERGE_SPECS[task]
        task_file = ann_dir / spec["subdir"] / f"{task}_postprocess_r{run_id}.xlsx"
        df_task = _read_or_warn(task_file, f"{task.upper()}")
        missing[task] = df_task is None
        valid_rates[task] = None
        if df_task is None:
            continue

        keep = ["row_id"] + [c for c in spec["raw_cols"] if c in df_task.columns]
        df = df.merge(df_task[keep], on="row_id", how="left")
        if f"{task}_parse_error" in df_task.columns:
            n = len(df_task)
            valid_rates[task] = round(1.0 - df_task[f"{task}_parse_error"].sum() / n, 4) if n else None

    c3_file = ann_dir / "C3_节点标注" / f"c3_postprocess_r{run_id}.xlsx"
    df_c3 = _read_or_warn(c3_file, "C3")
    missing["c3"] = df_c3 is None
    if df_c3 is not None:
        keep = ["row_id"] + [c for c in C3_SOURCE_COLS if c in df_c3.columns]
        df_c3 = df_c3[keep].copy()
        df_c3["提取节点"] = df_c3.apply(_filter_c3_nodes, axis=1)
        df = df.merge(df_c3[["row_id", "提取节点"]], on="row_id", how="left")

    c2_file = ann_dir / "C2_事件归档" / f"c2_event_result_r{run_id}.xlsx"
    df_c2 = _read_or_warn(c2_file, "C2")
    missing["c2"] = df_c2 is None
    if df_c2 is not None:
        df_c2 = df_c2.rename(columns={"event_name": "事件簇名", "一级事件名": "事件簇名"})
        if "事件簇名" not in df_c2.columns:
            print("[merge] ⚠️ C2 缺少 event_name/一级事件名, 跳过", file=sys.stderr)
            missing["c2"] = True
        else:
            if "row_id" in df_c2.columns:
                df_c2 = df_c2[["row_id", "事件簇名"]]
                df = df.merge(df_c2, on="row_id", how="left")
            elif "record_id" in df_c2.columns:
                df_c2 = df_c2.rename(columns={"record_id": "row_id"})[["row_id", "事件簇名"]]
                df = df.merge(df_c2, on="row_id", how="left")
            elif "word" in df_c2.columns and "platform" in df_c2.columns:
                df_c2 = df_c2.rename(columns={"word": "title"})[["platform", "title", "事件簇名"]]
                df = df.merge(df_c2, on=["platform", "title"], how="left")
            else:
                print("[merge] ⚠️ C2 缺少 row_id/record_id/word, 跳过", file=sys.stderr)
                missing["c2"] = True

    if missing.get("c2") and "事件簇名" not in df.columns:
        df["事件簇名"] = ""

    output_rows = len(df)
    row_match = output_rows == base_rows
    if not row_match:
        print(f"[merge] ⚠️ 行数不一致: 基础表 {base_rows}, 输出 {output_rows}", file=sys.stderr)

    dup_rows = int(df["row_id"].duplicated().sum())
    if dup_rows:
        print(f"[merge] ⚠️ row_id 重复: {dup_rows} 行", file=sys.stderr)

    c3_hit_rate = None
    if "提取节点" in df.columns:
        hit = (df["提取节点"].fillna("") != "").sum()
        c3_hit_rate = round(hit / output_rows, 4) if output_rows else None

    task_rename: dict[str, str] = {}
    for task in TASK_ORDER:
        task_rename.update(TASK_MERGE_SPECS[task]["rename"])
    df = df.rename(columns={**BASE_RENAME, **task_rename})

    drop_cols = [f"{t}_parse_error" for t in TASK_ORDER] + ["hot_thumbnail_url"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])

    c0_names = list(TASK_MERGE_SPECS["c0"]["rename"].values())
    routing_names: list[str] = []
    for task in ("r1", "r2", "r3", "r4", "r5"):
        routing_names.extend(TASK_MERGE_SPECS[task]["rename"].values())
    target = (
        list(BASE_RENAME.values())
        + c0_names
        + ["提取节点", "事件簇名"]
        + routing_names
    )
    ordered = [c for c in target if c in df.columns]
    leftover = [c for c in df.columns if c not in set(ordered)]
    df = df[ordered + leftover]

    out_file = merge_dir / f"wide_table_{mode}_r{run_id}.xlsx"
    tmp = out_file.with_suffix(".tmp.xlsx")
    try:
        df.to_excel(tmp, index=False)
        os.replace(tmp, out_file)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    print(f"\n[merge] ✅ 宽表: {out_file}")
    print(f"[merge] 规格: {output_rows} 行 × {len(df.columns)} 列")

    usable_yes = int((df["是否营销可用"] == "是").sum()) if "是否营销可用" in df.columns else None
    usable_ratio = round(usable_yes / output_rows, 4) if usable_yes is not None and output_rows else None

    lines = [
        f"# HC 健康度摘要 (run_id=r{run_id}, mode={mode})", "",
        f"宽表规格: {output_rows} 行 × {len(df.columns)} 列, row_match={row_match}", "",
        "## 各任务缺失情况",
    ]
    for task in TASK_ORDER + ["c3", "c2"]:
        is_missing = missing.get(task, False)
        vr = valid_rates.get(task)
        vr_str = f", 有效率 {vr:.2%}" if vr is not None else ""
        flag = "❌ 整批缺失" if is_missing else ("✅ 正常" if vr is None or vr >= 0.92 else "⚠️ 有效率偏低")
        lines.append(f"- {task.upper()}: {flag}{vr_str}")
    if usable_ratio is not None:
        note = "（⚠️ 偏离历史区间 53%~62%）" if usable_ratio < 0.50 or usable_ratio > 0.62 else ""
        lines += ["", f"## C0 是否营销可用占比: {usable_ratio:.2%}{note}"]
    if c3_hit_rate is not None:
        lines.append(f"## C3 窗口期节点命中率: {c3_hit_rate:.2%}")
    missing_count = sum(1 for v in missing.values() if v)
    if missing_count >= 2:
        lines += ["", "## ⚠️ ≥2 路整批缺失, 建议人工确认后再送 HC"]

    summary_text = "\n".join(lines)
    summary_file = merge_dir / f"health_summary_{mode}_r{run_id}.md"
    summary_file.write_text(summary_text, encoding="utf-8")
    print(f"\n[merge] 健康度摘要: {summary_file}")
    print(summary_text)

    return {
        "base_rows": base_rows, "output_rows": output_rows,
        "row_match": row_match, "columns": len(df.columns),
        "missing": missing, "valid_rates": valid_rates,
        "c3_hit_rate": c3_hit_rate, "usable_ratio": usable_ratio,
        "run_id": run_id, "mode": mode,
        "out_file": str(out_file),
        "health_summary_file": str(summary_file),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project-dir", required=True)
    p.add_argument("--mode", default="test", choices=["test", "full"])
    p.add_argument("--run-id", type=int, required=True)
    args = p.parse_args()
    try:
        result = run(args.project_dir, args.mode, args.run_id)
        print("\n=== 合并完成 ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
