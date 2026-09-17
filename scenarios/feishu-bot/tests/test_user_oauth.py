"""单聊用户 OAuth 与每用户 Vault 编排测试。"""
import asyncio
import base64
import os
import sys
import time
from pathlib import Path

_GROUP_BOT_DIR = Path(__file__).resolve().parents[1] / "cases" / "group-bot"
if str(_GROUP_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_GROUP_BOT_DIR))

import shared  # noqa: E402
from user_oauth import (  # noqa: E402
    CALENDAR_USER_SCOPES,
    DeviceAuthorization,
    DRIVE_USER_SCOPES,
    OAuthTokens,
    USER_ACCESS_TOKEN_ENV,
    USER_AUTH_PENDING,
    UserAuthorizationManager,
    _capture_access_token,
    _token_diagnostics,
)

from arkagent.feishu import IncomingMessage  # noqa: E402


def _message(open_id: str = "ou-alice") -> IncomingMessage:
    return IncomingMessage(
        event_id="ev-1",
        message_id="om-1",
        chat_id="oc-direct",
        chat_type="p2p",
        thread_id="",
        user_open_id=open_id,
        user_name="Alice",
        tenant_key="tenant-1",
        text="查看我的日程",
        mentioned_bot=False,
        create_time=1000,
    )


class FakeArk:
    def __init__(self):
        self.vaults = []
        self.credentials = []
        self.updated = []

    async def list_vaults(self):
        return list(self.vaults)

    async def create_vault(self, name):
        self.vaults.append({"id": "vlt-user", "display_name": name})
        return "vlt-user"

    async def list_credentials(self, _vault_id):
        return list(self.credentials)

    async def create_environment_variable_credential(
        self, vault_id, display_name, secret_name, secret_value
    ):
        self.credentials.append(
            {
                "id": "cred-user",
                "display_name": display_name,
                "secret_name": secret_name,
                "secret_value": secret_value,
            }
        )
        return "cred-user"

    async def update_environment_credential(self, vault_id, credential_id, value):
        self.updated.append((vault_id, credential_id, value))


class FakeOAuth:
    def __init__(self, open_id="ou-alice"):
        self.open_id = open_id
        self.requested_scopes = []

    async def begin(self, scopes):
        self.requested_scopes.append(scopes)
        return DeviceAuthorization("device", "https://auth.example", int(time.time() * 1000) + 60_000, 0)

    async def poll(self, _device):
        return OAuthTokens("access-token", "refresh-token", int(time.time() * 1000) + 7_200_000)

    async def get_user_open_id(self, _access_token):
        return self.open_id

    async def refresh(self, _refresh_token):
        return OAuthTokens("refreshed-access", "refreshed-refresh", int(time.time() * 1000) + 7_200_000)


def test_token_diagnostics_exposes_jwt_shape_without_token_contents():
    def encode(value):
        return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")

    token = ".".join(
        (
            encode('{"alg":"RS256","kid":"key-1"}'),
            encode('{"sub":"user-1","scope":"search:docs:read"}'),
            "signature",
        )
    )
    diagnostics = _token_diagnostics(token)

    assert diagnostics["is_jwt"] is True
    assert diagnostics["jwt_header_keys"] == ["alg", "kid"]
    assert diagnostics["jwt_claim_keys"] == ["scope", "sub"]
    assert token not in str(diagnostics)
    assert "user-1" not in str(diagnostics)


def test_capture_access_token_writes_owner_only_file(tmp_path, monkeypatch):
    path = tmp_path / "access-token"
    monkeypatch.setenv("OAUTH_DEBUG_ACCESS_TOKEN_PATH", str(path))

    _capture_access_token("full-access-token")

    assert path.read_text() == "full-access-token"
    assert os.stat(path).st_mode & 0o777 == 0o600


async def test_vault_id_provisions_placeholder_credential():
    store = shared.InMemorySessionMap()
    ark = FakeArk()
    manager = UserAuthorizationManager(store, ark, FakeOAuth(), _noop_card, _noop_notify)

    assert await manager.vault_id(_message()) == "vlt-user"
    assert ark.credentials[0]["secret_name"] == USER_ACCESS_TOKEN_ENV
    assert ark.credentials[0]["secret_value"] == USER_AUTH_PENDING
    assert store.get_user_oauth("tenant-1", "ou-alice")["vault_id"] == "vlt-user"


