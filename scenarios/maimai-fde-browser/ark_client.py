"""ark_client.py —— 自包含的火山方舟 Managed Agents HTTP 客户端（本 demo 专用）。

设计约束（与本仓库其它场景一致）：
  * 完全自包含：只依赖 httpx，**不 import 主项目 arkagent 包**，方便单独拷走运行。
  * 只覆盖本 demo 需要的最小 API 面：
      - create_agent / create_environment（支持 packages + setup_script）/ create_session（支持 resources）
      - upload_file（Files API，multipart，purpose=agent）—— 把沙箱脚本当输入文件挂进去
      - send_user_message / stream_events（SSE）—— 驱动 Agent
      - list_session_outputs / download_file —— 把沙箱写到 /mnt/session/outputs/ 的
        二维码、验证截图、职位结果取回本地
  * URL / header / data 包裹形状照火山方舟约定实现（参考 arkagent/ark.py 的调用契约，独立重写）。

鉴权：所有请求带 `Authorization: Bearer {ARK_API_KEY}`。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import quote

import httpx

DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
REQUEST_TIMEOUT = 120.0


class ArkError(RuntimeError):
    """方舟请求失败：消息里带状态码 + x-request-id + 响应片段，便于定位。"""


def _unwrap(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _items(payload) -> list:
    """从各种响应形状里取列表。payload 可能是 None / dict / 直接是 list。"""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(payload.get("items"), list):
        return payload["items"]
    return []


class ArkClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE,
                 client: Optional[httpx.AsyncClient] = None):
        if not api_key:
            raise ArkError("缺少 ARK_API_KEY")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)

    async def __aenter__(self) -> "ArkClient":
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
            raise ArkError(f"方舟请求失败 {resp.status_code}{suffix}: {resp.text[:400]}")
        if not resp.content:
            return {}
        try:
            parsed = resp.json()
        except json.JSONDecodeError:
            return {}
        # body 可能是 JSON null / 列表 / 其它非对象；统一回成 dict，避免下游 .get 崩。
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"data": parsed}
        return {}

    # ---- agents ----
    async def create_agent(self, config: dict) -> str:
        payload = await self._request("POST", "/agents", config)
        data = _unwrap(payload)
        ident = str(data.get("id") or data.get("agent_id") or "")
        if not ident:
            raise ArkError("创建 Agent 成功，但响应无 Agent ID")
        return ident

    async def delete_agent(self, agent_id: str) -> None:
        await self._delete(f"/agents/{quote(agent_id, safe='')}")

    # ---- environments ----
    async def create_environment(self, name: str, *, networking_type: str = "unrestricted",
                                 packages: Optional[dict] = None,
                                 setup_script: Optional[str] = None,
                                 env: Optional[dict] = None) -> str:
        """建 Environment。packages/setup_script 在沙箱首次启动时执行一次，
        用来装 Playwright + Chromium + 系统依赖（本 demo 的关键）。
        """
        config: dict = {"type": "cloud", "networking": {"type": networking_type}}
        if packages:
            config["packages"] = packages
        if setup_script:
            config["setup_script"] = setup_script
        if env:
            config["env"] = env
        payload = await self._request("POST", "/environments", {"name": name, "config": config})
        data = _unwrap(payload)
        ident = str(data.get("id") or data.get("environment_id") or "")
        if not ident:
            raise ArkError("创建 Environment 成功，但响应无 Environment ID")
        return ident

    async def delete_environment(self, environment_id: str) -> None:
        await self._delete(f"/environments/{quote(environment_id, safe='')}")

    # ---- files（输入上传 + 产物下载）----
    async def upload_file(self, path: Path, purpose: str = "agent") -> str:
        """把本地文件 multipart 上传到 Files API，返回 file_id（供 Session resources 挂载）。"""
        path = Path(path)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with path.open("rb") as fh:
            resp = await self._client.post(
                f"{self.base_url}/files", headers=headers,
                data={"purpose": purpose},
                files={"file": (path.name, fh, "application/octet-stream")},
                timeout=REQUEST_TIMEOUT,
            )
        if resp.status_code >= 400:
            rid = resp.headers.get("x-request-id")
            suffix = f" (req={rid})" if rid else ""
            raise ArkError(f"上传文件失败 {resp.status_code}{suffix}: {resp.text[:300]}")
        data = _unwrap(resp.json())
        ident = str(data.get("id") or data.get("file_id") or "")
        if not ident:
            raise ArkError("上传文件成功，但响应无 file_id")
        return ident

    async def list_session_outputs(self, session_id: str) -> list[dict]:
        """列出 Session 写到 /mnt/session/outputs/ 并注册到 Files API 的产物。

        返回 [{id, filename, bytes, mime_type, download_url}, ...]。
        """
        payload = await self._request(
            "GET", f"/files?scope_id={quote(session_id, safe='')}&purpose=agent&limit=100")
        result = []
        for item in _items(payload):
            if not item.get("id"):
                continue
            result.append({
                "id": str(item.get("id")),
                "filename": str(item.get("filename") or item.get("name") or ""),
                "bytes": item.get("bytes") or item.get("size"),
                "mime_type": str(item.get("mime_type") or item.get("content_type") or ""),
                "download_url": item.get("download_url"),
            })
        return result

    async def download(self, download_url: str, dest: Path) -> Path:
        """用预签名 download_url 下载文件内容到本地 dest。"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = await self._client.get(download_url, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 400:
            raise ArkError(f"下载产物失败 {resp.status_code}: {resp.text[:200]}")
        dest.write_bytes(resp.content)
        return dest

    # ---- sessions ----
    async def create_session(self, agent_id: str, environment_id: str, *,
                             title: Optional[str] = None,
                             resources: Optional[list[dict]] = None) -> str:
        body: dict = {"agent": agent_id, "environment_id": environment_id}
        if title:
            body["title"] = title
        if resources:
            body["resources"] = resources
        payload = await self._request("POST", "/sessions", body)
        data = _unwrap(payload)
        ident = str(data.get("id") or data.get("session_id") or "")
        if not ident:
            raise ArkError("创建 Session 成功，但响应无 Session ID")
        return ident

    async def delete_session(self, session_id: str) -> None:
        await self._delete(f"/sessions/{quote(session_id, safe='')}")

    async def send_event(self, session_id: str, events: list[dict]) -> None:
        await self._request(
            "POST", f"/sessions/{quote(session_id, safe='')}/events", {"events": events})

    async def send_user_message(self, session_id: str, text: str) -> None:
        await self.send_event(session_id, [
            {"type": "user.message", "content": [{"type": "text", "text": text}]},
        ])

    # ---- Chat Completions（方舟原生模型接口，用于编排侧直接让模型解析职位页文本）----
    async def chat_completions(self, model: str, messages: list[dict],
                               *, temperature: float = 0.0,
                               max_tokens: int = 4096) -> str:
        """调用方舟 /chat/completions，返回模型输出的纯文本（choices[0].message.content）。

        用途：登录后职位页选择器抓不到时，把 job_page.txt 的真实页面文本喂给 doubao，让模型
        直接抽取职位结构化字段——避免把脉脉的 DOM 选择器写死，页面改版也能扛。
        """
        payload = await self._request("POST", "/chat/completions", {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not choices:
            raise ArkError(f"chat_completions 无 choices：{json.dumps(payload, ensure_ascii=False)[:300]}")
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):  # 多模态分块时拼接文本块
            content = "".join(b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text")
        if not isinstance(content, str) or not content.strip():
            raise ArkError("chat_completions 返回内容为空")
        return content

    # ---- SSE 事件流 ----
    async def stream_events(self, session_id: str) -> AsyncIterator[dict]:
        """迭代 Session 事件流，逐个 yield 解析后的 event dict。"""
        url = f"{self.base_url}/sessions/{quote(session_id, safe='')}/events/stream"
        headers = {"Accept": "text/event-stream", "Authorization": f"Bearer {self.api_key}"}
        async with self._client.stream("GET", url, headers=headers, timeout=None) as resp:
            if resp.status_code >= 400:
                text = await resp.aread()
                raise ArkError(f"打开事件流失败 {resp.status_code}: {text[:300]}")
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    event = _parse_sse_block(block)
                    if event is not None:
                        yield event

    # ---- 清理（best-effort）----
    async def _delete(self, path: str) -> None:
        try:
            await self._request("DELETE", path)
        except ArkError:
            pass


def _parse_sse_block(block: str) -> Optional[dict]:
    """解析一个 SSE 事件块（event: / data: 行）。只关心 data 里的 JSON。"""
    data_lines = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith(":"):  # 注释行（如 ": ready"）
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
