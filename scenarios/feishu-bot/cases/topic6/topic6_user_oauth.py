"""Topic6 妙搭用户 OAuth、每用户 Vault 与短期 token 刷新。"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import httpx

log = logging.getLogger("arkagent.case.topic6.oauth")

USER_ACCESS_TOKEN_ENV = "LARKSUITE_CLI_USER_ACCESS_TOKEN"
USER_CREDENTIAL_NAME = "topic6-miaoda-user-access-token"
APPS_USER_SCOPES = (
    "offline_access",
    "auth:user.id:read",
    "spark:app:read",
    "spark:app:write",
)
MAX_VAULT_SECRET_BYTES = 4096
# 给 Vault 周期性重新解析留出传播窗口，避免长任务在 Phase H 临界过期。
REFRESH_EARLY_MS = 15 * 60_000


class UserAuthorizationRequired(RuntimeError):
    """当前消息发送者尚无可用的妙搭用户授权。"""


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
    """飞书 OAuth 2.0 Device Flow 的最小客户端。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._client = client

    async def begin(
        self, scopes: tuple[str, ...] = APPS_USER_SCOPES
    ) -> DeviceAuthorization:
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
        raise RuntimeError("飞书用户授权链接已过期，请重新运行授权脚本")

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
            return await self._client.post(url, json=body)
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(url, json=body)

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        if self._client is not None:
            response = await self._client.request(method, url, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(method, url, **kwargs)
        payload = _json(response)
        if (
            not response.is_success
            or payload.get("error")
            or (isinstance(payload.get("code"), int) and payload["code"] != 0)
        ):
            raise _oauth_error(response.status_code, payload)
        return payload


DeviceReady = Callable[[DeviceAuthorization], Optional[Awaitable[None]]]


class Topic6UserAuthorization:
    """把扫码用户的短期 token 放进独立 Ark Vault，并维护 refresh token。"""

    def __init__(self, store, ark, oauth: FeishuOAuth) -> None:
        self._store = store
        self._ark = ark
        self._oauth = oauth
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    async def authorize(
        self,
        *,
        expected_open_id: str = "",
        on_device_ready: Optional[DeviceReady] = None,
    ) -> str:
        device = await self._oauth.begin(APPS_USER_SCOPES)
        if on_device_ready is not None:
            result = on_device_ready(device)
            if inspect.isawaitable(result):
                await result
        tokens = await self._oauth.poll(device)
        _validate_access_token_size(tokens.access_token)
        open_id = await self._oauth.get_user_open_id(tokens.access_token)
        if expected_open_id and open_id != expected_open_id:
            raise RuntimeError(
                "扫码账号与 --expected-open-id 不一致，未写入任何凭据"
            )
        credential = await self._ensure_credential(open_id, tokens.access_token)
        self._store.save_user_oauth(
            open_id,
            credential["vault_id"],
            credential["credential_id"],
            tokens.refresh_token,
            tokens.expires_at,
            APPS_USER_SCOPES,
        )
        return open_id

    async def vault_id(self, open_id: str) -> str:
        """返回该用户的可用 Vault；必要时先刷新短期 access token。"""
        current = self._store.get_user_oauth(open_id)
        if not current or not current.get("refresh_token"):
            raise UserAuthorizationRequired(
                "当前飞书账号尚未授权妙搭发布。请先在 Gateway 主机运行 "
                "`python cases/topic6/authorize_miaoda_user.py` 完成扫码授权。"
            )
        if current["expires_at"] - _now_ms() > REFRESH_EARLY_MS:
            return current["vault_id"]

        lock = self._refresh_locks.setdefault(open_id, asyncio.Lock())
        async with lock:
            current = self._store.get_user_oauth(open_id)
            if current and current["expires_at"] - _now_ms() > REFRESH_EARLY_MS:
                return current["vault_id"]
            try:
                tokens = await self._oauth.refresh(current["refresh_token"])
                _validate_access_token_size(tokens.access_token)
                await self._ark.update_environment_credential(
                    current["vault_id"],
                    current["credential_id"],
                    tokens.access_token,
                )
            except Exception as error:
                raise UserAuthorizationRequired(
                    "当前飞书账号的妙搭授权已失效。请重新运行 "
                    "`python cases/topic6/authorize_miaoda_user.py` 扫码授权。"
                ) from error
            self._store.save_user_oauth(
                open_id,
                current["vault_id"],
                current["credential_id"],
                tokens.refresh_token,
                tokens.expires_at,
                tuple(current["scopes"]),
            )
            log.info(
                "topic6 user token refreshed user=%s vault=%s "
                "credential=%s expires_at=%s",
                open_id,
                current["vault_id"],
                current["credential_id"],
                tokens.expires_at,
            )
            return current["vault_id"]

    async def _ensure_credential(
        self, open_id: str, access_token: str
    ) -> dict[str, str]:
        current = self._store.get_user_oauth(open_id)
        if current:
            await self._ark.update_environment_credential(
                current["vault_id"], current["credential_id"], access_token
            )
            return {
                "vault_id": current["vault_id"],
                "credential_id": current["credential_id"],
            }

        safe_open_id = re.sub(r"[^a-z0-9-]+", "-", open_id.lower()).strip("-")
        vault_name = f"ark-topic6-miaoda-user-{safe_open_id or 'user'}"[:100]
        vault_id = next(
            (
                str(vault["id"])
                for vault in await self._ark.list_vaults()
                if vault.get("display_name") == vault_name
            ),
            "",
        )
        if not vault_id:
            vault_id = await self._ark.create_vault(vault_name)

        credential_id = next(
            (
                str(credential["id"])
                for credential in await self._ark.list_credentials(vault_id)
                if credential.get("secret_name") == USER_ACCESS_TOKEN_ENV
            ),
            "",
        )
        if credential_id:
            await self._ark.update_environment_credential(
                vault_id, credential_id, access_token
            )
        else:
            credential_id = await self._ark.create_environment_variable_credential(
                vault_id,
                USER_CREDENTIAL_NAME,
                USER_ACCESS_TOKEN_ENV,
                access_token,
            )
        return {"vault_id": vault_id, "credential_id": credential_id}


def _validate_access_token_size(access_token: str) -> None:
    if len(access_token.encode("utf-8")) > MAX_VAULT_SECRET_BYTES:
        raise RuntimeError(
            "飞书用户令牌超过 Ark Vault 4096 字节限制，请缩减应用用户权限"
        )


def _parse_tokens(payload: dict, fallback_refresh: str = "") -> OAuthTokens:
    access_token = _string(payload, "access_token")
    refresh_token = _string(payload, "refresh_token") or fallback_refresh
    if not access_token or not refresh_token:
        raise RuntimeError(
            "飞书 OAuth 响应缺少 access_token 或 refresh_token；"
            "请确认应用已开通 offline_access"
        )
    return OAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=_now_ms() + _positive_int(payload, "expires_in", 7200) * 1000,
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