async def test_authorization_updates_credential_and_resumes():
    store = shared.InMemorySessionMap()
    ark = FakeArk()
    cards = []
    resumed = []

    async def send_card(_message, url, domain):
        cards.append((url, domain))

    async def resume():
        resumed.append(True)

    manager = UserAuthorizationManager(store, ark, FakeOAuth(), send_card, _noop_notify)
    await manager.vault_id(_message())
    await manager.request(_message(), "calendar", (), resume)
    for _ in range(20):
        if resumed:
            break
        await asyncio.sleep(0)

    assert cards == [("https://auth.example", "calendar")]
    assert resumed == [True]
    assert ark.updated == [("vlt-user", "cred-user", "access-token")]
    assert store.get_user_oauth("tenant-1", "ou-alice")["refresh_token"] == "refresh-token"


async def test_authorization_rejects_different_account():
    store = shared.InMemorySessionMap()
    notices = []
    resumed = []

    async def notify(_message, text):
        notices.append(text)

    async def resume():
        resumed.append(True)

    manager = UserAuthorizationManager(
        store, FakeArk(), FakeOAuth("ou-other"), _noop_card, notify
    )
    await manager.request(_message(), "calendar", (), resume)
    for _ in range(20):
        if notices:
            break
        await asyncio.sleep(0)

    assert resumed == []
    assert "授权账号与消息发送者不一致" in notices[0]


async def test_drive_missing_scope_uses_domain_only_scopes():
    store = shared.InMemorySessionMap()
    store.save_user_oauth(
        "tenant-1",
        "ou-alice",
        "vlt-user",
        "cred-user",
        "refresh-token",
        int(time.time() * 1000) + 7_200_000,
        CALENDAR_USER_SCOPES,
    )
    oauth = FakeOAuth()
    resumed = []

    async def resume():
        resumed.append(True)

    manager = UserAuthorizationManager(store, FakeArk(), oauth, _noop_card, _noop_notify)
    await manager.request(
        _message(), "drive", ("search:docs:read",), resume
    )
    for _ in range(20):
        if resumed:
            break
        await asyncio.sleep(0)

    assert oauth.requested_scopes == [DRIVE_USER_SCOPES]
    assert store.get_user_oauth("tenant-1", "ou-alice")["scopes"] == DRIVE_USER_SCOPES


async def test_retry_cancels_old_device_code_and_sends_new_card():
    class RetryOAuth(FakeOAuth):
        def __init__(self):
            super().__init__()
            self.polling = []

        async def begin(self, scopes):
            self.requested_scopes.append(scopes)
            index = len(self.requested_scopes)
            return DeviceAuthorization(
                f"device-{index}",
                f"https://auth.example/{index}",
                int(time.time() * 1000) + 60_000,
                0,
            )

        async def poll(self, device):
            self.polling.append(device.device_code)
            if device.device_code == "device-1":
                await asyncio.Event().wait()
            return OAuthTokens(
                "access-token",
                "refresh-token",
                int(time.time() * 1000) + 7_200_000,
            )

    cards = []
    resumed = []

    async def send_card(_message, url, domain):
        cards.append((url, domain))

    async def resume():
        resumed.append(True)

    oauth = RetryOAuth()
    manager = UserAuthorizationManager(
        shared.InMemorySessionMap(), FakeArk(), oauth, send_card, _noop_notify
    )
    await manager.request(_message(), "drive", ("search:docs:read",), resume)
    await asyncio.sleep(0)
    assert await manager.retry(_message()) is True
    for _ in range(20):
        if resumed:
            break
        await asyncio.sleep(0)

    assert cards == [
        ("https://auth.example/1", "drive"),
        ("https://auth.example/2", "drive"),
    ]
    assert oauth.polling == ["device-1", "device-2"]
    assert resumed == [True]


async def test_rejects_unapproved_missing_scope():
    manager = UserAuthorizationManager(
        shared.InMemorySessionMap(),
        FakeArk(),
        FakeOAuth(),
        _noop_card,
        _noop_notify,
    )
    try:
        await manager.request(
            _message(), "drive", ("drive:drive:write",), lambda: None
        )
    except RuntimeError as error:
        assert "未允许的用户权限" in str(error)
    else:
        raise AssertionError("unapproved user scope should be rejected")


async def _noop_card(_message, _url, _domain):
    return None


async def _noop_notify(_message, _text):
    return None
