from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest


TOPIC6_DIR = Path(__file__).resolve().parents[1] / "cases" / "topic6"
ANNOTATE_SCRIPT = (
    TOPIC6_DIR
    / "ma-resources"
    / "skills"
    / "topic6-annotation"
    / "scripts"
    / "datahub_annotate.py"
)
PIPELINE_F_SCRIPT = (
    TOPIC6_DIR
    / "ma-resources"
    / "skills"
    / "topic6-insight"
    / "scripts"
    / "pipeline_f.py"
)
COORDINATOR_PROMPT = (
    TOPIC6_DIR / "ma-resources" / "agents" / "coordinator.system.md"
)
ANNOTATOR_PROMPT = (
    TOPIC6_DIR / "ma-resources" / "agents" / "annotator.system.md"
)
EVENT_REGISTRY_DIR = (
    TOPIC6_DIR / "ma-resources" / "skills" / "topic6-event-registry"
)
RELAY_SCRIPT = EVENT_REGISTRY_DIR / "scripts" / "relay.py"
C2_RUNNER_SCRIPT = EVENT_REGISTRY_DIR / "scripts" / "run_topic6_c2.py"
RUN_CONFIG_STATE_SCRIPT = (
    TOPIC6_DIR
    / "ma-resources"
    / "skills"
    / "topic6-annotation"
    / "scripts"
    / "run_config_state.py"
)
ENVIRONMENT_CONFIG = TOPIC6_DIR / "ma-resources" / "environment.json"
C0_V4_PROMPT = (
    TOPIC6_DIR
    / "ma-resources"
    / "skills"
    / "topic6-annotation"
    / "prompts"
    / "C0_基础事实"
    / "v4.md"
)
C0_V5_PROMPT = C0_V4_PROMPT.with_name("v5.md")
MARKETING_CALENDAR = (
    TOPIC6_DIR
    / "ma-resources"
    / "skills"
    / "topic6-fetch-normalize"
    / "references"
    / "marketing_calendar_2026.csv"
)
E2_NODES_SCRIPT = (
    TOPIC6_DIR
    / "ma-resources"
    / "skills"
    / "topic6-insight"
    / "01_统计"
    / "e2_nodes.py"
)


