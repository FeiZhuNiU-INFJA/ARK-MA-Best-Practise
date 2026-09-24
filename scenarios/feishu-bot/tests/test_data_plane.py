"""P3：数据面 MADataPlane 单测（路由 / 作用域 / 可写子集 / 回复策略）。

MADataPlane 的路由与作用域方法是纯 ConfigStore 查询，无需 mock 方舟 REST；用轻量消息桩即可。
"""
from __future__ import annotations

from dataclasses import dataclass

from arkagent.ark import ArkClient
from arkagent.gateway import (
    ConfigStore,
    DigitalEmployee,
    FeishuGroupBinding,
    MADataPlane,
    Project,
    RuntimeConfig,
    ScopeRef,
)
from arkagent.gateway.config_store import MEMORY_CATEGORIES


@dataclass
class _Msg:
    """满足 MADataPlane._MessageLike 形状的最小消息桩。"""

    chat_type: str
    tenant_key: str
    chat_id: str
    user_id: str = ""
    user_open_id: str = ""

    @property
    def employee_id(self) -> str:
        return self.user_id or self.user_open_id


def _plane(tmp_path, **kwargs) -> tuple[MADataPlane, ConfigStore]:
    store = ConfigStore(db_path=str(tmp_path / "admin.db"))
    ark = ArkClient("key", "https://ark.test/api/v3")
    plane = MADataPlane(
        ark,
        store,
        default_agent_id="agent-default",
        default_environment_id="env-default",
        **kwargs,
    )
    return plane, store


def _bind_project(store: ConfigStore, **project_kwargs) -> tuple[Project, DigitalEmployee]:
    emp = store.upsert_employee(
        DigitalEmployee(id="", name="阿J", ark_agent_id="agent-emp")
    )
    proj = store.upsert_project(Project(id="", name="项目A", **project_kwargs))
    store.upsert_binding(
        FeishuGroupBinding(
            tenant_key="t-1",
            chat_id="oc-1",
            project_id=proj.id,
            digital_employee_id=emp.id,
        )
    )
    return proj, emp


# ---- resolve_runtime_config -------------------------------------------------


def test_resolve_runtime_config_unbound_falls_back_to_env_default(tmp_path):
    plane, _store = _plane(tmp_path)
    runtime = plane.resolve_runtime_config("t-1", "oc-unbound")
    assert runtime.bound is False
    assert runtime.ark_agent_id == "agent-default"
    assert runtime.ark_environment_id == "env-default"
    assert runtime.reply_uses_topic is True
    assert runtime.writable_categories == tuple(MEMORY_CATEGORIES)


def test_resolve_runtime_config_hits_binding(tmp_path):
    plane, store = _plane(tmp_path)
    proj, _emp = _bind_project(
        store,
        memory_store_id="ms-proj",
        reply_uses_topic=False,
        writable_memory_categories=["facts", "decisions"],
    )
    runtime = plane.resolve_runtime_config("t-1", "oc-1")
    assert runtime.bound is True
    assert runtime.ark_agent_id == "agent-emp"  # persona 的 Agent，不是 env 默认
    assert runtime.project_id == proj.id
    assert runtime.memory_store_id == "ms-proj"
    assert runtime.reply_uses_topic is False
    assert runtime.writable_categories == ("facts", "decisions")


def test_resolve_runtime_config_binding_missing_agent_falls_back(tmp_path):
    plane, store = _plane(tmp_path)
    emp = store.upsert_employee(DigitalEmployee(id="", name="未同步"))  # 无 ark_agent_id
    proj = store.upsert_project(Project(id="", name="P"))
    store.upsert_binding(
        FeishuGroupBinding("oc-1", proj.id, emp.id)
    )
    runtime = plane.resolve_runtime_config("t-1", "oc-1")
    assert runtime.bound is True
    assert runtime.ark_agent_id == "agent-default"  # persona 尚未同步 → 回退 env 默认


