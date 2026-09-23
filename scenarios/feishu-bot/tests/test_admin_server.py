"""P4：管理后端 API（admin/server.py）单测。

用 starlette TestClient 走 HTTP，注入 FakeArk 的 MAControlPlane，避免真连方舟。
覆盖：CRUD（员工/项目/绑定/能力包）、记忆透传（列/增删改，含未建 Store 的 409）、
同步触发、token 鉴权、总览。
"""
from __future__ import annotations

import sys
from pathlib import Path

from starlette.testclient import TestClient

from arkagent.gateway import ConfigStore, MAControlPlane

_ADMIN_DIR = Path(__file__).resolve().parents[1] / "cases" / "digital-employee" / "admin"
if str(_ADMIN_DIR) not in sys.path:
    sys.path.insert(0, str(_ADMIN_DIR))

from server import create_app  # noqa: E402


class FakeArk:
    """只实现管理后端会透传/调用到的 ArkClient 子集。"""

    def __init__(self) -> None:
        self.stores_created = 0
        # store_id -> {memory_id: {path, content}}
        self._mem: dict[str, dict[str, dict]] = {}
        self._seq = 0
        self.created_agents: list[dict] = []

    async def create_memory_store(self, name: str, description: str) -> str:
        self.stores_created += 1
        sid = f"store-{self.stores_created}"
        self._mem[sid] = {}
        return sid

    async def list_memories(self, store_id, path_prefix="/", depth=2):
        return [
            {"id": mid, "path": item["path"], "type": "text", "content_sha256": ""}
            for mid, item in self._mem.get(store_id, {}).items()
        ]

    async def create_memory(self, store_id, path, content):
        self._seq += 1
        mid = f"mem-{self._seq}"
        self._mem.setdefault(store_id, {})[mid] = {"path": path, "content": content}
        return {"id": mid, "path": path, "content": content, "content_sha256": ""}

    async def get_memory(self, store_id, memory_id):
        item = self._mem[store_id][memory_id]
        return {"id": memory_id, "path": item["path"], "content": item["content"], "content_sha256": ""}

    async def update_memory(self, store_id, memory_id, *, path=None, content=None):
        item = self._mem[store_id][memory_id]
        if path is not None:
            item["path"] = path
        if content is not None:
            item["content"] = content
        return {"id": memory_id, "path": item["path"], "content": item["content"], "content_sha256": ""}

    async def delete_memory(self, store_id, memory_id):
        self._mem[store_id].pop(memory_id, None)

    async def create_agent(self, config):
        self.created_agents.append(config)
        return {"id": f"agent-{len(self.created_agents)}", "version": 1}


def _client(tmp_path, *, token: str = "") -> tuple[TestClient, ConfigStore, FakeArk]:
    store = ConfigStore(db_path=str(tmp_path / "admin.db"))
    ark = FakeArk()
    plane = MAControlPlane(ark, store)  # type: ignore[arg-type]
    app = create_app(store=store, plane=plane, token=token, serve_static=False)
    return TestClient(app), store, ark


# ---- CRUD -----------------------------------------------------------------


def test_employee_crud(tmp_path):
    client, _store, _ark = _client(tmp_path)
    # 建
    resp = client.post("/api/employees", json={"name": "阿J", "identity_prompt": "你是阿J"})
    assert resp.status_code == 201
    emp = resp.json()
    assert emp["name"] == "阿J" and emp["id"]
    emp_id = emp["id"]
    # 列
    assert any(e["id"] == emp_id for e in client.get("/api/employees").json()["employees"])
    # 读
    assert client.get(f"/api/employees/{emp_id}").json()["identity_prompt"] == "你是阿J"
    # 改
    upd = client.put(f"/api/employees/{emp_id}", json={"identity_prompt": "改了"})
    assert upd.json()["identity_prompt"] == "改了"
    # 删
    assert client.delete(f"/api/employees/{emp_id}").json()["deleted"] is True
    assert client.get(f"/api/employees/{emp_id}").status_code == 404


def test_employee_create_requires_name(tmp_path):
    client, _store, _ark = _client(tmp_path)
    assert client.post("/api/employees", json={"name": ""}).status_code == 400


def test_project_crud_with_switches(tmp_path):
    client, _store, _ark = _client(tmp_path)
    resp = client.post(
        "/api/projects",
        json={"name": "项目A", "reply_uses_topic": False, "writable_memory_categories": ["facts"]},
    )
    assert resp.status_code == 201
    proj = resp.json()
    assert proj["reply_uses_topic"] is False
    assert proj["writable_memory_categories"] == ["facts"]
    pid = proj["id"]
    upd = client.put(f"/api/projects/{pid}", json={"multimodal_enabled": False})
    assert upd.json()["multimodal_enabled"] is False


