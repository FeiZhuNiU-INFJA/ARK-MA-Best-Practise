"""ark_min.py —— 独立最小火山方舟 Managed Agents HTTP 客户端。

设计目标（本 skill 的硬约束）：
  * 完全自包含：只依赖 httpx，**不 import 主项目 arkagent**。
  * 覆盖重放对比实验所需的最小 API 面：
      - create_agent / create_environment / create_session
      - send_event（发 user.message、user.custom_tool_result）
      - stream_events（SSE 事件流迭代）
      - upload_skill（POST /skills，multipart，CreateSkill）
  * URL / header / 响应 data 包裹的形状照火山方舟约定实现（参考 arkagent/ark.py 的调用契约，
    但这里是独立重写，不引用其代码）。

用法：
    async with ArkMin(api_key, base_url) as ark:
        agent = await ark.create_agent(cfg)
        ...

注意：CreateSkill 需要 beta header `X-Ark-Beta: agentic-2026-06-01`（见方舟 MA 文档）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import quote

import httpx

DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
SKILL_BETA_HEADER = {"X-Ark-Beta": "agentic-2026-06-01"}
REQUEST_TIMEOUT = 120.0


class ArkMinError(RuntimeError):
    """方舟请求失败。消息里带上状态码 + x-request-id + 响应片段，便于定位。"""


def _unwrap(payload: dict) -> dict:
    """方舟响应统一包在 data 里；没有 data 就返回原体。"""
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


class ArkMin:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE,
                 client: Optional[httpx.AsyncClient] = None):
        if not api_key:
            raise ArkMinError("缺少 ARK_API_KEY")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)

    async def __aenter__(self) -> "ArkMin":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- 基础请求 ----
    async def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        resp = await self._client.request(
            method, f"{self.base_url}{path}", headers=headers,
            content=json.dumps(body) if body is not None else None,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code >= 400:
            rid = resp.headers.get("x-request-id")
            suffix = f" (req={rid})" if rid else ""
            raise ArkMinError(f"方舟请求失败 {resp.status_code}{suffix}: {resp.text[:400]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}

    # ---- chat completions（等效性 LLM 判分用；与 MA agent/session 无关）----
    async def chat(self, model: str, messages: list[dict],
                   temperature: float = 0.0, max_tokens: int = 1024) -> str:
        """最小 chat-completions：发一组 messages，返回首条回复的文本。

        用于 equivalence 的「原产物 vs MA 产物」语义判分——那是一次性的打分请求，
        不需要工具/流式，走方舟兼容 OpenAI 的 /chat/completions 即可。model 用普通对话模型 id。
        """
        payload = await self._request("POST", "/chat/completions", {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        data = _unwrap(payload)
        choices = data.get("choices") or payload.get("choices") or []
        if not choices:
            raise ArkMinError(f"chat 无 choices: {json.dumps(payload, ensure_ascii=False)[:300]}")
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "")

    # ---- agents ----
    async def create_agent(self, config: dict) -> str:
        payload = await self._request("POST", "/agents", config)
        data = _unwrap(payload)
        ident = str(data.get("id") or data.get("agent_id") or "")
        if not ident:
            raise ArkMinError("创建 Agent 成功，但响应无 Agent ID")
        return ident

    # ---- environments ----
    async def create_environment(self, name: str, networking_type: str = "unrestricted") -> str:
        # unrestricted 完全放行出网（custom tool 走事件流，不严格需要出网；但保持与 MA 常规一致）。
        # config 形状对齐官方 SDK 示例：{"type":"cloud","networking":{"type":...}}。
        config = {"type": "cloud", "networking": {"type": networking_type}}
        payload = await self._request("POST", "/environments", {"name": name, "config": config})
        data = _unwrap(payload)
        ident = str(data.get("id") or data.get("environment_id") or "")
        if not ident:
            raise ArkMinError("创建 Environment 成功，但响应无 Environment ID")
        return ident

    # ---- sessions ----
    async def create_session(self, agent_id: str, environment_id: str,
                             title: Optional[str] = None) -> str:
        body: dict = {"agent": agent_id, "environment_id": environment_id}
        if title:
            body["title"] = title
        payload = await self._request("POST", "/sessions", body)
        data = _unwrap(payload)
        ident = str(data.get("id") or data.get("session_id") or "")
        if not ident:
            raise ArkMinError("创建 Session 成功，但响应无 Session ID")
        return ident

    async def send_event(self, session_id: str, events: list[dict]) -> None:
        await self._request(
            "POST", f"/sessions/{quote(session_id, safe='')}/events",
            {"events": events},
        )

    async def send_user_message(self, session_id: str, text: str) -> None:
        await self.send_event(session_id, [
            {"type": "user.message", "content": [{"type": "text", "text": text}]},
        ])

    async def send_custom_tool_result(self, session_id: str, tool_use_id: str,
                                      output: str, is_error: bool = False) -> None:
        """回传自定义工具执行结果。

        官方事件结构：user.custom_tool_result 用 `custom_tool_use_id` 关联对应的
        agent.custom_tool_use 事件 ID（必须与 Agent 发起调用时的 ID 完全一致）。
        字段名不是 tool_use_id——用错会 400 InvalidPayload。
        """
        await self.send_event(session_id, [
            {
                "type": "user.custom_tool_result",
                "custom_tool_use_id": tool_use_id,
                "is_error": is_error,
                "content": [{"type": "text", "text": output}],
            },
        ])

    # ---- 清理（实跑收尾，best-effort，删除失败不抛以免掩盖主结果）----
    async def _delete(self, path: str) -> None:
        try:
            await self._request("DELETE", path)
        except ArkMinError:
            pass

    async def delete_session(self, session_id: str) -> None:
        await self._delete(f"/sessions/{quote(session_id, safe='')}")

    async def delete_environment(self, environment_id: str) -> None:
        await self._delete(f"/environments/{quote(environment_id, safe='')}")

    async def delete_agent(self, agent_id: str) -> None:
        await self._delete(f"/agents/{quote(agent_id, safe='')}")

    # ---- SSE 事件流 ----
    async def stream_events(self, session_id: str) -> AsyncIterator[dict]:
        """迭代 Session 事件流。逐个 yield 解析后的 event dict。"""
        url = f"{self.base_url}/sessions/{quote(session_id, safe='')}/events/stream"
        headers = {"Accept": "text/event-stream", "Authorization": f"Bearer {self.api_key}"}
        async with self._client.stream("GET", url, headers=headers, timeout=None) as resp:
            if resp.status_code >= 400:
                text = await resp.aread()
                raise ArkMinError(f"打开事件流失败 {resp.status_code}: {text[:300]}")
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                # SSE 以空行分隔事件块
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    event = _parse_sse_block(block)
                    if event is not None:
                        yield event

    # ---- skills 上传（CreateSkill）----
    async def upload_skill(self, skill_dir: Path, display_title: Optional[str] = None) -> dict:
        """把一个本地 skill 目录（含 SKILL.md 及子文档）multipart 上传，返回 skill 记录（含 id）。

        filename= 用相对路径（顶层目录/文件），符合方舟 CreateSkill 约定。
        """
        skill_dir = Path(skill_dir)
        top = skill_dir.name
        files = []
        opened = []
        try:
            for p in sorted(skill_dir.rglob("*")):
                if not p.is_file():
                    continue
                rel = f"{top}/{p.relative_to(skill_dir).as_posix()}"
                fh = p.open("rb")
                opened.append(fh)
                files.append(("files[]", (rel, fh, "text/markdown")))
            data = {}
            if display_title:
                data["display_title"] = display_title
            headers = {"Authorization": f"Bearer {self.api_key}", **SKILL_BETA_HEADER}
            resp = await self._client.post(
                f"{self.base_url}/skills", headers=headers, data=data, files=files,
                timeout=REQUEST_TIMEOUT,
            )
        finally:
            for fh in opened:
                fh.close()
        if resp.status_code >= 400:
            rid = resp.headers.get("x-request-id")
            suffix = f" (req={rid})" if rid else ""
            raise ArkMinError(f"CreateSkill 失败 {resp.status_code}{suffix}: {resp.text[:400]}")
        payload = resp.json()
        rec = _unwrap(payload)
        if not rec.get("id"):
            raise ArkMinError(f"CreateSkill 成功但响应无 id: {payload}")
        return rec


def _parse_sse_block(block: str) -> Optional[dict]:
    """把一个 SSE 事件块（若干 data: 行）解析成 dict。非 JSON 或空块返回 None。"""
    data_lines = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    if raw in ("", "[DONE]"):
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