# ---- resolve_memory_scope ---------------------------------------------------


def test_resolve_memory_scope_group_uses_project_id(tmp_path):
    plane, store = _plane(tmp_path)
    proj, _emp = _bind_project(store)
    msg = _Msg("group", "t-1", "oc-1")
    runtime = plane.resolve_runtime_config("t-1", "oc-1")
    scope = plane.resolve_memory_scope(msg, runtime)
    assert scope == ScopeRef("t-1", "group", proj.id)  # 群作用域用 project_id


def test_resolve_memory_scope_two_groups_same_project_share(tmp_path):
    plane, store = _plane(tmp_path)
    proj, emp = _bind_project(store)
    # 第二个群绑到同一项目 + 同一 persona。
    store.upsert_binding(FeishuGroupBinding("oc-2", proj.id, emp.id))
    r1 = plane.resolve_runtime_config("t-1", "oc-1")
    r2 = plane.resolve_runtime_config("t-1", "oc-2")
    s1 = plane.resolve_memory_scope(_Msg("group", "t-1", "oc-1"), r1)
    s2 = plane.resolve_memory_scope(_Msg("group", "t-1", "oc-2"), r2)
    assert s1 == s2  # 同项目多群 → 同一群记忆作用域


def test_resolve_memory_scope_group_unbound_uses_chat_id(tmp_path):
    plane, _store = _plane(tmp_path)
    runtime = plane.resolve_runtime_config("t-1", "oc-x")  # 未绑定
    scope = plane.resolve_memory_scope(_Msg("group", "t-1", "oc-x"), runtime)
    assert scope == ScopeRef("t-1", "group", "oc-x")  # 回退一群一记忆


def test_resolve_memory_scope_p2p_uses_employee_id(tmp_path):
    plane, _store = _plane(tmp_path)
    msg = _Msg("p2p", "t-1", "", user_id="user-42")
    scope = plane.resolve_memory_scope(msg, None)
    assert scope == ScopeRef("t-1", "user", "user-42")


# ---- writable_categories_for_scope -----------------------------------------


def test_writable_categories_group_uses_project_subset(tmp_path):
    plane, store = _plane(tmp_path)
    proj = store.upsert_project(
        Project(id="", name="P", writable_memory_categories=["conventions"])
    )
    scope = ScopeRef("t-1", "group", proj.id)
    assert plane.writable_categories_for_scope(scope) == ("conventions",)


def test_writable_categories_user_scope_is_all(tmp_path):
    plane, _store = _plane(tmp_path)
    scope = ScopeRef("t-1", "user", "user-1")
    assert plane.writable_categories_for_scope(scope) == tuple(MEMORY_CATEGORIES)


def test_writable_categories_group_without_project_is_all(tmp_path):
    plane, _store = _plane(tmp_path)
    scope = ScopeRef("t-1", "group", "oc-unbound")  # scope_id 非 project → 查不到
    assert plane.writable_categories_for_scope(scope) == tuple(MEMORY_CATEGORIES)


def test_writable_categories_filters_invalid_entries(tmp_path):
    plane, store = _plane(tmp_path)
    proj = store.upsert_project(
        Project(id="", name="P", writable_memory_categories=["facts", "garbage"])
    )
    scope = ScopeRef("t-1", "group", proj.id)
    assert plane.writable_categories_for_scope(scope) == ("facts",)


# ---- decide_reply_strategy --------------------------------------------------


def test_decide_reply_strategy_topic_on_off(tmp_path):
    plane, store = _plane(tmp_path)
    _bind_project(store, reply_uses_topic=True)
    on = plane.resolve_runtime_config("t-1", "oc-1")
    assert plane.decide_reply_strategy(on) == "thread"

    assert (
        plane.decide_reply_strategy(
            RuntimeConfig(ark_agent_id="a", ark_environment_id="e", reply_uses_topic=False)
        )
        == "chat"
    )
