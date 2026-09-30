"""Topic6 妙搭用户 OAuth、Vault 与 Gateway 接入测试。"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from arkagent.feishu import IncomingMessage

CASE_DIR = Path(__file__).resolve().parents[1] / "cases" / "topic6"


def _load(module_name: str, file_name: str):
    if str(CASE_DIR) not in sys.path:
        sys.path.insert(0, str(CASE_DIR))
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, CASE_DIR / file_name)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


pipeline_store = _load("pipeline_store", "pipeline_store.py")
oauth_module = _load("topic6_user_oauth", "topic6_user_oauth.py")
gateway_module = _load("topic6_gateway_for_oauth_test", "gateway.py")
runner_module = sys.modules["topic6_runner"]

APPS_USER_SCOPES = oauth_module.APPS_USER_SCOPES
DeviceAuthorization = oauth_module.DeviceAuthorization
OAuthTokens = oauth_module.OAuthTokens
Topic6UserAuthorization = oauth_module.Topic6UserAuthorization
UserAuthorizationRequired = oauth_module.UserAuthorizationRequired
PipelineStore = pipeline_store.PipelineStore
Topic6Gateway = gateway_module.Topic6Gateway
Topic6Runner = runner_module.Topic6Runner
RunnerConfig = runner_module.Topic6Config


class FakeArk:
    def __init__(self):
        self.vaults = []
        self.credentials = []
        self.created_vaults = []
        self.created_credentials = []
        self.updated = []
        self.session_vault_ids = None

    async def list_vaults(self):
        return list(self.vaults)

    async def create_vault(self, name):
        vault_id = f"vlt-{len(self.vaults) + 1}"
        self.vaults.append({"id": vault_id, "display_name": name})
        self.created_vaults.append(name)
        return vault_id

    async def list_credentials(self, vault_id):
        return [item for item in self.credentials if item["vault_id"] == vault_id]

    async def create_environment_variable_credential(
        self, vault_id, display_name, secret_name, secret_value
    ):
        credential_id = f"cred-{len(self.credentials) + 1}"
        item = {
            "id": credential_id,
            "vault_id": vault_id,
            "display_name": display_name,
            "secret_name": secret_name,
        }
        self.credentials.append(item)
        self.created_credentials.append(
            (vault_id, display_name, secret_name, secret_value)
        )
        return credential_id

    async def update_environment_credential(
        self, vault_id, credential_id, secret_value
    ):
        self.updated.append((vault_id, credential_id, secret_value))

    async def create_session(
        self,
        _agent_id,
        _environment_id,
        vault_ids=None,
        env_overrides=None,
        resources=None,
    ):
        _ = env_overrides, resources
        self.session_vault_ids = vault_ids
        return "sesn-topic6"


class FakeOAuth:
    def __init__(self, open_id="ou-scanned"):
        self.open_id = open_id
        self.poll_count = 0
        self.refresh_count = 0
        self.requested_scopes = []

    async def begin(self, scopes):
        self.requested_scopes.append(scopes)
        return DeviceAuthorization(
            "device-code",
            "https://accounts.example/device",
            int(time.time() * 1000) + 60_000,
            0,
        )

    async def poll(self, _device):
        self.poll_count += 1
        return OAuthTokens(
            f"access-{self.poll_count}",
            f"refresh-{self.poll_count}",
            int(time.time() * 1000) + 7_200_000,
        )

    async def get_user_open_id(self, _access_token):
        return self.open_id

    async def refresh(self, _refresh_token):
        self.refresh_count += 1
        return OAuthTokens(
            "access-refreshed",
            "refresh-rotated",
            int(time.time() * 1000) + 7_200_000,
        )


async def test_authorize_auto_detects_open_id_and_is_idempotent(tmp_path):
    store = PipelineStore(str(tmp_path / "topic6.db"))
    ark = FakeArk()
    oauth = FakeOAuth()
    authorization = Topic6UserAuthorization(store, ark, oauth)
    shown = []

    assert await authorization.authorize(
        on_device_ready=lambda device: shown.append(device.verification_url)
    ) == "ou-scanned"
    assert await authorization.authorize() == "ou-scanned"

    saved = store.get_user_oauth("ou-scanned")
    assert oauth.requested_scopes == [APPS_USER_SCOPES, APPS_USER_SCOPES]
    assert shown == ["https://accounts.example/device"]
    assert saved["refresh_token"] == "refresh-2"
    assert saved["scopes"] == APPS_USER_SCOPES
    assert len(ark.created_vaults) == 1
    assert len(ark.created_credentials) == 1
    assert ark.updated == [("vlt-1", "cred-1", "access-2")]
    store.close()


async def test_expected_open_id_mismatch_writes_nothing(tmp_path):
    store = PipelineStore(str(tmp_path / "topic6.db"))
    ark = FakeArk()
    authorization = Topic6UserAuthorization(store, ark, FakeOAuth("ou-other"))

    with pytest.raises(RuntimeError, match="expected-open-id"):
        await authorization.authorize(expected_open_id="ou-expected")

    assert store.get_user_oauth("ou-other") is None
    assert ark.created_vaults == []
    store.close()


async def test_expired_token_is_refreshed_in_place(tmp_path):
    store = PipelineStore(str(tmp_path / "topic6.db"))
    store.save_user_oauth(
        "ou-scanned",
        "vlt-existing",
        "cred-existing",
        "refresh-old",
        1,
        APPS_USER_SCOPES,
    )
    ark = FakeArk()
    oauth = FakeOAuth()
    authorization = Topic6UserAuthorization(store, ark, oauth)

    assert await authorization.vault_id("ou-scanned") == "vlt-existing"
    assert oauth.refresh_count == 1
    assert ark.updated == [
        ("vlt-existing", "cred-existing", "access-refreshed")
    ]
    assert store.get_user_oauth("ou-scanned")["refresh_token"] == "refresh-rotated"
    store.close()


async def test_missing_authorization_is_rejected_before_job_start():
    replies = []

    class Store:
        def complete_event(self, *_args):
            pass

    class Runner:
        async def start_job(self, **_kwargs):
            raise AssertionError("未授权时不应创建任务")

    class Hitl:
        async def handle_remark_message(self, **_kwargs):
            return False

    class UserAuth:
        async def vault_id(self, _open_id):
            raise UserAuthorizationRequired("请先扫码授权")

    async def reply(_chat_id, text):
        replies.append(text)

    gateway = Topic6Gateway(
        SimpleNamespace(authorized_open_ids=()),
        Store(),
        Runner(),
        Hitl(),
        UserAuth(),
        reply,
        None,
    )
    await gateway._process(_message())
    assert replies == ["请先扫码授权"]


async def test_gateway_passes_scanned_users_vault_to_runner():
    calls = []

    class Store:
        def complete_event(self, *_args):
            pass

    class Runner:
        async def start_job(self, **kwargs):
            calls.append(kwargs)

    class Hitl:
        async def handle_remark_message(self, **_kwargs):
            return False

    class UserAuth:
        async def vault_id(self, open_id):
            assert open_id == "ou-scanned"
            return "vlt-user"

    async def reply(_chat_id, _text):
        pass

    gateway = Topic6Gateway(
        SimpleNamespace(authorized_open_ids=()),
        Store(),
        Runner(),
        Hitl(),
        UserAuth(),
        reply,
        None,
    )
    await gateway._process(_message())
    assert calls[0]["user_vault_id"] == "vlt-user"


async def test_runner_mounts_static_and_user_vaults_on_new_session(tmp_path):
    class Sender:
        def send_interactive_card(self, _chat_id, _card):
            return ""

    store = PipelineStore(str(tmp_path / "topic6.db"))
    ark = FakeArk()
    runner = Topic6Runner(
        ark,
        Sender(),
        store,
        RunnerConfig(
            coordinator_agent_id="agent-1",
            environment_id="env-1",
            vault_ids=("vlt-static",),
        ),
    )
    runner._spawn_stream = lambda *_args, **_kwargs: object()

    await runner.start_job(
        chat_id="chat-1",
        thread_id="",
        user_open_id="ou-scanned",
        mode="demo",
        user_message="热点周报 demo",
        user_vault_id="vlt-user",
    )

    assert ark.session_vault_ids == ["vlt-static", "vlt-user"]
    store.close()


def _message() -> IncomingMessage:
    return IncomingMessage(
        event_id="event-1",
        message_id="message-1",
        chat_id="chat-1",
        chat_type="p2p",
        thread_id="",
        user_open_id="ou-scanned",
        user_name="扫码用户",
        tenant_key="tenant-1",
        text="热点周报 demo",
        mentioned_bot=False,
    )
