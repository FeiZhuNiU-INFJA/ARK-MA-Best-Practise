#!/usr/bin/env python3
"""Exercise the real n14 wordcloud adapter and n15 with one fresh SSJH range.

--plan accepts a ConfirmedPlan JSON or saved reproduction state's requirement.
Credentials stay in environment. This does not send Feishu messages or publish.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import json
from pathlib import Path
import sys

CASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CASE / "ma-resources/skills/pipeline-orchestrator"))
import charts
from engine import Dag, create_run
import handlers
import ssjh
import wordcloud_adapter as adapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-artifact", help="复用已保存的真实stats_bundle原始数据，避免重复取数")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        raise ValueError("验证目录必须为新路径")
    out.mkdir(parents=True)
    data = json.loads(Path(args.plan).read_text())
    plan = data.get("requirement") or data
    dag = Dag.load(CASE / "ma-resources/io/contracts/pipeline.v0.2.yaml")
    state = create_run(dag, out, "wordcloud-verification")
    state.write_artifact("confirmed_plan", plan)
    if not args.source_artifact and not ssjh.api_available():
        raise RuntimeError("缺少SSJH凭据/Skill，不能用降级样例代替真实验证")
    # Third party request logs may contain private source data; keep them local.
    if args.source_artifact:
        source_file = Path(args.source_artifact).resolve()
        source = json.loads(source_file.read_text())["brand"]["source"]
        adapter.write(out / "source-provenance.json",
                      {"path": str(source_file), "sha256": adapter.file_hash(source_file)})
    else:
        with (out / "acquisition.log").open("w") as log, redirect_stdout(log), redirect_stderr(log):
            source = ssjh.pull(plan, wordcloud=True, filterword=str(plan.get("brand_filter") or ""))
    if source.get("degraded") or not source.get("cloud_word"):
        raise RuntimeError("SSJH真实词云取数不完整")
    state.write_artifact("stats_bundle", {"brand": {
        "source": source, "wordcloud": source["cloud_word"],
        "wordcloud_rows": source.get("_wordcloud_tables") or {},
        "hot_content": source.get("articles") or {},
    }})
    state.write_artifact("wordcloud_data", {"campaigns": {}})
    chart_dir = state.run_dir / "charts"
    chart_dir.mkdir()
    result = []
    for spec in charts.build_chart_specs(state.read_artifact("stats_bundle"), {}, plan):
        if spec["chart_type"] != "wordcloud":
            continue
        images = charts._render_wordcloud(spec, str(chart_dir))
        result.append({**spec, "spec": spec, "local_path": images["transparent_path"],
                       "wordcloud_artifacts": images})
        print(f"RENDERED {spec['chart_id']} words={len(spec['categories'])}", flush=True)
    state.write_artifact("chart_urls", {"charts": result, "degraded": False})
    analysis = handlers.wordcloud_insight({"id": "n15_wordcloud_insight"}, state, {})["artifacts"]["wordcloud_insight"]
    state.write_artifact("wordcloud_insight", analysis)
    state.save()
    summary = {"status": "verified", "source": "cached real SSJH" if args.source_artifact else "fresh SSJH",
               "run_dir": str(state.run_dir), "ranges": []}
    for dimension, item in analysis["brand"].items():
        for platform, payload in item["platforms"].items():
            summary["ranges"].append({
                "dimension": dimension, "platform": platform,
                "input_word_count": payload["input_word_count"], "read_posts": payload["evidence_count"],
                "topics": len(payload["topics"]), "artifacts": payload["artifacts"],
            })
    adapter.write(out / "verification.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
