"""Topic6 触发解析、严格 HC JSON fail-closed 契约与卡片渲染测试。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1] / "cases" / "topic6"


def _load(name: str, file: str):
    if str(CASE_DIR) not in sys.path:
        sys.path.insert(0, str(CASE_DIR))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, CASE_DIR / file)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner_mod = _load("topic6_runner", "topic6_runner.py")
progress_card_mod = _load("topic6_progress_card", "topic6_progress_card.py")
hitl_mod = _load("topic6_hitl", "topic6_hitl.py")
extract_hc_payload = runner_mod.extract_hc_payload
extract_project_dir = runner_mod.extract_project_dir
parse_trigger = runner_mod.parse_trigger
normalize_user_text = runner_mod.normalize_user_text
validate_hc_payload = runner_mod.validate_hc_payload


def test_parse_trigger_supports_three_current_modes():
    assert parse_trigger("热点周报 demo") == "demo"
    assert parse_trigger("热点报告 演示") == "demo"
    assert parse_trigger("热点周报 full") == "full"
    assert parse_trigger("热点周报 skip_sampling") == "skip_sampling"
    assert parse_trigger("热点周报 跳过采样") == "skip_sampling"


def test_group_mention_is_removed_before_command_and_trigger_parsing():
    assert normalize_user_text("@热点周报助手 /new", mentioned_bot=True) == "/new"
    assert normalize_user_text(
        "@热点周报助手 热点周报 demo", mentioned_bot=True
    ) == "热点周报 demo"
    assert parse_trigger(normalize_user_text("@热点周报助手 /new", True)) is None


def test_progress_header_hides_running_phase_but_keeps_hc_phase():
    running = progress_card_mod._header("running", "A")
    waiting = progress_card_mod._header("wait_hc", "HC1")
    stopped = progress_card_mod._header("stopped", "A")

    assert running["title"]["content"] == "🚀 Topic6 Pipeline · 运行中"
    assert waiting["title"]["content"] == "⏸️ Topic6 Pipeline · 等待审核 HC1"
    assert stopped["title"]["content"] == "⏹️ Topic6 Pipeline · 已停止"


def test_progress_and_hc_cards_use_same_schema_version():
    job = runner_mod.PipelineJob(
        job_id="job-1",
        chat_id="chat-1",
        thread_id="",
        user_open_id="user-1",
        ma_session_id="session-1",
        mode="demo",
        project_dir="/workspace/demo",
    )

    progress = progress_card_mod.build_progress_card(
        job=job,
        status="running",
        elapsed_sec=1,
        tool_lines=["正在执行 Phase A"],
    )
    hc = hitl_mod.build_hc_card(job, "HC1", 1, {"hc": "HC1", "mode": "demo"})

    assert progress["schema"] == hc["schema"] == "2.0"
    assert progress["body"]["elements"][2]["tag"] == "markdown"


def test_hc_card_uses_payload_hc_kind_instead_of_stale_job_phase():
    job = runner_mod.PipelineJob(
        job_id="job-1",
        chat_id="chat-1",
        thread_id="",
        user_open_id="user-1",
        ma_session_id="session-1",
        mode="demo",
        project_dir="/workspace/demo",
        current_phase="HC1",
    )

    card = hitl_mod.build_hc_card(
        job,
        "HC3",
        3,
        {"hc": "HC3", "feishu_doc_url": "https://example.feishu.cn/docx/abc"},
    )

    assert "当前阶段:**HC3**" in card["body"]["elements"][0]["content"]
    form = next(
        element for element in card["body"]["elements"] if element["tag"] == "form"
    )
    assert form["elements"][0] == {
        "tag": "input",
        "name": "reject_reason",
        "required": True,
        "input_type": "multiline_text",
        "rows": 2,
        "auto_resize": True,
        "max_rows": 4,
        "max_length": 500,
        "width": "fill",
        "label": {"tag": "plain_text", "content": "打回原因"},
        "placeholder": {
            "tag": "plain_text",
            "content": "说明需要回退或调整的内容，提交后由 Agent 继续处理",
        },
    }
    assert form["elements"][1]["form_action_type"] == "submit"
    assert form["elements"][1]["name"] == "reject_submit"
    assert form["elements"][1]["behaviors"] == [
        {
            "type": "callback",
            "value": {
                "job_id": job.job_id,
                "hc": "HC3",
                "event_id": 3,
                "decision": "reject",
            },
        }
    ]


def test_hc3_payload_requires_published_feishu_document():
    assert validate_hc_payload({"hc": "HC3", "feishu_doc_url": ""}) == (
        "HC3 缺少 feishu_doc_url：Phase F 飞书发布未完成"
    )
    assert validate_hc_payload(
        {"hc": "HC3", "feishu_doc_url": "https://example.feishu.cn/docx/abc"}
    ) is None


def test_valid_json_takes_priority_over_fallback():
    # 严格 JSON 存在时,现有 extract_hc_payload 已经吃掉;兜底不会覆盖。
    text = '{"hc": "HC1", "mode": "full"}'
    assert extract_hc_payload(text) == {"hc": "HC1", "mode": "full"}


def test_extract_hc_payload_supports_nested_json_in_markdown_fence():
    text = """```json
{
  "hc": "HC1",
  "mode": "demo",
  "distribution_summary": {
    "rows": 500,
    "cols": 28,
    "r1_r5_valid_rates": [1.0, 1.0, 1.0, 1.0, 1.0]
  },
  "issues_detected": []
}
```

备注：通过后将直接进入 Phase E。"""

    assert extract_hc_payload(text) == {
        "hc": "HC1",
        "mode": "demo",
        "distribution_summary": {
            "rows": 500,
            "cols": 28,
            "r1_r5_valid_rates": [1.0, 1.0, 1.0, 1.0, 1.0],
        },
        "issues_detected": [],
    }


def test_extract_project_dir_accepts_only_direct_topic6_project_path():
    project = "/workspace/Projects/W40热点周报_20260928-20261004"
    assert extract_project_dir(f"已创建目录\n[project_dir] {project}") == project
    assert extract_project_dir("[project_dir] /workspace/topic6-demo") is None
    assert extract_project_dir("[project_dir] /workspace/Projects/a/nested") is None
    assert extract_project_dir("[project_dir] /workspace/Projects/../secrets") is None


def test_hc_meta_description_without_json_is_not_accepted():
    text = (
        "宽表已就绪。现在输出 HC1 结构化 JSON，"
        "等待用户点击卡片确认。"
    )
    assert extract_hc_payload(text) is None
