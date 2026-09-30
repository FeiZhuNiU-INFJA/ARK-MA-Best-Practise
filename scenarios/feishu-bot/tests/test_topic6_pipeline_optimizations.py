from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import httpx
import pandas as pd
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
PIPELINE_E_SCRIPT = PIPELINE_F_SCRIPT.with_name("pipeline_e.py")
COORDINATOR_PROMPT = (
    TOPIC6_DIR / "ma-resources" / "agents" / "coordinator.system.md"
)
PIPELINE_OVERVIEW = TOPIC6_DIR / "topic6_pipeline_overview.html"
PHASE_F_PROMPT = (
    TOPIC6_DIR
    / "ma-resources"
    / "skills"
    / "topic6-annotation"
    / "prompts"
    / "07_阶段_F发布.md"
)
ANNOTATOR_PROMPT = (
    TOPIC6_DIR / "ma-resources" / "agents" / "annotator.system.md"
)
EVENT_REGISTRY_DIR = (
    TOPIC6_DIR / "ma-resources" / "skills" / "topic6-event-registry"
)
RELAY_SCRIPT = EVENT_REGISTRY_DIR / "scripts" / "relay.py"
C2_RUNNER_SCRIPT = EVENT_REGISTRY_DIR / "scripts" / "run_topic6_c2.py"
DEMO_ROUTES_SCRIPT = (
    TOPIC6_DIR
    / "ma-resources"
    / "skills"
    / "topic6-annotation"
    / "scripts"
    / "run_demo_routes.py"
)
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


