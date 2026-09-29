from __future__ import annotations

import importlib.util
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


def _load_annotate_module():
    if "pandas" not in sys.modules and importlib.util.find_spec("pandas") is None:
        sys.modules.setdefault("pandas", types.ModuleType("pandas"))
    spec = importlib.util.spec_from_file_location("topic6_datahub_annotate", ANNOTATE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
