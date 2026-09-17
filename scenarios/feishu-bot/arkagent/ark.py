"""火山方舟 Managed Agents 客户端（httpx 异步）。

移植自原 TS 项目 src/ark.ts，并新增本 demo 需要的能力：
  - create_session 支持 resources（挂载 Memory Store，卡点 D）
  - send_message 支持追加 system.message（动态系统提示词，卡点 C）
  - create_static_bearer_credential（卡点 A：静态 Bearer 鉴权 MCP）
  - create_memory_store / create_memory（卡点 D：每用户专属记忆）

SSE 解析、超时回查逻辑与原实现保持等价。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import quote

import httpx

from .timing import Stopwatch, time_block, timing_logger

log = logging.getLogger("arkagent.ark")

REQUEST_TIMEOUT = 30.0

# lark-cli 版本 + 安装脚本（对齐源仓库 src/ark.ts 的 LARK_CLI_SETUP_SCRIPT）。
# 方舟沙箱是干净的 cloud 环境，agent_toolset 的 shell 里默认没有 lark-cli——建 Environment 时
# 用 setup_script 把对应架构的二进制拉到 /usr/local/bin，Session 起来后 shell 里就能直接 `lark-cli ...`。
# SHA256 校验防止镜像被替换；用 npmmirror 国内镜像加速。
LARK_CLI_VERSION = "1.0.94"
LARK_CLI_SETUP_SCRIPT = f"""set -e
case "$(uname -m)" in
  x86_64) ARCH=amd64; SHA=60f505be65b43b5e58b01ec671199723483c5f630a03dfdd32d89f7f40f47d53 ;;
  aarch64|arm64) ARCH=arm64; SHA=87ddcba89557936958d8dcba4269d02837a32bb605dc0ac9c1aea8d653cbb7a3 ;;
  *) echo "unsupported architecture" >&2; exit 1 ;;
esac
ARCHIVE=/tmp/lark-cli.tar.gz
curl --fail --location --silent --show-error --connect-timeout 10 --max-time 120 "https://registry.npmmirror.com/-/binary/lark-cli/v{LARK_CLI_VERSION}/lark-cli-{LARK_CLI_VERSION}-linux-$ARCH.tar.gz" -o "$ARCHIVE"
echo "$SHA  $ARCHIVE" | sha256sum -c -
tar -xzf "$ARCHIVE" -C /usr/local/bin lark-cli
chmod 0755 /usr/local/bin/lark-cli
rm -f "$ARCHIVE\""""




@dataclass
class RunResult:
    terminal: str  # "idle" | "failed"
    messages: list[str] = field(default_factory=list)
    # terminal=="failed" 时方舟给出的失败摘要（error.type + error.message，已截断）。
    # 供上层写日志/回执用；成功轮为空字符串。见 event_error。
    error: str = ""