def _load_demo_routes_module():
    spec = importlib.util.spec_from_file_location(
        "topic6_demo_routes", DEMO_ROUTES_SCRIPT
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


def _load_pipeline_e_module():
    openai_stub = types.ModuleType("openai")
    openai_stub.AsyncOpenAI = object
    old_openai = sys.modules.get("openai")
    old_stdout, old_stderr = sys.stdout, sys.stderr
    fake_stdout = types.SimpleNamespace(buffer=__import__("io").BytesIO())
    fake_stderr = types.SimpleNamespace(buffer=__import__("io").BytesIO())
    sys.modules["openai"] = openai_stub
    try:
        sys.stdout, sys.stderr = fake_stdout, fake_stderr
        spec = importlib.util.spec_from_file_location(
            "topic6_pipeline_e", PIPELINE_E_SCRIPT
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        if old_openai is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = old_openai
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


def test_c2_runner_parallel_stage_passes_command_and_log_path(tmp_path, monkeypatch):
    module = _load_c2_runner_module()
    calls = []
    monkeypatch.setattr(
        module,
        "_run_command",
        lambda command, log_path: calls.append((command, log_path)),
    )

    module._run_stage(
        "00_platforms",
        [["python", "weibo"], ["python", "douyin"]],
        tmp_path,
    )

    assert sorted(command[-1] for command, _path in calls) == ["douyin", "weibo"]
    assert {path.name for _command, path in calls} == {
        "00_platforms_weibo.log",
        "00_platforms_douyin.log",
    }


def test_c2_demo_fast_normalizes_merge_compatible_result():
    module = _load_c2_runner_module()
    records = [
        {"record_id": "a", "platform": "微博", "title": "事件A"},
        {"record_id": "b", "platform": "抖音", "title": "事件A视频"},
    ]

    result = module._normalize_demo_fast_results(
        records,
        {
            "results": [
                {"row_id": "a", "一级事件名": "事件A"},
                {"row_id": "b", "一级事件名": "事件A"},
            ]
        },
    )
    assert result == [
        {"row_id": "a", "一级事件名": "事件A"},
        {"row_id": "b", "一级事件名": "事件A"},
    ]

    with pytest.raises(ValueError, match="missing row_ids"):
        module._normalize_demo_fast_results(
            [
                {"record_id": "a", "platform": "微博", "title": "事件A"},
                {"record_id": "b", "platform": "抖音", "title": "事件B"},
            ],
            {"results": [{"row_id": "a", "一级事件名": "事件A"}]},
        )


def test_c2_demo_fast_rejects_non_demo_mode():
    module = _load_c2_runner_module()
    args = module.argparse.Namespace(mode="full", demo_fast=True)

    with pytest.raises(ValueError, match="only valid with --mode demo"):
        module.run(args)


def test_demo_routes_require_complete_unique_results():
    module = _load_demo_routes_module()
    rows = [{"row_id": "a"}, {"row_id": "b"}]

    normalized = module._normalize_results(
        "r2",
        rows,
        [
            {"row_id": "a", "是否商业合作": "是", "判断说明": "存在合作"},
            {"row_id": "b", "是否商业合作": "否", "判断说明": "无双边关系"},
        ],
    )
    assert [row["r2_是否商业合作"] for row in normalized] == ["是", "否"]

    with pytest.raises(ValueError, match="missing or invalid row_ids"):
        module._normalize_results(
            "r2",
            rows,
            [{"row_id": "a", "是否商业合作": "是", "判断说明": "存在合作"}],
        )

    with pytest.raises(ValueError, match="duplicate row_id"):
        module._normalize_results(
            "r2",
            rows,
            [
                {"row_id": "a", "是否商业合作": "是", "判断说明": "存在合作"},
                {"row_id": "a", "是否商业合作": "否", "判断说明": "重复"},
            ],
        )


def test_demo_routes_retry_disconnect_429_and_5xx(monkeypatch):
    module = _load_demo_routes_module()
    route = module.ROUTES["r2"]
    rows = [{"row_id": "a"}]
    request = httpx.Request(
        "POST", "https://ark.example/chat/completions"
    )
    outcomes = [
        httpx.RemoteProtocolError("remote closed"),
        httpx.Response(
            429,
            text="rate limited",
            request=request,
        ),
        httpx.Response(
            503,
            text="unavailable",
            request=request,
        ),
        httpx.Response(
            200,
            json={
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "results": [
                                        {
                                            "row_id": "a",
                                            "是否商业合作": "是",
                                            "判断说明": "存在合作",
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        },
                    }
                ],
            },
            request=request,
        ),
    ]
    clients = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            self.posts = []
            clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def post(self, url, **kwargs):
            self.posts.append((url, kwargs))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(module.httpx, "Client", FakeClient)
    monkeypatch.setattr(module.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(module.random, "uniform", lambda _start, _end: 0)

    result = module._request_route(
        "r2",
        route,
        rows,
        model="doubao-seed-evolving",
        base_url="https://ark.example",
        api_key="test-key",
        max_attempts=4,
    )

    assert len(clients) == 4
    assert all(client.closed for client in clients)
    assert all(len(client.posts) == 1 for client in clients)
    assert all(client.kwargs["http2"] is False for client in clients)
    assert all(
        client.posts[0][1]["headers"]["Connection"] == "close"
        for client in clients
    )
    assert result["normalized"][0]["r2_是否商业合作"] == "是"
    assert result["input_tokens"] == 10


def test_demo_routes_persist_completed_routes_and_resume_missing_chunk(
    tmp_path, monkeypatch
):
    module = _load_demo_routes_module()
    project = tmp_path / "project"
    source = project / "usable.xlsx"
    project.mkdir()
    input_frame = pd.DataFrame(
        [
            {"row_id": "a", "platform": "微博", "title": "A", "desc": "A desc"},
            {"row_id": "b", "platform": "抖音", "title": "B", "desc": "B desc"},
            {"row_id": "c", "platform": "小红书", "title": "C", "desc": "C desc"},
            {"row_id": "d", "platform": "微信", "title": "D", "desc": "D desc"},
        ]
    )
    source.write_bytes(b"stable input signature")

    def fake_atomic_excel(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_json(force_ascii=False), encoding="utf-8")

    monkeypatch.setattr(module.pd, "read_excel", lambda _path: input_frame.copy())
    monkeypatch.setattr(module, "_atomic_excel", fake_atomic_excel)
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.example")
    monkeypatch.setattr(module, "_record_cost", lambda *_args, **_kwargs: None)

    failed_once = False
    first_calls = []

    def fake_request(route_name, route, rows, **_kwargs):
        nonlocal failed_once
        row_ids = tuple(row["row_id"] for row in rows)
        first_calls.append((route_name, row_ids))
        if route_name == "r2" and row_ids == ("c", "d") and not failed_once:
            failed_once = True
            raise RuntimeError("simulated disconnect")
        return {
            "route": route_name,
            "normalized": [
                {
                    "row_id": row["row_id"],
                    f"{route_name}_{route['label']}": "是",
                    f"{route_name}_判断说明": "测试判断",
                    f"{route_name}_parse_error": 0,
                }
                for row in rows
            ],
            "input_tokens": len(rows) * 10,
            "output_tokens": len(rows) * 2,
        }

    monkeypatch.setattr(module, "_request_route", fake_request)

    with pytest.raises(RuntimeError, match="rerun to resume"):
        module.run(
            str(project),
            str(source),
            "doubao-seed-evolving",
            1,
            batch_size=2,
            max_workers=2,
        )

    for route_name in ("r1", "r3", "r4", "r5"):
        paths = module._task_paths(project, route_name, 1)
        assert paths["raw"].exists()
        assert paths["post"].exists()
        assert json.loads(paths["meta"].read_text())["status"] == "pass"

    r2_checkpoint = json.loads(
        module._task_paths(project, "r2", 1)["checkpoint"].read_text()
    )
    assert set(r2_checkpoint["chunks"]) == {"0"}
    assert not module._task_paths(project, "r2", 1)["meta"].exists()

    resumed_calls = []

    def resumed_request(route_name, route, rows, **kwargs):
        resumed_calls.append((route_name, tuple(row["row_id"] for row in rows)))
        return fake_request(route_name, route, rows, **kwargs)

    monkeypatch.setattr(module, "_request_route", resumed_request)
    result = module.run(
        str(project),
        str(source),
        "doubao-seed-evolving",
        1,
        batch_size=2,
        max_workers=2,
    )

    assert resumed_calls == [("r2", ("c", "d"))]
    assert list(result) == list(module.ROUTES)
    assert result["r2"]["rows"] == 4
    assert result["r1"]["cached"] is True
    r2_meta = json.loads(module._task_paths(project, "r2", 1)["meta"].read_text())
    assert r2_meta["batch_size"] == 2
    assert r2_meta["chunk_count"] == 2
    assert r2_meta["input_tokens"] == 40


def test_pipeline_e_streams_with_fresh_client_and_retries_disconnect(monkeypatch):
    module = _load_pipeline_e_module()
    clients = []
    create_kwargs = []

    class _Stream:
        def __aiter__(self):
            chunks = [
                types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            delta=types.SimpleNamespace(content="洞察"),
                            finish_reason=None,
                        )
                    ],
                    usage=None,
                ),
                types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            delta=types.SimpleNamespace(content="完成"),
                            finish_reason="stop",
                        )
                    ],
                    usage=types.SimpleNamespace(
                        prompt_tokens=12,
                        completion_tokens=3,
                    ),
                ),
            ]
            return _AsyncIterator(chunks)

    class _AsyncIterator:
        def __init__(self, values):
            self._values = iter(values)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._values)
            except StopIteration as error:
                raise StopAsyncIteration from error

    class _Completions:
        def __init__(self, attempt):
            self._attempt = attempt

        async def create(self, **kwargs):
            create_kwargs.append(kwargs)
            if self._attempt == 1:
                raise ConnectionError("remote closed")
            return _Stream()

    class _Client:
        def __init__(self, attempt):
            self.chat = types.SimpleNamespace(
                completions=_Completions(attempt)
            )
            self.closed = False

        async def close(self):
            self.closed = True

    def fake_client(**kwargs):
        assert kwargs == {
            "api_key": "test-key",
            "base_url": "https://ark.example/api/v3",
            "max_retries": 0,
            "timeout": 600.0,
        }
        client = _Client(len(clients) + 1)
        clients.append(client)
        return client

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(module, "AsyncOpenAI", fake_client)
    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(module.random, "uniform", lambda _start, _end: 0)

    result = asyncio.run(
        module.call_llm_async(
            "test-key",
            "https://ark.example/api/v3",
            {"id": "e1", "name": "行业话题", "max_tokens": 128},
            "prompt",
            "doubao-seed-evolving",
            max_attempts=2,
        )
    )

    assert result["status"] == "ok"
    assert result["content"] == "洞察完成"
    assert result["input_tokens"] == 12
    assert result["output_tokens"] == 3
    assert result["stop_reason"] == "stop"
    assert len(clients) == 2
    assert all(client.closed for client in clients)
    assert all(kwargs["stream"] is True for kwargs in create_kwargs)
    assert all(
        kwargs["stream_options"] == {"include_usage": True}
        for kwargs in create_kwargs
    )