def test_binding_crud(tmp_path):
    client, _store, _ark = _client(tmp_path)
    proj = client.post("/api/projects", json={"name": "P"}).json()
    emp = client.post("/api/employees", json={"name": "E"}).json()
    resp = client.post(
        "/api/bindings",
        json={
            "chat_id": "oc_1",
            "project_id": proj["id"],
            "digital_employee_id": emp["id"],
        },
    )
    assert resp.status_code == 201
    # 按项目过滤
    rows = client.get(f"/api/bindings?project_id={proj['id']}").json()["bindings"]
    assert len(rows) == 1 and rows[0]["chat_id"] == "oc_1"
    # 删
    assert client.delete("/api/bindings/oc_1").json()["deleted"] is True
    assert client.get("/api/bindings").json()["bindings"] == []


def test_binding_requires_all_fields(tmp_path):
    client, _store, _ark = _client(tmp_path)
    assert client.post("/api/bindings", json={"chat_id": "oc_1"}).status_code == 400


def test_bundle_crud(tmp_path):
    client, _store, _ark = _client(tmp_path)
    resp = client.post(
        "/api/bundles",
        json={"name": "包", "skills": [{"skill_id": "lark-im"}], "builtin_tool_toggles": {"web_search": True}},
    )
    assert resp.status_code == 201
    bundle = resp.json()
    assert bundle["skills"] == [{"skill_id": "lark-im"}]
    bid = bundle["id"]
    upd = client.put(f"/api/bundles/{bid}", json={"mcp_servers": [{"name": "m"}]})
    assert upd.json()["mcp_servers"] == [{"name": "m"}]
    assert client.delete(f"/api/bundles/{bid}").json()["deleted"] is True


# ---- 记忆透传 --------------------------------------------------------------


def test_memory_requires_store_first(tmp_path):
    client, _store, _ark = _client(tmp_path)
    proj = client.post("/api/projects", json={"name": "P"}).json()
    # 未建 Store → 409
    assert client.get(f"/api/memory/{proj['id']}").status_code == 409


def test_memory_crud_after_store_provisioned(tmp_path):
    client, _store, ark = _client(tmp_path)
    proj = client.post("/api/projects", json={"name": "P"}).json()
    # 懒建 Store
    synced = client.post(f"/api/sync/project-memory/{proj['id']}")
    assert synced.status_code == 200
    assert synced.json()["memory_store_id"] == "store-1"
    assert ark.stores_created == 1
    pid = proj["id"]
    # 建记忆
    created = client.post(f"/api/memory/{pid}", json={"path": "/facts/x", "content": "hi"})
    assert created.status_code == 201
    mid = created.json()["id"]
    # 列
    assert any(m["id"] == mid for m in client.get(f"/api/memory/{pid}").json()["memories"])
    # 读
    assert client.get(f"/api/memory/{pid}/{mid}").json()["content"] == "hi"
    # 改
    assert client.put(f"/api/memory/{pid}/{mid}", json={"content": "bye"}).json()["content"] == "bye"
    # 删
    assert client.delete(f"/api/memory/{pid}/{mid}").json()["deleted"] is True


def test_memory_create_requires_path(tmp_path):
    client, _store, _ark = _client(tmp_path)
    proj = client.post("/api/projects", json={"name": "P"}).json()
    client.post(f"/api/sync/project-memory/{proj['id']}")
    assert client.post(f"/api/memory/{proj['id']}", json={"content": "x"}).status_code == 400


# ---- 同步 ------------------------------------------------------------------


def test_sync_employee_creates_agent(tmp_path):
    client, _store, ark = _client(tmp_path)
    emp = client.post("/api/employees", json={"name": "阿J", "identity_prompt": "s"}).json()
    resp = client.post(f"/api/sync/employee/{emp['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ark_agent_id"] == "agent-1"
    assert body["sync_status"] == "synced"
    assert len(ark.created_agents) == 1


def test_sync_employee_404(tmp_path):
    client, _store, _ark = _client(tmp_path)
    assert client.post("/api/sync/employee/nope").status_code == 404


# ---- 鉴权 & 总览 ----------------------------------------------------------


def test_token_auth_enforced(tmp_path):
    client, _store, _ark = _client(tmp_path, token="secret")
    # 无 token → 401
    assert client.get("/api/employees").status_code == 401
    # 错误 token → 401
    assert client.get("/api/employees", headers={"Authorization": "Bearer wrong"}).status_code == 401
    # 正确 token（Bearer）→ 200
    assert client.get("/api/employees", headers={"Authorization": "Bearer secret"}).status_code == 200
    # 正确 token（X-Admin-Token）→ 200
    assert client.get("/api/employees", headers={"X-Admin-Token": "secret"}).status_code == 200


def test_overview_counts(tmp_path):
    client, _store, _ark = _client(tmp_path)
    client.post("/api/employees", json={"name": "E"})
    client.post("/api/projects", json={"name": "P"})
    counts = client.get("/api/overview").json()["counts"]
    assert counts["employees"] == 1 and counts["projects"] == 1
