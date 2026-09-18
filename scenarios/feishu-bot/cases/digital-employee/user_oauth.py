"""单聊按需用户 OAuth：Device Flow、每用户 Vault 与授权续跑编排。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional
import urllib.request

import httpx

from arkagent.feishu import IncomingMessage

USER_ACCESS_TOKEN_ENV = "LARKSUITE_CLI_USER_ACCESS_TOKEN"
USER_CREDENTIAL_NAME = "lark-cli-user-access-token"
USER_AUTH_PENDING = "ARKAGENT_USER_AUTH_PENDING"
BASE_USER_SCOPES = (
    "offline_access",
    "auth:user.id:read",
)
CALENDAR_USER_SCOPES = BASE_USER_SCOPES + (
    "calendar:calendar:read",
    "calendar:calendar.event:read",
    "calendar:calendar.free_busy:read",
)
DRIVE_USER_SCOPES = BASE_USER_SCOPES + ("search:docs:read",)
DOMAIN_USER_SCOPES = {
    "calendar": CALENDAR_USER_SCOPES,
    "drive": DRIVE_USER_SCOPES,
}
MAX_VAULT_SECRET_BYTES = 4096
DEBUG_ACCESS_TOKEN_PATH = Path(".dbg/oauth-vault-token-limit.access_token")
DEBUG_REQUEST_IDS_PATH = Path(".dbg/oauth-vault-token-limit.request_ids.jsonl")


def _token_diagnostics(token: str) -> dict:
    """生成不含凭据内容的 token 结构诊断，供临时调试使用。"""
    raw = token.encode("utf-8")
    result = {
        "token_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "dot_segments": len(token.split(".")),
        "is_jwt": False,
    }
    parts = token.split(".")
    if len(parts) != 3:
        return result
    try:
        header = json.loads(_base64url_decode(parts[0]))
        claims = json.loads(_base64url_decode(parts[1]))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return result
    if not isinstance(header, dict) or not isinstance(claims, dict):
        return result
    result.update(
        {
            "is_jwt": True,
            "segment_bytes": [len(part.encode("utf-8")) for part in parts],
            "jwt_header_keys": sorted(str(key) for key in header),
            "jwt_claim_keys": sorted(str(key) for key in claims),
        }
    )
    return result


def _base64url_decode(value: str) -> str:
    return base64.urlsafe_b64decode(
        value + "=" * (-len(value) % 4)
    ).decode("utf-8")


def _capture_access_token(token: str) -> None:
    """仅用于当前调试会话：保存原始 bearer token 到用户私有文件。"""
    path = Path(os.environ.get("OAUTH_DEBUG_ACCESS_TOKEN_PATH", DEBUG_ACCESS_TOKEN_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(descriptor, token.encode("utf-8"))
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _capture_request_id(operation: str, response: httpx.Response) -> None:
    """仅保存 OAuth 调用的追踪 ID，不保存凭据、请求体或响应体。"""
    request_id = next(
        (
            response.headers.get(header)
            for header in ("x-tt-logid", "x-request-id", "x-requestid")
            if response.headers.get(header)
        ),
        "",
    )
    if not request_id:
        return
    DEBUG_REQUEST_IDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        DEBUG_REQUEST_IDS_PATH,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        entry = {
            "at_ms": _now_ms(),
            "operation": operation,
            "status_code": response.status_code,
            "request_id": request_id,
        }
        os.write(descriptor, (json.dumps(entry) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)


# #region debug-point A-C:oauth-vault-token-limit
def _debug_report(hypothesis_id: str, location: str, msg: str, data: dict) -> None:
    def _send() -> None:
        try:
            settings = dict(
                line.split("=", 1)
                for line in Path(".dbg/oauth-vault-token-limit.env").read_text().splitlines()
                if "=" in line
            )
            payload = json.dumps(
                {
                    "sessionId": settings.get(
                        "DEBUG_SESSION_ID", "oauth-vault-token-limit"
                    ),
                    "runId": settings.get("DEBUG_RUN_ID", "pre-fix"),
                    "hypothesisId": hypothesis_id,
                    "location": location,
                    "msg": f"[DEBUG] {msg}",
                    "data": data,
                }
            ).encode()
            urllib.request.urlopen(
                urllib.request.Request(
                    settings.get("DEBUG_SERVER_URL", "http://127.0.0.1:7777/event"),
                    data=payload,
                    headers={"Content-Type": "application/json"},
                ),
                timeout=1,
            ).read()
        except Exception:
            pass

    __import__("threading").Thread(target=_send, daemon=True).start()
# #endregion


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_at: int


@dataclass(frozen=True)
class DeviceAuthorization:
    device_code: str
    verification_url: str
    expires_at: int
    interval_s: float


class FeishuOAuth:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._client = client

    async def begin(self, scopes: tuple[str, ...]) -> DeviceAuthorization:
        payload = await self._request(
            "POST",
            "https://accounts.feishu.cn/oauth/v1/device_authorization",
            data={
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "scope": " ".join(scopes),
            },
        )
        device_code = _string(payload, "device_code")
        verification_url = (
            _string(payload, "verification_uri_complete")
            or _string(payload, "verification_uri")
        )
        if not device_code or not verification_url:
            raise RuntimeError("飞书 Device Flow 响应缺少授权地址或 device_code")
        return DeviceAuthorization(
            device_code=device_code,
            verification_url=verification_url,
            expires_at=_now_ms() + _positive_int(payload, "expires_in", 600) * 1000,
            interval_s=float(_positive_int(payload, "interval", 5)),
        )

    async def poll(self, device: DeviceAuthorization) -> OAuthTokens:
        interval_s = device.interval_s
        while _now_ms() < device.expires_at:
            response = await self._post_json(
                "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                    "device_code": device.device_code,
                },
            )
            payload = _json(response)
            if response.is_success and payload.get("access_token"):
                return _parse_tokens(payload)
            error = str(payload.get("error") or payload.get("msg") or "unknown_error")
            if error == "authorization_pending":
                await asyncio.sleep(interval_s)
                continue
            if error == "slow_down":
                interval_s += 5
                await asyncio.sleep(interval_s)
                continue
            raise _oauth_error(response.status_code, payload)
        raise RuntimeError("飞书用户授权链接已过期，请重新发起需要个人数据的请求")

    async def refresh(self, refresh_token: str) -> OAuthTokens:
        payload = await self._request(
            "POST",
            "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "refresh_token": refresh_token,
            },
        )
        return _parse_tokens(payload, refresh_token)

    async def get_user_open_id(self, access_token: str) -> str:
        payload = await self._request(
            "GET",
            "https://open.feishu.cn/open-apis/authen/v1/user_info",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        open_id = _string(payload, "open_id") or _string(data, "open_id")
        if not open_id:
            raise RuntimeError("飞书用户信息响应缺少 open_id")
        return open_id

    async def _post_json(self, url: str, body: dict) -> httpx.Response:
        if self._client is not None:
            response = await self._client.post(url, json=body)
            _capture_request_id("oauth_token", response)
            return response
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=body)
            _capture_request_id("oauth_token", response)
            return response

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        if self._client is not None:
            response = await self._client.request(method, url, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(method, url, **kwargs)
        _capture_request_id(_oauth_operation(url), response)
        payload = _json(response)
        if (
            not response.is_success
            or payload.get("error")
            or (isinstance(payload.get("code"), int) and payload["code"] != 0)
        ):
            raise _oauth_error(response.status_code, payload)
        return payload


def _oauth_operation(url: str) -> str:
    if "device_authorization" in url:
        return "device_authorization"
    if "user_info" in url:
        return "user_info"
    return "oauth_request"


Resume = Callable[[], Awaitable[None]]
Notify = Callable[[IncomingMessage, str], Awaitable[None]]
SendCard = Callable[[IncomingMessage, str, str], Awaitable[None]]


@dataclass
class PendingAuthorization:
    message: IncomingMessage
    domain: str
    scopes: tuple[str, ...]
    resumes: list[tuple[IncomingMessage, Resume]]
    task: Optional[asyncio.Task] = None


class UserAuthorizationManager:
    """为单聊发送者置备独立 Vault，并在鉴权失败后完成增量授权与续跑。"""

    def __init__(self, store, ark, oauth: FeishuOAuth, send_card: SendCard, notify: Notify):
        self._store = store
        self._ark = ark
        self._oauth = oauth
        self._send_card = send_card
        self._notify = notify
        self._provisioning: dict[str, asyncio.Task] = {}
        self._pending: dict[str, PendingAuthorization] = {}

    async def vault_id(self, message: IncomingMessage) -> str:
        current = self._store.get_user_oauth(
            message.tenant_key, message.user_open_id
        )
        if current and current["expires_at"] - _now_ms() > 5 * 60_000:
            return current["vault_id"]
        if current and current.get("refresh_token"):
            try:
                tokens = await self._oauth.refresh(current["refresh_token"])
                await self._ark.update_environment_credential(
                    current["vault_id"], current["credential_id"], tokens.access_token
                )
                self._save_tokens(message, current, tokens, current["scopes"])
                return current["vault_id"]
            except Exception:
                # 保留原 Vault 绑定；lark-cli 会给出结构化 token_missing，再重新授权。
                pass
        return (await self._ensure_credential(message))["vault_id"]

    async def request(
        self,
        message: IncomingMessage,
        domain: str,
        missing_scopes: tuple[str, ...],
        resume: Resume,
    ) -> None:
        if message.chat_type != "p2p":
            raise RuntimeError("群聊不能申请或使用个人用户凭据")
        domain_scopes = DOMAIN_USER_SCOPES.get(domain)
        if domain_scopes is None:
            raise RuntimeError(f"当前不支持为 {domain or '未知'} 域申请用户授权")
        unsupported = set(missing_scopes) - set(domain_scopes)
        if unsupported:
            raise RuntimeError(
                f"{domain} 域请求了未允许的用户权限：{', '.join(sorted(unsupported))}"
            )
        # 方舟 environment_variable Credential 的 secret_value 上限为 4096 字节。
        # 飞书 token 会随 scope 数量增长，因此只签发当前业务域 token，不跨域累加。
        requested_scopes = domain_scopes
        key = f"{self._key(message)}:{domain}"
        pending = self._pending.get(key)
        if pending:
            pending.resumes.append((message, resume))
            return
        pending = PendingAuthorization(
            message=message,
            domain=domain,
            scopes=requested_scopes,
            resumes=[(message, resume)],
        )
        self._pending[key] = pending
        try:
            await self._start_attempt(key, pending)
        except Exception:
            if self._pending.get(key) is pending:
                self._pending.pop(key, None)
            raise

    async def retry(self, message: IncomingMessage) -> bool:
        """取消当前用户的旧 device code，并发送一张带新链接的授权卡片。"""
        prefix = f"{self._key(message)}:"
        matches = [
            (key, pending)
            for key, pending in self._pending.items()
            if key.startswith(prefix)
        ]
        if not matches:
            return False
        for key, pending in matches:
            old_task = pending.task
            pending.task = None
            if old_task and not old_task.done():
                old_task.cancel()
                try:
                    await old_task
                except asyncio.CancelledError:
                    pass
            pending.message = message
            await self._start_attempt(key, pending)
        return True

    async def _start_attempt(
        self, key: str, pending: PendingAuthorization
    ) -> None:
        device = await self._oauth.begin(pending.scopes)
        await self._send_card(
            pending.message, device.verification_url, pending.domain
        )
        pending.task = asyncio.create_task(
            self._complete(key, pending, device)
        )

    async def _complete(
        self,
        key: str,
        pending: PendingAuthorization,
        device: DeviceAuthorization,
    ) -> None:
        try:
            tokens = await self._oauth.poll(device)
            _capture_access_token(tokens.access_token)
            # #region debug-point A:token-size
            _debug_report(
                "A",
                "user_oauth.py:_complete",
                "user access token received",
                {
                    "domain": pending.domain,
                    **_token_diagnostics(tokens.access_token),
                    "scope_count": len(pending.scopes),
                    "vault_limit_bytes": MAX_VAULT_SECRET_BYTES,
                },
            )
            # #endregion
            open_id = await self._oauth.get_user_open_id(tokens.access_token)
            if open_id != pending.message.user_open_id:
                raise RuntimeError("授权账号与消息发送者不一致，请使用发送消息的飞书账号授权")
            credential = await self._ensure_credential(pending.message)
            if len(tokens.access_token.encode("utf-8")) > MAX_VAULT_SECRET_BYTES:
                raise RuntimeError(
                    "当前业务域的用户令牌超过 Vault 4096 字节限制，请联系管理员缩减应用权限"
                )
            await self._ark.update_environment_credential(
                credential["vault_id"], credential["credential_id"], tokens.access_token
            )
            # #region debug-point B:vault-update-success
            _debug_report(
                "B",
                "user_oauth.py:_complete",
                "vault credential update succeeded",
                {"domain": pending.domain},
            )
            # #endregion
            self._save_tokens(
                pending.message, credential, tokens, pending.scopes
            )
            for _queued_message, resume in pending.resumes:
                await resume()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - OAuth 失败须通知用户，不能泄露 token
            # #region debug-point B:authorization-failure
            _debug_report(
                "B",
                "user_oauth.py:_complete",
                "authorization completion failed",
                {
                    "domain": pending.domain,
                    "error_type": type(error).__name__,
                    "error_text": str(error)[:180],
                },
            )
            # #endregion
            for queued_message, _resume in pending.resumes:
                await self._notify(
                    queued_message, f"用户授权未完成：{str(error)[:180]}"
                )
        finally:
            if (
                self._pending.get(key) is pending
                and pending.task is asyncio.current_task()
            ):
                self._pending.pop(key, None)

    async def _ensure_credential(self, message: IncomingMessage) -> dict[str, str]:
        key = self._key(message)
        existing_task = self._provisioning.get(key)
        if existing_task is not None:
            return await existing_task

        async def _provision() -> dict[str, str]:
            current = self._store.get_user_oauth(
                message.tenant_key, message.user_open_id
            )
            if current:
                return {
                    "vault_id": current["vault_id"],
                    "credential_id": current["credential_id"],
                }
            safe_open_id = "".join(
                ch if ch.isalnum() or ch == "-" else "-"
                for ch in message.user_open_id.lower()
            ).strip("-") or "user"
            vault_name = f"ark-employee-user-{safe_open_id}"[:100]
            vault_id = ""
            for vault in await self._ark.list_vaults():
                if vault.get("display_name") == vault_name:
                    vault_id = vault["id"]
                    break
            if not vault_id:
                vault_id = await self._ark.create_vault(vault_name)
            credential_id = ""
            for credential in await self._ark.list_credentials(vault_id):
                if credential.get("secret_name") == USER_ACCESS_TOKEN_ENV:
                    credential_id = credential["id"]
                    break
            if not credential_id:
                credential_id = await self._ark.create_environment_variable_credential(
                    vault_id,
                    USER_CREDENTIAL_NAME,
                    USER_ACCESS_TOKEN_ENV,
                    USER_AUTH_PENDING,
                )
            self._store.save_user_oauth(
                message.tenant_key,
                message.user_open_id,
                vault_id,
                credential_id,
                "",
                0,
                (),
            )
            return {"vault_id": vault_id, "credential_id": credential_id}

        task = asyncio.create_task(_provision())
        self._provisioning[key] = task
        try:
            return await task
        finally:
            self._provisioning.pop(key, None)

    def _save_tokens(
        self,
        message: IncomingMessage,
        credential: dict,
        tokens: OAuthTokens,
        scopes: tuple[str, ...],
    ) -> None:
        self._store.save_user_oauth(
            message.tenant_key,
            message.user_open_id,
            credential["vault_id"],
            credential["credential_id"],
            tokens.refresh_token,
            tokens.expires_at,
            scopes,
        )

    @staticmethod
    def _key(message: IncomingMessage) -> str:
        return f"{message.tenant_key}:{message.user_open_id}"


def _parse_tokens(payload: dict, fallback_refresh: str = "") -> OAuthTokens:
    access_token = _string(payload, "access_token")
    refresh_token = _string(payload, "refresh_token") or fallback_refresh
    if not access_token or not refresh_token:
        raise RuntimeError(
            "飞书 OAuth 响应缺少 access_token 或 refresh_token；请确认包含 offline_access"
        )
    return OAuthTokens(
        access_token,
        refresh_token,
        _now_ms() + _positive_int(payload, "expires_in", 7200) * 1000,
    )


def _json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(f"飞书 OAuth 请求失败 HTTP {response.status_code}") from error
    return payload if isinstance(payload, dict) else {}


def _oauth_error(status: int, payload: dict) -> RuntimeError:
    detail = (
        payload.get("error_description")
        or payload.get("msg")
        or payload.get("error")
        or payload.get("code")
        or "未知错误"
    )
    return RuntimeError(f"飞书 OAuth 请求失败 {status}: {str(detail)[:180]}")


def _string(payload: dict, key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _positive_int(payload: dict, key: str, fallback: int) -> int:
    try:
        value = int(payload.get(key))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _now_ms() -> int:
    return int(time.time() * 1000)