def test_pipeline_e_checkpoints_each_section_and_resumes_only_failure(
    tmp_path, monkeypatch
):
    module = _load_pipeline_e_module()
    insight_dir = tmp_path / "06_洞察" / "v1"
    insight_dir.mkdir(parents=True)
    (insight_dir / "e1_data.md").write_text("E1 data", encoding="utf-8")
    (insight_dir / "e3_data.md").write_text("E3 data", encoding="utf-8")
    checkpoint_path = insight_dir / "pipeline_e_checkpoint_v1.json"
    checkpoint = {
        "checkpoint_version": module.CHECKPOINT_VERSION,
        "run_signature": "test-signature",
        "model": "test-model",
        "mode": "demo",
        "version": "v1",
        "sections": {},
    }
    sections = [
        {
            "id": "e1",
            "name": "E1",
            "prompt_dir": "E1",
            "data_file": "e1_data.md",
            "section_title": "# E1",
            "max_tokens": 128,
            "output_file": "e1_v1.md",
        },
        {
            "id": "e3",
            "name": "E3",
            "prompt_dir": "E3",
            "data_file": "e3_data.md",
            "section_title": "# E3",
            "max_tokens": 128,
            "output_file": "e3_v1.md",
        },
    ]
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.example")
    monkeypatch.setattr(module, "load_prompt_template", lambda _name: "{{data}}")

    output_written = asyncio.Event()
    original_write = module._write_section_output

    def observed_write(sec, result, output_path):
        original_write(sec, result, output_path)
        if sec["id"] == "e1":
            output_written.set()

    calls = []
    active = 0
    max_active = 0

    async def first_call(_key, _url, sec, _prompt, model, *, max_attempts):
        nonlocal active, max_active
        assert model == "test-model"
        assert max_attempts == 3
        calls.append(sec["id"])
        active += 1
        max_active = max(max_active, active)
        try:
            if sec["id"] == "e3":
                await output_written.wait()
                saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                assert saved["sections"]["e1"]["status"] == "ok"
                return {
                    "id": "e3",
                    "name": "E3",
                    "status": "error",
                    "error": "disconnect",
                    "content": "",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "elapsed_s": 0.1,
                    "model": model,
                    "stop_reason": None,
                }
            await asyncio.sleep(0)
            return {
                "id": "e1",
                "name": "E1",
                "status": "ok",
                "content": "E1 result",
                "input_tokens": 10,
                "output_tokens": 2,
                "elapsed_s": 0.1,
                "model": model,
                "stop_reason": "stop",
            }
        finally:
            active -= 1

    monkeypatch.setattr(module, "_write_section_output", observed_write)
    monkeypatch.setattr(module, "call_llm_async", first_call)
    first_results = asyncio.run(
        module.run_all_sections(
            tmp_path,
            insight_dir,
            "test-model",
            sections,
            checkpoint=checkpoint,
            checkpoint_path=checkpoint_path,
            max_workers=2,
            max_attempts=3,
        )
    )

    assert [result["status"] for result in first_results] == ["ok", "error"]
    assert (insight_dir / "e1_v1.md").exists()
    assert not (insight_dir / "e3_v1.md").exists()
    assert max_active == 2

    resumed_calls = []

    async def resumed_call(_key, _url, sec, _prompt, model, *, max_attempts):
        resumed_calls.append(sec["id"])
        return {
            "id": sec["id"],
            "name": sec["name"],
            "status": "ok",
            "content": f"{sec['id']} resumed",
            "input_tokens": 8,
            "output_tokens": 2,
            "elapsed_s": 0.1,
            "model": model,
            "stop_reason": "stop",
        }

    monkeypatch.setattr(module, "call_llm_async", resumed_call)
    resumed = asyncio.run(
        module.run_all_sections(
            tmp_path,
            insight_dir,
            "test-model",
            sections,
            checkpoint=checkpoint,
            checkpoint_path=checkpoint_path,
            max_workers=2,
            max_attempts=3,
        )
    )

    assert resumed_calls == ["e3"]
    assert resumed[0]["cached"] is True
    assert resumed[1]["status"] == "ok"
    assert (insight_dir / "e3_v1.md").exists()
    saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert set(saved["sections"]) == {"e1", "e3"}


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


