"""P2：控制面 MAControlPlane 单测（respx mock 方舟 REST API）。"""
from __future__ import annotations

import httpx
import respx

from arkagent.ark import ArkClient
from arkagent.gateway import (
    ConfigStore,
    DigitalEmployee,
    MAControlPlane,
    Project,
)
from arkagent.gateway.config_store import SYNC_FAILED, SYNC_SYNCED

BASE = "https://ark.test/api/v3"


def _plane(tmp_path, **kwargs) -> tuple[MAControlPlane, ConfigStore, ArkClient]:
    store = ConfigStore(db_path=str(tmp_path / "admin.db"))
    ark = ArkClient("key", BASE)
    return MAControlPlane(ark, store, **kwargs), store, ark


@respx.mock
async def test_sync_employee_creates_agent_and_backfills(tmp_path):
    route = respx.post(f"{BASE}/agents").mock(
        return_value=httpx.Response(200, json={"data": {"id": "agent-new", "name": "阿J", "version": 1}})
    )
    plane, store, ark = _plane(tmp_path)
    emp = store.upsert_employee(
        DigitalEmployee(id="", name="阿J", identity_prompt="你是阿J", model_id="m1")
    )
    synced = await plane.sync_employee(emp)
    await ark.aclose()

    assert route.called
    # 请求体带上装配好的 Agent 定义。
    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert body["system"] == "你是阿J"
    assert body["model"] == {"id": "m1"}
    # 回填 id/version/status。
    assert synced.ark_agent_id == "agent-new"
    assert synced.ark_agent_version == "1"
    assert synced.sync_status == SYNC_SYNCED
    assert store.get_employee(emp.id).ark_agent_id == "agent-new"


@respx.mock
async def test_sync_employee_updates_with_current_version(tmp_path):
    respx.get(f"{BASE}/agents/agent-1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "agent-1", "version": 4}})
    )
    update = respx.post(f"{BASE}/agents/agent-1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "agent-1", "version": 5}})
    )
    plane, store, ark = _plane(tmp_path)
    emp = store.upsert_employee(
        DigitalEmployee(id="", name="阿J", identity_prompt="sys", ark_agent_id="agent-1")
    )
    synced = await plane.sync_employee(emp)
    await ark.aclose()

    assert update.called
    import json

    body = json.loads(update.calls[0].request.content)
    assert body["version"] == 4  # 带当前 version 做乐观并发校验
    assert synced.ark_agent_version == "5"
    assert synced.sync_status == SYNC_SYNCED


@respx.mock
async def test_sync_employee_marks_failed_on_error(tmp_path):
    respx.post(f"{BASE}/agents").mock(return_value=httpx.Response(500, text="boom"))
    plane, store, ark = _plane(tmp_path)
    emp = store.upsert_employee(DigitalEmployee(id="", name="阿J", identity_prompt="s"))
    raised = False
    try:
        await plane.sync_employee(emp)
    except Exception:
        raised = True
    await ark.aclose()

    assert raised
    persisted = store.get_employee(emp.id)
    assert persisted.sync_status == SYNC_FAILED
    assert persisted.sync_error


@respx.mock
async def test_sync_project_memory_lazy_creates_store(tmp_path):
    route = respx.post(f"{BASE}/memory_stores").mock(
        return_value=httpx.Response(200, json={"data": {"id": "store-xyz"}})
    )
    plane, store, ark = _plane(tmp_path)
    project = store.upsert_project(Project(id="", name="项目A"))
    synced = await plane.sync_project_memory(project)
    assert synced.memory_store_id == "store-xyz"
    assert store.get_project(project.id).memory_store_id == "store-xyz"

    # 已有 store_id 时幂等：不再发第二次请求。
    await plane.sync_project_memory(synced)
    await ark.aclose()
    assert route.call_count == 1


async def test_build_agent_config_auto_loads_bundle(tmp_path):
    from arkagent.gateway import CapabilityBundle

    plane, store, ark = _plane(tmp_path)
    bundle = store.upsert_bundle(
        CapabilityBundle(id="", name="包", skills=[{"skill_id": "lark-im"}])
    )
    emp = DigitalEmployee(id="emp_x", name="阿J", identity_prompt="s", bundle_id=bundle.id)
    config = plane.build_agent_config(emp)
    await ark.aclose()
    assert config["skills"] == [{"skill_id": "lark-im"}]


async def test_ensure_environment_requires_provisioner(tmp_path):
    plane, _store, ark = _plane(tmp_path)
    raised = False
    try:
        await plane.ensure_environment("app-1")
    except RuntimeError as error:
        raised = "env_provisioner" in str(error)
    await ark.aclose()
    assert raised


async def test_ensure_environment_delegates_to_injected(tmp_path):
    calls = {}

    async def fake_env(ark, app_id, name_hint):
        calls["args"] = (app_id, name_hint)
        return "env-123"

    plane, _store, ark = _plane(tmp_path, env_provisioner=fake_env)
    env_id = await plane.ensure_environment("app-1", "hint")
    await ark.aclose()
    assert env_id == "env-123"
    assert calls["args"] == ("app-1", "hint")
