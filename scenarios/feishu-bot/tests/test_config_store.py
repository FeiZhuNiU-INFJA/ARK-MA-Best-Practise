"""P1：配置库 DAO + 参数化 Agent 定义装配单测。"""
from __future__ import annotations

from arkagent.gateway import (
    CapabilityBundle,
    ConfigStore,
    DigitalEmployee,
    FeishuGroupBinding,
    Project,
    build_agent_config,
)
from arkagent.gateway.config_store import MEMORY_CATEGORIES, SYNC_SYNCED


def _store(tmp_path) -> ConfigStore:
    return ConfigStore(db_path=str(tmp_path / "admin.db"))


# ---- capability_bundle ----
def test_bundle_roundtrip_serializes_json_fields(tmp_path):
    store = _store(tmp_path)
    bundle = store.upsert_bundle(
        CapabilityBundle(
            id="",
            name="销售包",
            skills=[{"type": "custom", "skill_id": "s1"}],
            mcp_servers=[{"name": "crm", "url": "https://crm.example"}],
            builtin_tool_toggles={"web_search": True, "web_fetch": False},
            writable_memory_categories=["facts", "decisions"],
        )
    )
    assert bundle.id.startswith("bundle_")
    fetched = store.get_bundle(bundle.id)
    assert fetched is not None
    assert fetched.skills == [{"type": "custom", "skill_id": "s1"}]
    assert fetched.mcp_servers[0]["name"] == "crm"
    assert fetched.builtin_tool_toggles == {"web_search": True, "web_fetch": False}
    assert fetched.writable_memory_categories == ["facts", "decisions"]


def test_bundle_update_keeps_id_and_lists(tmp_path):
    store = _store(tmp_path)
    bundle = store.upsert_bundle(CapabilityBundle(id="", name="a"))
    bundle.name = "b"
    bundle.skills = [{"skill_id": "x"}]
    store.upsert_bundle(bundle)
    all_bundles = store.list_bundles()
    assert len(all_bundles) == 1
    assert all_bundles[0].name == "b"
    assert all_bundles[0].skills == [{"skill_id": "x"}]


# ---- digital_employee ----
def test_employee_roundtrip_and_sync_backfill(tmp_path):
    store = _store(tmp_path)
    emp = store.upsert_employee(
        DigitalEmployee(id="", name="阿J", identity_prompt="你是阿J", model_id="m1")
    )
    assert emp.id.startswith("emp_")
    assert emp.sync_status == "pending"
    # 模拟同步回填。
    emp.ark_agent_id = "agent-123"
    emp.ark_agent_version = "3"
    emp.sync_status = SYNC_SYNCED
    emp.synced_at = 111
    store.upsert_employee(emp)
    fetched = store.get_employee(emp.id)
    assert fetched.ark_agent_id == "agent-123"
    assert fetched.ark_agent_version == "3"
    assert fetched.sync_status == SYNC_SYNCED
    assert fetched.synced_at == 111


def test_employee_default_writable_categories_full(tmp_path):
    # 员工默认不带可写子集（子集在项目/bundle 上）；这里验证 Project 默认全量。
    store = _store(tmp_path)
    project = store.upsert_project(Project(id="", name="项目A"))
    assert project.writable_memory_categories == list(MEMORY_CATEGORIES)


# ---- project ----
def test_project_roundtrip_and_bool_flags(tmp_path):
    store = _store(tmp_path)
    project = store.upsert_project(
        Project(
            id="",
            name="项目A",
            memory_store_id="",
            writable_memory_categories=["facts"],
            reply_uses_topic=False,
            multimodal_enabled=False,
        )
    )
    fetched = store.get_project(project.id)
    assert fetched.reply_uses_topic is False
    assert fetched.multimodal_enabled is False
    assert fetched.markdown_enabled is True
    assert fetched.writable_memory_categories == ["facts"]
    # 懒建 store 回填。
    fetched.memory_store_id = "store-1"
    store.upsert_project(fetched)
    assert store.get_project(project.id).memory_store_id == "store-1"