def test_coordinator_uses_50_rows_for_demo_and_500_for_full():
    coordinator = COORDINATOR_PROMPT.read_text(encoding="utf-8")

    assert "mode=full" in coordinator
    assert "sample_500.py --size 500" in coordinator
    assert "mode=demo" in coordinator
    assert "sample_500.py --size 50" in coordinator
    assert "mode=skip_sampling" in coordinator


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


def test_coordinator_uses_demo_fast_paths_and_single_insight_entry():
    coordinator = COORDINATOR_PROMPT.read_text(encoding="utf-8")

    assert "run_demo_routes.py" in coordinator
    assert "--mode demo --run-id {N} --demo-fast" in coordinator
    assert "C2_事件归档/c2_run/c2_status.json" in coordinator
    assert "不再委派 4 个 `topic6-insighter`" in coordinator
    assert "--publish-date \"{publish_date}\" --version {N}" in coordinator
    assert "最多重跑一次同一正式入口" in coordinator
    assert "禁止用 inline Python" in coordinator
    assert "禁止精简或改写 R1~R5 Prompt" in coordinator
    assert "流式有限并发、断连重试和版块级 checkpoint" in coordinator
    assert "最多\n  原参数重跑一次上述同一正式入口" in coordinator
    assert "禁止拆成\n  四次 `--sections` 调用" in coordinator


