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
ENVIRONMENT_CONFIG = TOPIC6_DIR / "ma-resources" / "environment.json"
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
    assert "--model-id Doubao-Seed-Evolving" in annotator
    assert "最多重试 1 次" in annotator
    assert "`Doubao-pro-32k`" not in coordinator
    assert "--model-id Doubao-pro-32k" not in annotator


def test_coordinator_uses_50_rows_for_demo_and_500_for_test():
    coordinator = COORDINATOR_PROMPT.read_text(encoding="utf-8")

    assert "mode=test" in coordinator
    assert "sample_500.py --size 500" in coordinator
    assert "mode=demo" in coordinator
    assert "sample_500.py --size 50" in coordinator


def test_coordinator_uses_cross_platform_c2_flow_and_merge_contract():
    coordinator = COORDINATOR_PROMPT.read_text(encoding="utf-8")

    required_steps = [
        "00_clean_titles.py",
        "04_build_embeddings.py --model doubao-embedding-vision-251215",
        "x0_merge_platforms.py",
        "05_recall_candidates.py --top-k 60",
        "x2_confidence_filter.py",
        "x3_review_bidirectional.py",
        "x4_detail_table.py",
        "c2_event_result_r{N}.xlsx",
    ]
    assert all(step in coordinator for step in required_steps)
    assert "不得使用已失效的 `Doubao-embedding` 模型名" in coordinator
    assert "禁止把 `00_seed_from_registry.py` 当成 C2 起点" in coordinator
    assert "`feishu_doc_url` 必须是非空的飞书 `/docx/` URL" in coordinator
    assert "严禁用本地 Markdown 路径代替飞书文档并进入 HC3" in coordinator
    assert "output=`04_标注/c2_raw.jsonl`" not in coordinator


def test_environment_preinstalls_openai_for_insight_pipeline():
    environment = json.loads(ENVIRONMENT_CONFIG.read_text(encoding="utf-8"))

    assert "openai>=1.0" in environment["config"]["packages"]["pip"]


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