# ---- feishu_group_binding ----
def test_binding_unique_by_chat(tmp_path):
    store = _store(tmp_path)
    project = store.upsert_project(Project(id="", name="项目A"))
    emp = store.upsert_employee(DigitalEmployee(id="", name="阿J"))
    store.upsert_binding(
        FeishuGroupBinding(
            chat_id="c1",
            project_id=project.id, digital_employee_id=emp.id,
        )
    )
    # 同一 chat_id 再绑不同员工 → 覆盖，不新增。
    emp2 = store.upsert_employee(DigitalEmployee(id="", name="阿K"))
    store.upsert_binding(
        FeishuGroupBinding(
            chat_id="c1",
            project_id=project.id, digital_employee_id=emp2.id,
        )
    )
    bindings = store.list_bindings()
    assert len(bindings) == 1
    assert bindings[0].digital_employee_id == emp2.id
    got = store.get_binding("c1")
    assert got.project_id == project.id
    assert store.get_binding("nope") is None


def test_binding_list_filtered_by_project(tmp_path):
    store = _store(tmp_path)
    p1 = store.upsert_project(Project(id="", name="P1"))
    p2 = store.upsert_project(Project(id="", name="P2"))
    emp = store.upsert_employee(DigitalEmployee(id="", name="阿J"))
    store.upsert_binding(FeishuGroupBinding("c1", p1.id, emp.id))
    store.upsert_binding(FeishuGroupBinding("c2", p1.id, emp.id))
    store.upsert_binding(FeishuGroupBinding("c3", p2.id, emp.id))
    assert len(store.list_bindings(project_id=p1.id)) == 2
    assert len(store.list_bindings(project_id=p2.id)) == 1
    assert len(store.list_bindings()) == 3


def test_binding_cascade_deleted_with_project(tmp_path):
    store = _store(tmp_path)
    project = store.upsert_project(Project(id="", name="P"))
    emp = store.upsert_employee(DigitalEmployee(id="", name="阿J"))
    store.upsert_binding(FeishuGroupBinding("c1", project.id, emp.id))
    store.delete_project(project.id)
    assert store.get_binding("c1") is None


# ---- build_agent_config ----
def test_build_agent_config_minimal_matches_legacy_shape():
    emp = DigitalEmployee(id="emp_1", name="数字员工阿J", identity_prompt="你是阿J", model_id="doubao-seed-evolving")
    config = build_agent_config(emp, None)
    assert config["name"] == "数字员工阿J"
    assert config["model"] == {"id": "doubao-seed-evolving"}
    assert config["system"] == "你是阿J"
    assert config["skills"] == []
    assert "mcp_servers" not in config
    toolset = config["tools"][0]
    assert toolset["type"] == "agent_toolset_20260701"
    assert toolset["default_config"] == {"enabled": True}
    # 默认 web_search / web_fetch 关闭。
    disabled = {c["name"]: c["enabled"] for c in toolset["configs"]}
    assert disabled == {"web_search": False, "web_fetch": False}
    # memory 4 工具追加在后。
    names = [t.get("name") for t in config["tools"] if t.get("type") == "custom"]
    assert names == ["memory_list", "memory_get", "memory_upsert", "memory_forget"]


def test_build_agent_config_with_bundle_wires_skills_mcp_toggles():
    emp = DigitalEmployee(id="emp_2", name="阿J", identity_prompt="sys", model_id="m2")
    bundle = CapabilityBundle(
        id="b1",
        name="包",
        skills=[{"type": "skill_hub", "skill_id": "lark-im"}],
        mcp_servers=[{"name": "crm", "url": "https://crm.example"}],
        builtin_tool_toggles={"web_search": True, "web_fetch": False},
    )
    config = build_agent_config(emp, bundle)
    assert config["model"] == {"id": "m2"}
    assert config["skills"] == [{"type": "skill_hub", "skill_id": "lark-im"}]
    assert config["mcp_servers"] == [{"name": "crm", "url": "https://crm.example"}]
    types = [t["type"] for t in config["tools"]]
    assert "mcp_toolset" in types
    toolset = config["tools"][0]
    toggles = {c["name"]: c["enabled"] for c in toolset["configs"]}
    assert toggles["web_search"] is True
    assert toggles["web_fetch"] is False
    assert config["metadata"]["digital_employee_id"] == "emp_2"
