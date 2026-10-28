"""Shared test helpers: sys.path wiring, DAG path, temp workspace, auto-resume."""
import sys, os
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAG_PATH = os.path.join(ORCH_DIR, "../../io/contracts/pipeline.v0.2.yaml")

from pathlib import Path  # noqa: E402

import engine  # noqa: E402
import hc_bridge  # noqa: E402
import handlers  # noqa: E402


def default_human_outputs(st, node_id=None):
    node_id = node_id or st.human["node"]
    if node_id == "n01_hc_requirement":
        import json
        return hc_bridge.build_human_outputs(st, "pass", json.dumps({
            "brand": "测试品牌",
            "activity_name": "测试活动",
            "period_start": "2026-01-01",
            "period_end": "2026-01-07",
            "platforms": ["xiaohongshu"],
            "brand_query": "测试品牌",
            "activity_query": "测试活动",
            "product_query": "测试产品",
            "content_scope": "all",
            "business_questions": ["活动表现与改进"],
            "delivery_expectation": "飞书营销活动复盘报告",
            "allowed_actions": "调用已登记能力",
            "source_file": {"name": "test.xlsx", "mount_path": "test.xlsx"},
        }, ensure_ascii=False))
    if node_id == "n17_validate":
        return {"qc_report": {"has_p0": False, "p0_count": 0, "issues": []}}
    return hc_bridge.build_human_outputs(st, "pass", "")


def load_dag():
    return engine.Dag.load(Path(DAG_PATH))


def make_state(workspace, task_id="t1", run_id="r1", params=None, current_node="n00_intake"):
    """Create a RunState with a registered run_dir rooted at `workspace`."""
    from state import RunState, register_run_dir

    run_dir = Path(workspace) / "tasks" / task_id / "runs" / run_id
    for sub in ("artifacts", "attempts", "validation", "delivery"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    register_run_dir(run_id, run_dir)
    st = RunState(
        run_id=run_id, task_id=task_id, dag_slug="s", dag_version="0.2",
        entry=current_node,
        params={"run_mode": "full", **dict(params or {})},
        current_node=current_node,
        frontier=[current_node],  # 与 engine.create_run 对齐：run() 靠 while st.frontier 驱动
    )
    return st


@contextmanager
def compliant_pipeline_services():
    """Install deterministic, non-degraded service outputs for topology tests."""
    originals = {}
    original_current_revision = handlers.feishu.current_revision

    def install(name, fn):
        originals[name] = handlers._REGISTRY[name]
        handlers._REGISTRY[name] = fn

    def output_fixture(node, st, _ctx):
        payloads = {
            "run_record": {"status": "initialized"},
            "resource_lock": {"lock": "lock_test", "frozen": True},
            "context_snapshot": {"confirmed_plan": st.read_artifact("confirmed_plan")},
            "brand_stats": {"articles": {}, "degraded": False},
            "brand_stats_baseline": {"articles": {}, "degraded": False},
            "cleaned_data": {"rows": [], "clean_record": {"total_out": 0}},
            "ugc_category_list": {
                "mode": "fixed_four",
                "categories": ["模仿打卡", "创意改编", "测评分享", "自发安利"],
                "source": "fixed",
            },
            "annotated_round1": {"round": 1, "rows": [], "degraded": False},
            "opinion_candidates": {
                "candidates": ["正面体验"],
                "source": "fixture",
                "degraded": False,
            },
            "annotated_data": {
                "round": 2,
                "rows": [],
                "coverage": {"has_error": False},
                "degraded": False,
            },
            "stats_bundle": {"campaigns": {}, "degraded": False},
            "wordcloud_data": {"campaigns": {}, "degraded": False},
            "chart_urls": {"charts": [], "chart_count": 0, "degraded": False},
            "wordcloud_insight": {"campaigns": {}, "source": "fixture"},
            "report_md": {
                "markdown": "# 测试报告\n\n## 品牌传播表现\n\n## 活动传播表现\n\n"
                "## 综合定位\n\n## 经验总结\n",
                "chapters": "five",
                "version": f"0.{st.counters.get('report_patch', 0)}",
                "source": "fixture",
                "degraded": False,
            },
            "delivery_draft": {
                "state": "ready",
                "report_version": "0.0",
                "revision_id": 1,
                "degraded": False,
            },
            "external_receipts": {"revision_id": 1, "degraded": False},
            "feishu_doc": {
                "doc_token": "doc_fixture",
                "url": "https://www.feishu.cn/docx/doc_fixture",
                "revision_id": 1,
                "report_version": "0.0",
                "ownership_transferred": True,
                "readback": {"ok": True, "revision_id": 1},
                "degraded": False,
            },
            "delivery_record": {"committed": True, "revision_id": 1, "degraded": False},
            "task_result": {"status": "delivered"},
            "handoff": {
                "validated": True,
                "terminal_accepted": True,
                "degraded": False,
            },
        }
        if node["id"] == "n04_run_init":
            st.params["resource_lock"] = "lock_test"
        if node["id"] == "n19_fix":
            st.counters["qc_rounds"] = st.counters.get("qc_rounds", 0) + 1
            st.counters["report_patch"] = st.counters.get("report_patch", 0) + 1
        return {
            "artifacts": {
                key: payloads.get(key, {"fixture": True})
                for key in node.get("outputs", [])
            }
        }

    for name in (
        "run_init", "ssjh_pull", "clean_campaign", "ugc_discovery", "annotate",
        "opinion_candidates", "aggregate", "charts_render", "wordcloud_insight",
        "write_report", "fix_report", "delivery_draft", "delivery_commit", "handoff",
    ):
        install(name, output_fixture)
    handlers.feishu.current_revision = lambda _doc_token: 1
    try:
        yield
    finally:
        handlers.feishu.current_revision = original_current_revision
        handlers._REGISTRY.update(originals)


def drive_to_completion(
    dag, st, human_outputs_by_node=None, ctx=None, max_steps=60, mode="full"
):
    """Run + auto-resume all human gates until completed/failed.

    human_outputs_by_node: {node_id: {artifact_key: payload}} — payload injected
    when the run pauses at that gate. For n17 the injected qc_report drives the
    QC decision branch.
    """
    human_outputs_by_node = human_outputs_by_node or {}
    st.params["run_mode"] = mode
    with compliant_pipeline_services():
        if st.status != "awaiting_human":
            engine.run(dag, st, ctx)
        steps = 0
        while st.status == "awaiting_human" and steps < max_steps:
            node_id = st.human["node"]
            if node_id in human_outputs_by_node:
                outs = human_outputs_by_node[node_id]
            else:
                outs = default_human_outputs(st, node_id)
            engine.resume(dag, st, human_outputs=outs, ctx=ctx)
            steps += 1
    return st
