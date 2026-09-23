"""数字员工可视化管理系统 —— 后端 API 服务（建在 ark-gateway 控制面上）。

方案 A 下配置库是全量权威，本服务只依赖控制面 ``MAControlPlane``（内含 ``ArkClient`` +
``ConfigStore``）：CRUD 直接读写配置库，记忆读写透传到方舟 memory API，``/api/sync/*``
触发单向同步。**不碰飞书 channel、不装配 session**，与 runtime 解耦。

路由概览：
  - ``/api/overview``                              总览（各实体计数 + 最近同步日志）
  - ``/api/employees``            GET/POST         数字员工列表 / 新建
  - ``/api/employees/{id}``       GET/PUT/DELETE    单个数字员工 读/改/删
  - ``/api/projects``             GET/POST         项目列表 / 新建
  - ``/api/projects/{id}``        GET/PUT/DELETE    单个项目 读/改/删
  - ``/api/bindings``             GET/POST         群绑定列表（可 ?project_id=）/ 新建·改
  - ``/api/bindings/{chat_id}``  DELETE           删除群绑定
  - ``/api/bundles``              GET/POST         能力包列表 / 新建
  - ``/api/bundles/{id}``         GET/PUT/DELETE    单个能力包 读/改/删
  - ``/api/memory/{project_id}``  GET/POST         列/建 项目共享记忆条目（透传方舟）
  - ``/api/memory/{project_id}/{memory_id}``  GET/PUT/DELETE  读/改/删 单条记忆
  - ``/api/sync/employee/{id}``          POST      同步单个数字员工到方舟（建/更新 Agent）
  - ``/api/sync/project-memory/{id}``    POST      懒建项目共享 Memory Store
  - ``/api/sync/logs``                   GET       最近同步日志

鉴权：``env ADMIN_API_TOKEN`` 非空时，``/api/*`` 需带 ``Authorization: Bearer <token>``
（或 ``X-Admin-Token: <token>``）；为空时放行（仅限本地/内网，文档已标注不建议公网暴露）。
静态前端由 ``admin/web/`` 托管在根路径。
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from arkagent.ark import ArkClient
from arkagent.gateway import (
    CapabilityBundle,
    ConfigStore,
    DigitalEmployee,
    FeishuGroupBinding,
    MAControlPlane,
    Project,
)

WEB_DIR = Path(__file__).resolve().parent / "web"


# ---- 序列化辅助 ------------------------------------------------------------


def _as_dict(entity: Any) -> dict:
    """dataclass 实体 → JSON 可序列化 dict。"""
    return dataclasses.asdict(entity)


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - 非法 JSON 统一转 400
        raise _BadRequest("请求体不是合法 JSON")
    if not isinstance(payload, dict):
        raise _BadRequest("请求体必须是 JSON 对象")
    return payload


class _BadRequest(Exception):
    """handler 内抛出，统一转 400。"""


# ---- 鉴权中间件 ------------------------------------------------------------


class _TokenAuthMiddleware(BaseHTTPMiddleware):
    """token 非空时校验 /api/* 请求头，否则放行（本地/内网模式）。"""

    def __init__(self, app, token: str = "") -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if self._token and request.url.path.startswith("/api/"):
            presented = _extract_token(request)
            if presented != self._token:
                return _err("未授权：缺少或错误的 admin token", status=401)
        return await call_next(request)


def _extract_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-admin-token", "").strip()


# ---- 应用工厂 --------------------------------------------------------------


def create_app(
    *,
    store: ConfigStore,
    plane: MAControlPlane,
    token: str = "",
    serve_static: bool = True,
) -> Starlette:
    """构造 Starlette 应用。store/plane 注入便于测试（可传 mock ArkClient）。"""

    # ---- 数字员工 -----------------------------------------------------------
    async def list_employees(request: Request) -> Response:
        return _ok({"employees": [_as_dict(e) for e in store.list_employees()]})

    async def create_employee(request: Request) -> Response:
        body = await _body(request)
        name = str(body.get("name") or "").strip()
        if not name:
            return _err("name 不能为空")
        emp = DigitalEmployee(
            id="",
            name=name,
            identity_prompt=str(body.get("identity_prompt") or ""),
            model_id=str(body.get("model_id") or "doubao-seed-evolving"),
            bundle_id=body.get("bundle_id") or None,
        )
        return _ok(_as_dict(store.upsert_employee(emp)), status=201)

    async def get_employee(request: Request) -> Response:
        emp = store.get_employee(request.path_params["employee_id"])
        return _ok(_as_dict(emp)) if emp else _err("数字员工不存在", status=404)

    async def update_employee(request: Request) -> Response:
        emp = store.get_employee(request.path_params["employee_id"])
        if not emp:
            return _err("数字员工不存在", status=404)
        body = await _body(request)
        # 只允许改身份类字段；ark_agent_id/version/sync_status 由同步流程维护。
        if "name" in body:
            emp.name = str(body["name"] or "").strip() or emp.name
        if "identity_prompt" in body:
            emp.identity_prompt = str(body["identity_prompt"] or "")
        if "model_id" in body:
            emp.model_id = str(body["model_id"] or "").strip() or emp.model_id
        if "bundle_id" in body:
            emp.bundle_id = body["bundle_id"] or None
        return _ok(_as_dict(store.upsert_employee(emp)))

    async def delete_employee(request: Request) -> Response:
        store.delete_employee(request.path_params["employee_id"])
        return _ok({"deleted": True})

    # ---- 项目 ---------------------------------------------------------------
    async def list_projects(request: Request) -> Response:
        return _ok({"projects": [_as_dict(p) for p in store.list_projects()]})

    async def create_project(request: Request) -> Response:
        body = await _body(request)
        name = str(body.get("name") or "").strip()
        if not name:
            return _err("name 不能为空")
        project = Project(id="", name=name, description=str(body.get("description") or ""))
        _apply_project_fields(project, body)
        return _ok(_as_dict(store.upsert_project(project)), status=201)

    async def get_project(request: Request) -> Response:
        project = store.get_project(request.path_params["project_id"])
        return _ok(_as_dict(project)) if project else _err("项目不存在", status=404)

    async def update_project(request: Request) -> Response:
        project = store.get_project(request.path_params["project_id"])
        if not project:
            return _err("项目不存在", status=404)
        body = await _body(request)
        if "name" in body:
            project.name = str(body["name"] or "").strip() or project.name
        if "description" in body:
            project.description = str(body["description"] or "")
        _apply_project_fields(project, body)
        return _ok(_as_dict(store.upsert_project(project)))

    async def delete_project(request: Request) -> Response:
        store.delete_project(request.path_params["project_id"])
        return _ok({"deleted": True})

    # ---- 群绑定 -------------------------------------------------------------
    async def list_bindings(request: Request) -> Response:
        project_id = request.query_params.get("project_id")
        rows = store.list_bindings(project_id)
        return _ok({"bindings": [_as_dict(b) for b in rows]})

    async def upsert_binding(request: Request) -> Response:
        body = await _body(request)
        chat_id = str(body.get("chat_id") or "").strip()
        project_id = str(body.get("project_id") or "").strip()
        employee_id = str(body.get("digital_employee_id") or "").strip()
        if not (chat_id and project_id and employee_id):
            return _err("chat_id/project_id/digital_employee_id 均不能为空")
        # tenant_key 仅作归属属性，可选透传，不参与路由主键（默认 "default"）。
        tenant_key = str(body.get("tenant_key") or "").strip() or "default"
        binding = FeishuGroupBinding(
            chat_id=chat_id,
            project_id=project_id,
            digital_employee_id=employee_id,
            tenant_key=tenant_key,
        )
        return _ok(_as_dict(store.upsert_binding(binding)), status=201)

    async def delete_binding(request: Request) -> Response:
        store.delete_binding(request.path_params["chat_id"])
        return _ok({"deleted": True})

    # ---- 能力包 -------------------------------------------------------------
    async def list_bundles(request: Request) -> Response:
        return _ok({"bundles": [_as_dict(b) for b in store.list_bundles()]})

    async def create_bundle(request: Request) -> Response:
        body = await _body(request)
        name = str(body.get("name") or "").strip()
        if not name:
            return _err("name 不能为空")
        bundle = CapabilityBundle(id="", name=name)
        _apply_bundle_fields(bundle, body)
        return _ok(_as_dict(store.upsert_bundle(bundle)), status=201)

    async def get_bundle(request: Request) -> Response:
        bundle = store.get_bundle(request.path_params["bundle_id"])
        return _ok(_as_dict(bundle)) if bundle else _err("能力包不存在", status=404)

    async def update_bundle(request: Request) -> Response:
        bundle = store.get_bundle(request.path_params["bundle_id"])
        if not bundle:
            return _err("能力包不存在", status=404)
        body = await _body(request)
        if "name" in body:
            bundle.name = str(body["name"] or "").strip() or bundle.name
        _apply_bundle_fields(bundle, body)
        return _ok(_as_dict(store.upsert_bundle(bundle)))

    async def delete_bundle(request: Request) -> Response:
        store.delete_bundle(request.path_params["bundle_id"])
        return _ok({"deleted": True})

    # ---- 项目共享记忆（透传方舟 memory API）--------------------------------
    def _require_store_id(project_id: str) -> tuple[Optional[str], Optional[Response]]:
        project = store.get_project(project_id)
        if not project:
            return None, _err("项目不存在", status=404)
        if not project.memory_store_id:
            return None, _err(
                "项目尚未创建共享 Memory Store，请先 POST /api/sync/project-memory/{id}",
                status=409,
            )
        return project.memory_store_id, None

    async def list_memory(request: Request) -> Response:
        store_id, error = _require_store_id(request.path_params["project_id"])
        if error:
            return error
        prefix = request.query_params.get("path_prefix", "/")
        depth = int(request.query_params.get("depth", "3"))
        items = await plane.ark.list_memories(store_id, prefix, depth)
        return _ok({"store_id": store_id, "memories": items})

    async def create_memory(request: Request) -> Response:
        store_id, error = _require_store_id(request.path_params["project_id"])
        if error:
            return error
        body = await _body(request)
        path = str(body.get("path") or "").strip()
        content = str(body.get("content") or "")
        if not path:
            return _err("path 不能为空")
        created = await plane.ark.create_memory(store_id, path, content)
        return _ok(created, status=201)

    async def get_memory(request: Request) -> Response:
        store_id, error = _require_store_id(request.path_params["project_id"])
        if error:
            return error
        item = await plane.ark.get_memory(store_id, request.path_params["memory_id"])
        return _ok(item)

    async def update_memory(request: Request) -> Response:
        store_id, error = _require_store_id(request.path_params["project_id"])
        if error:
            return error
        body = await _body(request)
        path = body.get("path")
        content = body.get("content")
        if path is None and content is None:
            return _err("path/content 至少提供一个")
        updated = await plane.ark.update_memory(
            store_id,
            request.path_params["memory_id"],
            path=None if path is None else str(path),
            content=None if content is None else str(content),
        )
        return _ok(updated)

    async def delete_memory(request: Request) -> Response:
        store_id, error = _require_store_id(request.path_params["project_id"])
        if error:
            return error
        await plane.ark.delete_memory(store_id, request.path_params["memory_id"])
        return _ok({"deleted": True})

    # ---- 同步 ---------------------------------------------------------------
    async def sync_employee(request: Request) -> Response:
        emp = store.get_employee(request.path_params["employee_id"])
        if not emp:
            return _err("数字员工不存在", status=404)
        try:
            synced = await plane.sync_employee(emp)
        except Exception as error:  # noqa: BLE001 - 同步失败已落状态，转 502 供前端提示
            return _err(f"同步失败：{error}", status=502)
        return _ok(_as_dict(synced))

    async def sync_project_memory(request: Request) -> Response:
        project = store.get_project(request.path_params["project_id"])
        if not project:
            return _err("项目不存在", status=404)
        try:
            synced = await plane.sync_project_memory(project)
        except Exception as error:  # noqa: BLE001
            return _err(f"同步失败：{error}", status=502)
        return _ok(_as_dict(synced))

    async def sync_logs(request: Request) -> Response:
        limit = int(request.query_params.get("limit", "100"))
        return _ok({"logs": store.list_sync_logs(limit)})

    # ---- 总览 ---------------------------------------------------------------
    async def overview(request: Request) -> Response:
        return _ok(
            {
                "counts": {
                    "employees": len(store.list_employees()),
                    "projects": len(store.list_projects()),
                    "bindings": len(store.list_bindings()),
                    "bundles": len(store.list_bundles()),
                },
                "recent_sync_logs": store.list_sync_logs(20),
            }
        )

    routes = [
        Route("/api/overview", overview, methods=["GET"]),
        Route("/api/employees", list_employees, methods=["GET"]),
        Route("/api/employees", create_employee, methods=["POST"]),
        Route("/api/employees/{employee_id}", get_employee, methods=["GET"]),
        Route("/api/employees/{employee_id}", update_employee, methods=["PUT"]),
        Route("/api/employees/{employee_id}", delete_employee, methods=["DELETE"]),
        Route("/api/projects", list_projects, methods=["GET"]),
        Route("/api/projects", create_project, methods=["POST"]),
        Route("/api/projects/{project_id}", get_project, methods=["GET"]),
        Route("/api/projects/{project_id}", update_project, methods=["PUT"]),
        Route("/api/projects/{project_id}", delete_project, methods=["DELETE"]),
        Route("/api/bindings", list_bindings, methods=["GET"]),
        Route("/api/bindings", upsert_binding, methods=["POST"]),
        Route("/api/bindings/{chat_id}", delete_binding, methods=["DELETE"]),
        Route("/api/bundles", list_bundles, methods=["GET"]),
        Route("/api/bundles", create_bundle, methods=["POST"]),
        Route("/api/bundles/{bundle_id}", get_bundle, methods=["GET"]),
        Route("/api/bundles/{bundle_id}", update_bundle, methods=["PUT"]),
        Route("/api/bundles/{bundle_id}", delete_bundle, methods=["DELETE"]),
        Route("/api/memory/{project_id}", list_memory, methods=["GET"]),
        Route("/api/memory/{project_id}", create_memory, methods=["POST"]),
        Route("/api/memory/{project_id}/{memory_id}", get_memory, methods=["GET"]),
        Route("/api/memory/{project_id}/{memory_id}", update_memory, methods=["PUT"]),
        Route("/api/memory/{project_id}/{memory_id}", delete_memory, methods=["DELETE"]),
        Route("/api/sync/employee/{employee_id}", sync_employee, methods=["POST"]),
        Route(
            "/api/sync/project-memory/{project_id}",
            sync_project_memory,
            methods=["POST"],
        ),
        Route("/api/sync/logs", sync_logs, methods=["GET"]),
    ]
    if serve_static and WEB_DIR.exists():
        routes.append(Mount("/", app=StaticFiles(directory=str(WEB_DIR), html=True)))

    middleware = [Middleware(_TokenAuthMiddleware, token=token)]
    app = Starlette(routes=routes, middleware=middleware, exception_handlers={
        _BadRequest: _bad_request_handler,
    })
    return app


async def _bad_request_handler(request: Request, exc: _BadRequest) -> Response:
    return _err(str(exc) or "请求非法", status=400)


def _apply_project_fields(project: Project, body: dict) -> None:
    """把请求体里的项目开关 / 可写子集应用到实体（仅在提供时覆盖）。"""
    if "writable_memory_categories" in body:
        cats = body["writable_memory_categories"]
        if isinstance(cats, list):
            project.writable_memory_categories = [str(c) for c in cats]
    for key in ("reply_uses_topic", "multimodal_enabled", "markdown_enabled"):
        if key in body:
            setattr(project, key, bool(body[key]))


def _apply_bundle_fields(bundle: CapabilityBundle, body: dict) -> None:
    if "skills" in body and isinstance(body["skills"], list):
        bundle.skills = body["skills"]
    if "mcp_servers" in body and isinstance(body["mcp_servers"], list):
        bundle.mcp_servers = body["mcp_servers"]
    if "builtin_tool_toggles" in body and isinstance(body["builtin_tool_toggles"], dict):
        bundle.builtin_tool_toggles = body["builtin_tool_toggles"]
    if "writable_memory_categories" in body and isinstance(
        body["writable_memory_categories"], list
    ):
        bundle.writable_memory_categories = [str(c) for c in body["writable_memory_categories"]]