class ArkError(RuntimeError):
    """方舟请求异常。

    除消息文本外带上结构化字段，供调用方可靠判定（不必再靠字符串匹配）：
      - status_code：HTTP 状态码（404=Session 不存在、409=RuntimeBusy 等）。
      - body：响应体前若干字符，便于日志排查。
    向后兼容：状态码仍拼进 message 字符串，老的 `" 409" in str(error)` 判断照常工作。
    """

    def __init__(self, message: str, status_code: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _now_ms() -> int:
    return int(time.time() * 1000)


def _unwrap(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _response_id(payload: dict, resource: str) -> str:
    data = _unwrap(payload)
    ident = str(data.get("id") or "")
    if not ident:
        raise ArkError(f"创建 {resource} 成功，但响应中没有 ID")
    return ident


class ArkClient:
    def __init__(self, api_key: str, base_url: str, client: Optional[httpx.AsyncClient] = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        self._environment_configs: dict[str, dict] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = await self._client.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            content=json.dumps(body) if body is not None else None,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            request_id = response.headers.get("x-request-id")
            suffix = f" ({request_id})" if request_id else ""
            body = response.text[:300]
            # 带上结构化 status_code/body，调用方可据 404（Session 失效）/409（队列忙）精准兜底，
            # 无需再解析 message 字符串。message 里仍保留状态码，兼容旧的字符串判断。
            raise ArkError(
                f"方舟请求失败 {response.status_code}{suffix}: {body}",
                status_code=response.status_code,
                body=body,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except json.JSONDecodeError:
            return {}

    # ---- agents ----
    async def get_agent(self, agent_id: str) -> dict:
        payload = await self._request("GET", f"/agents/{quote(agent_id, safe='')}")
        data = _unwrap(payload)
        version = data.get("version")
        return {"id": str(data.get("id") or agent_id), "version": None if version is None else str(version)}

    async def list_agents(self) -> list[dict]:
        payload = await self._request("GET", "/agents?limit=100")
        return [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or item.get("id") or ""),
                "version": None if item.get("version") is None else str(item.get("version")),
            }
            for item in _items(payload)
            if item.get("id")
        ]

    async def create_agent(self, config: dict) -> dict:
        payload = await self._request("POST", "/agents", config)
        data = _unwrap(payload)
        ident = str(data.get("id") or data.get("agent_id") or "")
        if not ident:
            raise ArkError("创建 Agent 成功，但响应中没有 Agent ID")
        version = data.get("version")
        return {"id": ident, "name": str(data.get("name") or config.get("name", "")), "version": None if version is None else str(version)}

    async def update_agent(self, agent_id: str, config: dict, version: int) -> dict:
        """原地更新 Agent（生成新版本，Agent ID 不变）。方舟的更新语义是 POST 到具体资源
        （POST /agents/{id}，非 PUT/PATCH——后两者返回 404）；body 须携带当前 version，
        不匹配则更新失败——用于改 system prompt / tools / mcp_servers 而无需新建 Agent。"""
        body = {**config, "version": version}
        payload = await self._request("POST", f"/agents/{quote(agent_id, safe='')}", body)
        data = _unwrap(payload)
        new_version = data.get("version")
        return {
            "id": str(data.get("id") or agent_id),
            "name": str(data.get("name") or config.get("name", "")),
            "version": None if new_version is None else str(new_version),
        }

    # ---- environments ----
    async def list_environments(self) -> list[dict]:
        payload = await self._request("GET", "/environments?limit=100")
        return [
            {"id": str(item.get("id") or ""), "name": str(item.get("name") or item.get("id") or "")}
            for item in _items(payload)
            if item.get("id")
        ]

    async def create_environment(
        self,
        name: str,
        env: Optional[dict[str, str]] = None,
        setup_script: Optional[str] = None,
    ) -> dict:
        config: dict = {"type": "cloud", "networking": {"type": "unrestricted"}}
        if env:
            config["env"] = env
        # setup_script 在 Session 首次拉起沙箱时执行一次，用于装 lark-cli 这类系统级依赖。
        if setup_script:
            config["setup_script"] = setup_script
        payload = await self._request("POST", "/environments", {"name": name, "config": config})
        data = _unwrap(payload)
        ident = str(data.get("id") or data.get("environment_id") or "")
        if not ident:
            raise ArkError("创建 Environment 成功，但响应中没有 Environment ID")
        return {"id": ident, "name": str(data.get("name") or name)}

    async def get_environment_config(self, environment_id: str) -> dict:
        cached = self._environment_configs.get(environment_id)
        if cached:
            return cached
        payload = await self._request("GET", f"/environments/{quote(environment_id, safe='')}")
        data = _unwrap(payload)
        config = data.get("config")
        if not isinstance(config, dict) or not isinstance(config.get("type"), str):
            raise ArkError("Environment 响应缺少有效 config")
        self._environment_configs[environment_id] = config
        return config

    # ---- vaults & credentials ----
    async def create_vault(self, display_name: str) -> str:
        payload = await self._request("POST", "/vaults", {"display_name": display_name})
        return _response_id(payload, "Vault")

    async def list_vaults(self) -> list[dict]:
        payload = await self._request("GET", "/vaults?limit=100")
        return [
            {"id": str(item.get("id") or ""), "display_name": str(item.get("display_name") or "")}
            for item in _items(payload)
            if item.get("id")
        ]

    async def list_credentials(self, vault_id: str) -> list[dict]:
        payload = await self._request("GET", f"/vaults/{quote(vault_id, safe='')}/credentials?limit=100")
        result = []
        for item in _items(payload):
            if not item.get("id"):
                continue
            auth = item.get("auth") or {}
            result.append(
                {
                    "id": str(item.get("id")),
                    "display_name": str(item.get("display_name") or ""),
                    "auth_type": str(auth.get("type") or ""),
                    "secret_name": str(auth.get("secret_name") or ""),
                    "mcp_server_url": auth.get("mcp_server_url"),
                }
            )
        return result

    async def create_static_bearer_credential(self, vault_id: str, display_name: str, mcp_server_url: str, token: str) -> str:
        """卡点 A：为 MCP 创建静态 Bearer 凭据。创建时方舟会立即握手探测 MCP，不可达则 4xx。"""
        payload = await self._request(
            "POST",
            f"/vaults/{quote(vault_id, safe='')}/credentials",
            {
                "display_name": display_name,
                "auth": {"type": "static_bearer", "mcp_server_url": mcp_server_url, "token": token},
            },
        )
        return _response_id(payload, "Credential")

    async def create_environment_variable_credential(
        self, vault_id: str, display_name: str, secret_name: str, secret_value: str
    ) -> str:
        """把一个敏感值作为「环境变量凭据」存进 Vault（对齐源仓库 createEnvironmentVariableCredential）。

        与 static_bearer 不同：这类凭据不绑 MCP，只在 Session 挂上对应 vault 后，把 secret_value
        注入沙箱环境变量 secret_name。lark-cli 的 Bot 身份使用
        LARKSUITE_CLI_TENANT_ACCESS_TOKEN；App Secret 留在 Bot 主机，不进入 Vault 或 Agent 沙箱。
        """
        payload = await self._request(
            "POST",
            f"/vaults/{quote(vault_id, safe='')}/credentials",
            {
                "display_name": display_name,
                "auth": {
                    "type": "environment_variable",
                    "secret_name": secret_name,
                    "secret_value": secret_value,
                    "networking": {"type": "unrestricted"},
                },
            },
        )
        return _response_id(payload, "Credential")

    async def update_environment_credential(
        self, vault_id: str, credential_id: str, secret_value: str
    ) -> None:
        """轮换环境变量凭据的值（如短期 token 刷新）。只改 secret_value，凭据 id 不变。"""
        await self._request(
            "POST",
            f"/vaults/{quote(vault_id, safe='')}/credentials/{quote(credential_id, safe='')}",
            {"auth": {"type": "environment_variable", "secret_value": secret_value}},
        )

    async def delete_credential(self, vault_id: str, credential_id: str) -> None:
        """硬删除凭据。mcp_server_url 是结构性字段、创建后锁定，换 MCP 地址只能删旧建新
        （官方「轮换凭据」：结构性字段不可改，删除旧凭据再创建新的）。"""
        await self._request(
            "DELETE",
            f"/vaults/{quote(vault_id, safe='')}/credentials/{quote(credential_id, safe='')}",
        )

    # ---- memory stores (卡点 D) ----
    async def create_memory_store(self, name: str, description: str) -> str:
        payload = await self._request("POST", "/memory_stores", {"name": name, "description": description})
        return _response_id(payload, "Memory Store")

    async def create_memory(self, store_id: str, path: str, content: str) -> None:
        await self._request(
            "POST",
            f"/memory_stores/{quote(store_id, safe='')}/memories",
            {"path": path, "content": content},
        )

    # ---- sessions ----
    async def create_session(
        self,
        agent_id: str,
        environment_id: str,
        vault_ids: Optional[list[str]] = None,
        env_overrides: Optional[dict[str, str]] = None,
        resources: Optional[list[dict]] = None,
    ) -> str:
        """一次成型：卡点 B（env_overrides 注入 OpenID）+ A（vault_ids）+ D（resources 挂 Memory Store）。"""
        vault_ids = vault_ids or []
        env_overrides = env_overrides or {}
        resources = resources or []

        body: dict = {"agent": agent_id}
        if env_overrides:
            # 卡点 B：会话级环境变量透传（environment_with_overrides）。
            with time_block("ark.get_environment_config", env=environment_id):
                environment_config = await self.get_environment_config(environment_id)
            merged_env = {**(environment_config.get("env") or {}), **env_overrides}
            body["environment"] = {
                "id": environment_id,
                "type": "environment_with_overrides",
                "config": {**environment_config, "env": merged_env},
            }
        else:
            body["environment_id"] = environment_id
        if vault_ids:
            body["vault_ids"] = vault_ids
        if resources:
            body["resources"] = resources

        with time_block("ark.create_session.post"):
            payload = await self._request("POST", "/sessions", body)
        data = _unwrap(payload)
        ident = str(data.get("id") or data.get("session_id") or "")
        if not ident:
            raise ArkError("创建 Session 成功，但响应中没有 Session ID")
        return ident

    # ---- files & session resources (多模态：上传文件 + 挂载到 Session 文件系统) ----
    async def upload_file(self, name: str, mime_type: str, data: bytes) -> str:
        """上传文件到方舟 Files API（purpose=agent），返回 file_id。

        对齐官方「上传并挂载文件」：先把二进制传到 /files 拿 file_id，后续 create_session
        的 resources 或 add_session_file 用它挂到 Session 沙箱的 /mnt/session/uploads/。
        purpose=agent 表示该文件供 Agent 读取（见 docs「上传文件」）。走 multipart/form-data，
        不复用 _request（后者只发 JSON）；鉴权头与超时保持一致。
        """
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}
        files = {"file": (name, data, mime_type or "application/octet-stream")}
        with time_block("ark.upload_file", name=name):
            response = await self._client.post(
                f"{self.base_url}/files",
                headers=headers,
                data={"purpose": "agent"},
                files=files,
                timeout=REQUEST_TIMEOUT,
            )
        if response.status_code >= 400:
            request_id = response.headers.get("x-request-id")
            suffix = f" ({request_id})" if request_id else ""
            body = response.text[:300]
            raise ArkError(
                f"上传文件失败 {response.status_code}{suffix}: {body}",
                status_code=response.status_code,
                body=body,
            )
        payload = response.json() if response.content else {}
        data_obj = _unwrap(payload)
        file_id = str(data_obj.get("id") or "")
        if not file_id:
            raise ArkError(f"上传文件 {name} 成功，但响应中没有 File ID")
        return file_id

    async def add_session_resource(self, session_id: str, resource: dict) -> None:
        """向运行中的 Session 追加一个文件资源（POST /sessions/{id}/resources）。

        用于「已有 Session」场景下追加挂载——create_session 只在首次建会话时带 resources，
        本方法覆盖会话已存在、后续消息又带附件的情况。resource 至少含 type 与 file_id
        （文件挂载还建议带 mount_path）。见 docs「在 Session 运行时管理文件 · 添加文件资源」。
        """
        await self._request(
            "POST",
            f"/sessions/{quote(session_id, safe='')}/resources",
            resource,
        )

    async def add_session_file(self, session_id: str, file_id: str, mount_path: str) -> None:
        """add_session_resource 的文件便捷封装：把 file_id 挂到 /mnt/session/uploads/{mount_path}。"""
        await self.add_session_resource(
            session_id,
            {"type": "file", "file_id": file_id, "mount_path": mount_path},
        )

    async def send_message(self, session_id: str, text: str, system_message: Optional[str] = None) -> None:
        """发送 user.message；若给了 system_message，追加为数组最后一个元素（卡点 C 动态系统提示词）。"""
        events: list[dict] = [{"type": "user.message", "content": [{"type": "text", "text": text}]}]
        if system_message:
            events.append({"type": "system.message", "content": [{"type": "text", "text": system_message}]})
        await self._request(
            "POST",
            f"/sessions/{quote(session_id, safe='')}/events",
            {"events": events},
        )

    async def run(
        self,
        session_id: str,
        text: str,
        timeout_ms: int,
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
        system_message: Optional[str] = None,
    ) -> RunResult:
        started_at = _now_ms()
        messages: list[str] = []
        seen: set[str] = set()
        try:
            # 先建流再发消息，避免秒回 Agent 在 SSE 订阅建立前就 message+idle。
            async def _drive() -> RunResult:
                first_event_logged = False
                first_message_logged = False
                async with self._open_event_stream(session_id) as stream:
                    with time_block("ark.run.send_message", session=session_id):
                        await self.send_message(session_id, text, system_message=system_message)
                    # 从「消息已发出」开始计时，衡量方舟侧首字节/首条消息/到终态的等待。
                    sw = Stopwatch()
                    async for event in stream:
                        if not first_event_logged:
                            first_event_logged = True
                            timing_logger.info(
                                "[timing] ark.run.first_event=%.1fms session=%s", sw.total_ms(), session_id
                            )
                        eid = event.get("id")
                        if eid and eid in seen:
                            continue
                        if eid:
                            seen.add(eid)
                        if event.get("type") == "agent.message":
                            body = event_text(event)
                            if body:
                                if not first_message_logged:
                                    first_message_logged = True
                                    timing_logger.info(
                                        "[timing] ark.run.first_message=%.1fms session=%s", sw.total_ms(), session_id
                                    )
                                messages.append(body)
                        progress = event_progress(event)
                        if progress and on_progress:
                            await on_progress(progress)
                        if event.get("type") in ("session.error", "session.status_failed"):
                            sw.mark("ark.run.to_terminal", session=session_id, terminal="failed")
                            error = event_error(event)
                            # 把方舟给的失败原因落到日志：否则上层只看到「执行失败」，排查得手动拉 events。
                            log.warning(
                                "方舟 Session 执行失败 session=%s：%s",
                                session_id, error or "（未提供错误详情）",
                            )
                            return RunResult(terminal="failed", messages=messages, error=error)
                        if event.get("type") == "session.status_idle":
                            sw.mark("ark.run.to_terminal", session=session_id, terminal="idle")
                            return RunResult(terminal="idle", messages=messages)
                raise ArkError("事件流结束，但未观察到 Session 终态")

            return await asyncio.wait_for(_drive(), timeout=timeout_ms / 1000)
        except (asyncio.TimeoutError, httpx.ReadTimeout):
            recovered = await self._recover_timed_out_run(session_id, started_at)
            if recovered:
                return recovered
            raise ArkError("Session 运行超时")

    async def _recover_timed_out_run(self, session_id: str, started_at: int) -> Optional[RunResult]:
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(5)
            payload = await self._request("GET", f"/sessions/{quote(session_id, safe='')}/events?limit=200")
            events = payload.get("data") if isinstance(payload.get("data"), list) else []
            result = result_from_events(events, started_at)
            if result:
                return result
        return None

    def _open_event_stream(self, session_id: str):
        return _EventStream(self, session_id)


class _EventStream:
    def __init__(self, client: ArkClient, session_id: str):
        self._client = client
        self._session_id = session_id
        self._ctx = None
        self._response: Optional[httpx.Response] = None

    async def __aenter__(self) -> AsyncIterator[dict]:
        url = f"{self._client.base_url}/sessions/{quote(self._session_id, safe='')}/events/stream"
        headers = {"Accept": "text/event-stream", "Authorization": f"Bearer {self._client.api_key}"}
        self._ctx = self._client._client.stream("GET", url, headers=headers, timeout=None)
        self._response = await self._ctx.__aenter__()
        if self._response.status_code >= 400:
            body = ""
            try:
                body = (await self._response.aread()).decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001 - 读错误体失败不影响抛出状态码
                body = ""
            raise ArkError(
                f"方舟事件流失败 {self._response.status_code}: {body}",
                status_code=self._response.status_code,
                body=body,
            )
        return self._iterate()

    async def __aexit__(self, *exc) -> None:
        if self._ctx is not None:
            await self._ctx.__aexit__(*exc)

    async def _iterate(self) -> AsyncIterator[dict]:
        buffer = ""
        async for chunk in self._response.aiter_text():
            buffer += chunk.replace("\r\n", "\n")
            events, buffer = drain_event_buffer(buffer)
            for event in events:
                yield event
        tail = buffer.strip()
        if tail:
            for event in parse_event_block(tail):
                yield event


# ---- SSE helpers（与原 TS 等价，模块级便于单测）----
def parse_event_block(block: str) -> list[dict]:
    lines = [line.strip() for line in block.split("\n")]
    lines = [line for line in lines if line and not line.startswith(":")]
    if not lines:
        return []
    data_lines = [line[5:].strip() for line in lines if line.startswith("data:")]
    if data_lines:
        return [json.loads("\n".join(data_lines))]
    return [json.loads(line) for line in lines]


def drain_event_buffer(input_text: str) -> tuple[list[dict], str]:
    normalized = input_text.replace("\r\n", "\n")
    events: list[dict] = []
    cursor = 0
    while True:
        boundary = normalized.find("\n\n", cursor)
        if boundary < 0:
            break
        events.extend(parse_event_block(normalized[cursor:boundary]))
        cursor = boundary + 2
    rest = normalized[cursor:]
    if "\n\n" not in normalized and "\n" in rest:
        parts = rest.split("\n")
        pending = parts.pop() if parts else ""
        for line in parts:
            events.extend(parse_event_block(line))
        return events, pending
    return events, rest


def event_progress(event: dict) -> Optional[str]:
    if event.get("type") == "agent.tool_result" and event.get("is_error") is True:
        return "工具执行未成功，Agent 正在尝试恢复"
    if event.get("type") != "agent.tool_use":
        return None
    name = event.get("name") if isinstance(event.get("name"), str) else "未知工具"
    payload_input = event.get("input") if isinstance(event.get("input"), dict) else {}
    description = payload_input.get("description")
    description = description.strip() if isinstance(description, str) else ""
    # 只展示 Agent 主动提供的简短描述，绝不转发 command、路径或完整工具参数。
    if description:
        return f"正在执行：{description[:120]}"
    return f"正在调用工具：{str(name)[:80]}"


def result_from_events(events: list[dict], started_at: int) -> Optional[RunResult]:
    def _after(event: dict) -> bool:
        stamp = event.get("processed_at")
        if not isinstance(stamp, str):
            return False
        parsed = _parse_iso_ms(stamp)
        return parsed is not None and parsed >= started_at

    current = [event for event in events if _after(event)]
    failed_events = [event for event in current if event.get("type") in ("session.error", "session.status_failed")]
    failed = bool(failed_events)
    idle = any(event.get("type") == "session.status_idle" for event in current)
    if not failed and not idle:
        return None
    messages = [event_text(event) for event in current if event.get("type") == "agent.message"]
    messages = [m for m in messages if m]
    # 与实时路径一致：失败时把方舟给的错误摘要一并带出（取第一条失败事件的 error）。
    error = event_error(failed_events[0]) if failed_events else ""
    return RunResult(terminal="failed" if failed else "idle", messages=messages, error=error)


def event_text(event: dict) -> str:
    content = event.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text")


def event_error(event: dict) -> str:
    """从 session.error / session.status_failed 事件里提取一句可读的失败摘要。

    方舟的失败详情藏在 event["error"] 里（形如 {"type": "model_request_failed_error",
    "message": "{...InvalidParameter...Timeout while processing file_url...}"}）；不提取的话
    上层只知道 terminal=failed、看不到为什么。这里把 type + message 拼成一行（截断防日志爆炸），
    message 若是嵌套 JSON 也原样带上——排查时能直接看到 file_url 超时这类根因。
    """
    error = event.get("error")
    if not isinstance(error, dict):
        return ""
    etype = str(error.get("type") or "").strip()
    message = error.get("message")
    message = str(message).strip() if message is not None else ""
    summary = f"{etype}: {message}" if etype and message else (etype or message)
    return summary[:500]


def _parse_iso_ms(value: str) -> Optional[int]:
    from datetime import datetime

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def _items(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    return []