def _load_annotate_module():
    if "pandas" not in sys.modules and importlib.util.find_spec("pandas") is None:
        sys.modules.setdefault("pandas", types.ModuleType("pandas"))
    spec = importlib.util.spec_from_file_location("topic6_datahub_annotate", ANNOTATE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_relay_module():
    spec = importlib.util.spec_from_file_location("topic6_event_registry_relay", RELAY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_c2_runner_module():
    spec = importlib.util.spec_from_file_location(
        "topic6_event_registry_runner", C2_RUNNER_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_run_config_state_module():
    spec = importlib.util.spec_from_file_location(
        "topic6_run_config_state", RUN_CONFIG_STATE_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_pipeline_f_module():
    spec = importlib.util.spec_from_file_location("topic6_pipeline_f", PIPELINE_F_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_e2_nodes_module():
    script_dir = str(E2_NODES_SCRIPT.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("topic6_e2_nodes", E2_NODES_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeHttpResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self._body


def test_relay_appends_resource_to_versioned_ark_base_url(monkeypatch):
    module = _load_relay_module()
    requested_urls = []

    def fake_urlopen(request, timeout):
        assert timeout == 600
        requested_urls.append(request.full_url)
        return _FakeHttpResponse(
            {
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            }
        )

    monkeypatch.setenv("ARK_BASE_URL", "https://ark.example/api/v3/")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    assert module.Relay("test-model", 10, "system").call("hello") == "ok"
    assert requested_urls == ["https://ark.example/api/v3/chat/completions"]


def test_vision_embedder_uses_multimodal_protocol_one_text_per_request(monkeypatch):
    module = _load_relay_module()
    requested_urls = []
    requested_bodies = []

    def fake_urlopen(request, timeout):
        assert timeout == 600
        requested_urls.append(request.full_url)
        requested_bodies.append(json.loads(request.data))
        return _FakeHttpResponse(
            {"usage": {"input_tokens": 1}, "data": {"embedding": [0.1, 0.2]}}
        )

    monkeypatch.setenv("ARK_BASE_URL", "https://ark.example/api/v3")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    assert module.Embedder().embed(["hello", "world"], batch=64) == [
        [0.1, 0.2],
        [0.1, 0.2],
    ]
    assert requested_urls == [
        "https://ark.example/api/v3/embeddings/multimodal",
        "https://ark.example/api/v3/embeddings/multimodal",
    ]
    assert requested_bodies == [
        {
            "model": "doubao-embedding-vision-251215",
            "input": [{"type": "text", "text": "hello"}],
            "encoding_format": "float",
        },
        {
            "model": "doubao-embedding-vision-251215",
            "input": [{"type": "text", "text": "world"}],
            "encoding_format": "float",
        },
    ]


def test_embedder_keeps_standard_batch_protocol_for_legacy_override(monkeypatch):
    module = _load_relay_module()
    requested_bodies = []

    def fake_urlopen(request, timeout):
        assert timeout == 600
        assert request.full_url == "https://ark.example/api/v3/embeddings"
        requested_bodies.append(json.loads(request.data))
        return _FakeHttpResponse(
            {
                "usage": {"prompt_tokens": 2},
                "data": [
                    {"embedding": [0.1]},
                    {"embedding": [0.2]},
                ],
            }
        )

    monkeypatch.setenv("ARK_BASE_URL", "https://ark.example/api/v3")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    embedder = module.Embedder(model="text-embedding-3-small")
    assert embedder.embed(["hello", "world"], batch=64) == [[0.1], [0.2]]
    assert requested_bodies == [
        {"model": "text-embedding-3-small", "input": ["hello", "world"]}
    ]


def test_extract_result_rows_flattens_nested_data_and_result_alias():
    module = _load_annotate_module()

    rows = module._extract_result_rows(
        {
            "result_list": [
                {
                    "input_data": {"row_id": "T0001", "title": "标题"},
                    "result": {"是否营销可用": "是"},
                }
            ]
        }
    )

    assert rows[0]["row_id"] == "T0001"
    assert rows[0]["title"] == "标题"
    assert rows[0]["llm_result"] == '{"是否营销可用": "是"}'


def test_download_inline_results_reads_paginated_result_list(tmp_path, monkeypatch):
    module = _load_annotate_module()
    written_rows = []

    class _FakeFrame:
        def __init__(self, rows):
            self._rows = rows
            self.columns = set().union(*(row.keys() for row in rows))

        def to_excel(self, path, index=False):
            assert index is False
            written_rows.extend(self._rows)
            path.write_text("written", encoding="utf-8")

    monkeypatch.setattr(
        module, "pd", types.SimpleNamespace(DataFrame=_FakeFrame)
    )
    pages = {
        1: {"result_list": [{"row_id": "T0001", "llm_result": "{}"}]},
        2: {"result_list": [{"row_id": "T0002", "llm_result": "{}"}]},
    }

    monkeypatch.setattr(
        module,
        "_get_task",
        lambda _key, _task_id, *, page=None, page_size=None: pages.get(
            page, {"result_list": []}
        ),
    )
    out = tmp_path / "result.xlsx"
    module._download_inline_results(
        "secret",
        42,
        {"result_list": [{"row_id": "partial", "llm_result": "{}"}]},
        out,
        expected_rows=2,
    )

    assert [row["row_id"] for row in written_rows] == ["T0001", "T0002"]


def test_validate_model_id_prints_list_and_returns_exact_match(capsys):
    module = _load_annotate_module()
    models = [
        {
            "model_id": "Doubao-pro-32k",
            "platform": "Volcengine",
            "input_price": "¥1.2",
            "output_price": "¥3.4",
        },
        {"model_id": "gpt-5.6-luna", "platform": "OpenAI"},
    ]

    info = module._validate_model_id("Doubao-pro-32k", models)

    assert info == {
        "platform": "Volcengine",
        "input_price": 1.2,
        "output_price": 3.4,
    }
    output = capsys.readouterr().out
    assert "/api/v1/model/list: 2 models" in output
    assert "Doubao-pro-32k, gpt-5.6-luna" in output


def test_validate_model_id_suggests_case_sensitive_match():
    module = _load_annotate_module()

    with pytest.raises(
        ValueError,
        match="invalid model_id: doubao-pro-32k; 大小写敏感候选: Doubao-pro-32k",
    ):
        module._validate_model_id(
            "doubao-pro-32k",
            [{"model_id": "Doubao-pro-32k"}],
        )


def test_agent_prompts_use_canonical_model_id_and_allow_one_retry():
    coordinator = COORDINATOR_PROMPT.read_text(encoding="utf-8")
    annotator = ANNOTATOR_PROMPT.read_text(encoding="utf-8")

    assert "`Doubao-Seed-Evolving`" in coordinator
    assert '--model-id "$DATAHUB_MODEL_ID"' in annotator
    assert "C2_CHAT_MODEL_ID" in coordinator
    assert "`doubao-seed-evolving`" in coordinator
    assert "最多重试 1 次" in annotator
    assert "`Doubao-pro-32k`" not in coordinator
    assert "--model-id Doubao-pro-32k" not in annotator


def test_c2_runner_uses_platform_00_then_merged_remaining_stages(tmp_path):
    module = _load_c2_runner_module()
    plan = module.build_stage_plan(
        tmp_path / "run",
        tmp_path / "input.csv",
        "doubao-seed-evolving",
        "doubao-embedding-vision-251215",
    )

    assert [stage for stage, _commands in plan] == [
        "00_platforms",
        "x0_merge",
        "01_eventness",
        "02_frames",
        "03_entities",
        "04_embeddings",
        "05_recall",
        "06_blocks",
        "07_archive",
        "x2_confidence",
        "x3_review",
        "x4_detail",
    ]
    assert len(plan[0][1]) == 4
    platform_commands = " ".join(" ".join(command) for command in plan[0][1])
    assert "01_eventness.py" not in platform_commands
    merged_commands = " ".join(
        " ".join(command) for _stage, commands in plan[2:] for command in commands
    )
    assert "doubao-seed-evolving" in merged_commands
    assert "Doubao-Seed-Evolving" not in merged_commands


def test_c2_runner_resumes_and_invalidates_outputs_when_model_changes(
    tmp_path, monkeypatch
):
    module = _load_c2_runner_module()
    project = tmp_path / "project"
    source = project / "input.xlsx"
    source.parent.mkdir(parents=True)
    source.write_text("source", encoding="utf-8")
    (project / "run_config.yaml").write_text(
        "status:\n  current_phase: c_route_sample\n",
        encoding="utf-8",
    )

    executed = []
    def prepare_input(_source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("normalized-input", encoding="utf-8")
        return 1

    monkeypatch.setattr(module, "_prepare_input", prepare_input)
    monkeypatch.setattr(
        module,
        "build_stage_plan",
        lambda *_args, **_kwargs: [
            ("stage_a", [["stage-a"]]),
            ("stage_b", [["stage-b"]]),
        ],
    )
    monkeypatch.setattr(
        module,
        "_run_stage",
        lambda stage, _commands, _log_dir: executed.append(stage),
    )
    monkeypatch.setattr(
        module,
        "_write_result",
        lambda _run_dir, destination, _rows: destination.write_text(
            "result", encoding="utf-8"
        ),
    )

    def args(chat_model):
        return module.argparse.Namespace(
            project_dir=str(project),
            mode="demo",
            run_id=1,
            input=str(source),
            chat_model=chat_model,
            embedding_model="embedding-v1",
            x2_verdict="sonnet",
        )

    module.run(args("chat-v1"))
    assert executed == ["stage_a", "stage_b"]

    module.run(args("chat-v1"))
    assert executed == ["stage_a", "stage_b"]

    stale = project / "04_标注" / "C2_事件归档" / "c2_run" / "merged" / "stale"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")
    module.run(args("chat-v2"))

    assert executed == ["stage_a", "stage_b", "stage_a", "stage_b"]
    assert not stale.exists()


def test_c0_v5_is_compact_and_keeps_output_contract():
    old_prompt = C0_V4_PROMPT.read_text(encoding="utf-8")
    prompt = C0_V5_PROMPT.read_text(encoding="utf-8")

    assert len(prompt) < len(old_prompt) * 0.4
    for field in (
        "商业实体",
        "热点驱动词",
        "行业归属",
        "营销触发方式",
        "营销维度",
        "平台原生形式",
        "不可用原因",
        "是否营销可用",
        "判断说明",
    ):
        assert field in prompt


def test_run_config_state_deep_merges_parallel_task_results(tmp_path):
    module = _load_run_config_state_module()
    config_path = tmp_path / "run_config.yaml"
    config_path.write_text(
        "mode: demo\nstatus:\n  current_phase: c_route_sample\n"
        "  c0_base:\n    status: done\n",
        encoding="utf-8",
    )

    module.update_run_config(
        tmp_path,
        {"r1_platform": {"status": "done", "row_count": 18}},
    )
    config = module.update_run_config(
        tmp_path,
        {"c2_cluster": {"status": "running", "stage": "01_eventness"}},
    )

    assert config["status"]["current_phase"] == "c_route_sample"
    assert config["status"]["c0_base"]["status"] == "done"
    assert config["status"]["r1_platform"]["row_count"] == 18
    assert config["status"]["c2_cluster"]["stage"] == "01_eventness"


def test_coordinator_uses_50_rows_for_demo_and_500_for_test():
    coordinator = COORDINATOR_PROMPT.read_text(encoding="utf-8")

    assert "mode=test" in coordinator
    assert "sample_500.py --size 500" in coordinator
    assert "mode=demo" in coordinator
    assert "sample_500.py --size 50" in coordinator


def test_coordinator_uses_cross_platform_c2_flow_and_merge_contract():
    coordinator = COORDINATOR_PROMPT.read_text(encoding="utf-8")

    required_steps = [
        "run_topic6_c2.py",
        "四平台并行 `00_clean_titles.py`",
        "`x0_merge_platforms.py`",
        "merged 目录统一执行 `01→02→03→04→05→06→07→x2→x3→x4`",
        "c2_event_result_r{N}.xlsx",
    ]
    assert all(step in coordinator for step in required_steps)
    assert "严禁在 x0 前按平台执行 01~04" in coordinator
    assert "禁止把 `00_seed_from_registry.py` 当成 C2 起点" in coordinator
    assert "`feishu_doc_url` 必须是非空的飞书 `/docx/` URL" in coordinator
    assert "严禁用本地 Markdown 路径代替飞书文档并进入 HC3" in coordinator
    assert "output=`04_标注/c2_raw.jsonl`" not in coordinator


def test_environment_preinstalls_openai_for_insight_pipeline():
    environment = json.loads(ENVIRONMENT_CONFIG.read_text(encoding="utf-8"))

    assert "openai>=1.0" in environment["config"]["packages"]["pip"]
    assert environment["config"]["env"]["DATAHUB_MODEL_ID"] == "Doubao-Seed-Evolving"
    assert environment["config"]["env"]["C2_CHAT_MODEL_ID"] == "doubao-seed-evolving"
    assert (
        environment["config"]["env"]["EMBEDDING_MODEL_ID"]
        == "doubao-embedding-vision-251215"
    )


def test_pipeline_f_does_not_duplicate_date_range_in_period_label():
    module = _load_pipeline_f_module()

    assert module.format_period_display(
        "W39 热点周报 (2026-09-21 ~ 2026-09-27)",
        "2026-09-21",
        "2026-09-27",
    ) == "W39 热点周报 (2026-09-21 ~ 2026-09-27)"


def test_e2_nodes_reads_shared_csv_calendar(monkeypatch):
    module = _load_e2_nodes_module()
    monkeypatch.setattr(module, "CALENDAR_PATH", MARKETING_CALENDAR)
    calendar = dict(
        (name, node_date)
        for node_date, name, _node_type in module._load_calendar_rows()
    )

    assert calendar["世界心脏日"].isoformat() == "2026-09-29"


def test_pipeline_f_marks_demo_report_as_sample_only(tmp_path):
    project = tmp_path / "W39_demo"
    round_dir = project / "06_洞察" / "v1"
    round_dir.mkdir(parents=True)
    for section in ("e1", "e2", "e3", "e4"):
        (round_dir / f"{section}_v1.md").write_text(
            f"# {section}\n内容", encoding="utf-8"
        )

    result = subprocess.run(
        [
            sys.executable,
            str(PIPELINE_F_SCRIPT),
            "--project-dir",
            str(project),
            "--mode",
            "demo",
            "--period-label",
            "2026-W39",
            "--date-start",
            "2026-09-21",
            "--date-end",
            "2026-09-27",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = project / "07_报告" / "热点报告_2026-W39_v1.md"
    assert "基于 50 条分层样本生成" in report.read_text(encoding="utf-8")