def test_pipeline_overview_documents_demo_execution_differences():
    overview = PIPELINE_OVERVIEW.read_text(encoding="utf-8")

    assert "demo / full / skip_sampling 模式" in overview
    assert "分层抽样 50 条" in overview
    assert "R1~R5 Ark 批量 + C2 单次归并" in overview
    assert "跳过全量与 HC2" in overview
    assert "run_demo_routes.py" in overview
    assert "run_topic6_c2.py --mode demo --demo-fast" in overview
    assert "Phase E · 单入口调度" in overview
    assert "不再委派 4 个 Insighter" in overview


def test_phase_f_uses_runtime_identity_and_response_url_without_secret_output():
    coordinator = COORDINATOR_PROMPT.read_text(encoding="utf-8")
    prompt = PHASE_F_PROMPT.read_text(encoding="utf-8")

    assert 'OPERATOR_OID="${FEISHU_USER_OPEN_ID:-}"' in prompt
    assert 'x.get("data", {}).get("folder_token")' in prompt
    assert 'x.get("url") or x.get("data", {}).get("url")' in prompt
    assert "docs:document.media:upload" in prompt
    assert "docs:document:import" in prompt
    assert "docs:permission.member:create" in prompt
    assert "docs:permission.member:transfer" in prompt
    assert "docs:permission.member:retrieve" in prompt
    assert "--member-type openid --member-id \"$OPERATOR_OID\"" in prompt
    assert "--perm edit --as bot --yes" in prompt
    assert "严禁执行 `lark-cli auth login`" in prompt
    assert "降级为 `--as user`" in prompt
    assert "任一步失败不得输出 HC3" in coordinator
    assert "FEISHU_HOTREPORT_FOLDER_TOKEN" not in prompt
    assert "CC_SESSION_KEY" not in prompt
    assert "bluefocus.feishu.cn" not in prompt
    assert "禁止用 `env`、`printenv`、`set`、`export -p`" in coordinator
    assert "禁止开启 `set -x`" in prompt


def test_environment_preinstalls_openai_for_insight_pipeline():
    environment = json.loads(ENVIRONMENT_CONFIG.read_text(encoding="utf-8"))

    assert "openai>=1.0" in environment["config"]["packages"]["pip"]
    assert "httpx>=0.27" in environment["config"]["packages"]["pip"]
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
