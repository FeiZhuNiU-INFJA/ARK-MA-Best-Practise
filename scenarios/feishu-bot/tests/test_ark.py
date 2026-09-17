import httpx
import pytest
import respx

from arkagent.ark import (
    ArkClient,
    drain_event_buffer,
    event_progress,
    event_text,
    event_user_authorization_required,
    remember_lark_cli_tool_domain,
    result_from_events,
)

BASE = "https://ark.example/api/v3"


def _client():
    return ArkClient("key", BASE)


# ---- pure SSE helpers ----
def test_drain_event_buffer_parses_split_sse_frames():
    events, rest = drain_event_buffer('data: {"type":"agent.message","id":"1"}\n\ndata: {"type":"session.')
    assert len(events) == 1
    events2, _ = drain_event_buffer(rest + 'status_idle","id":"2"}\n\n')
    assert events2[0]["type"] == "session.status_idle"


def test_drain_event_buffer_parses_ndjson():
    events, _ = drain_event_buffer('{"type":"agent.message","id":"1"}\n{"type":"session.status_idle"}\n')
    assert len(events) == 2


def test_event_text_joins_text_blocks_only():
    assert event_text({"content": [{"type": "text", "text": "甲"}, {"type": "image"}, {"type": "text", "text": "乙"}]}) == "甲\n乙"


def test_event_progress_hides_raw_commands():
    assert event_progress({"type": "agent.tool_use", "name": "bash", "input": {"description": "检查工具", "command": "env | grep TOKEN"}}) == "正在执行：检查工具"
    assert event_progress({"type": "agent.tool_use", "name": "read", "input": {"file_path": "/secret"}}) == "正在调用工具：read"
    assert event_progress({"type": "agent.tool_result", "is_error": True}) == "工具执行未成功，Agent 正在尝试恢复"
    assert event_progress({"type": "agent.thinking"}) is None


def test_detects_structured_lark_user_token_missing_with_domain():
    domains = {}
    remember_lark_cli_tool_domain(
        {
            "type": "agent.tool_use",
            "id": "tool-1",
            "input": {"command": "lark-cli calendar +agenda --as user"},
        },
        domains,
    )
    request = event_user_authorization_required(
        {
            "type": "agent.tool_result",
            "tool_use_id": "tool-1",
            "content": [
                {
                    "type": "text",
                    "text": (
                        'exit_code: 3\n--- stderr ---\n'
                        '{"ok":false,"identity":"user","error":'
                        '{"type":"authentication","subtype":"token_missing"}}'
                    ),
                }
            ],
        },
        domains,
    )
    assert request is not None
    assert request.domain == "calendar"


def test_does_not_treat_unstructured_tool_error_as_authorization():
    request = event_user_authorization_required(
        {
            "type": "agent.tool_result",
            "tool_use_id": "tool-1",
            "content": [{"type": "text", "text": "token_missing"}],
        }
    )
    assert request is None


def test_detects_structured_lark_user_token_invalid():
    request = event_user_authorization_required(
        {
            "type": "agent.tool_result",
            "tool_use_id": "tool-calendar",
            "content": [
                {
                    "type": "text",
                    "text": (
                        'exit_code: 3\n--- stderr ---\n'
                        '{"ok":false,"identity":"user","error":'
                        '{"type":"authentication","subtype":"token_invalid"}}'
                    ),
                }
            ],
        },
        {"tool-calendar": "calendar"},
    )
    assert request is not None
    assert request.subtype == "token_invalid"
    assert request.domain == "calendar"


def test_detects_structured_lark_user_missing_scope():
    request = event_user_authorization_required(
        {
            "type": "agent.tool_result",
            "tool_use_id": "tool-drive",
            "content": [
                {
                    "type": "text",
                    "text": (
                        'exit_code: 3\n--- stderr ---\n'
                        '{"ok":false,"identity":"user","error":'
                        '{"type":"authorization","subtype":"missing_scope",'
                        '"code":99991679,"missing_scopes":["search:docs:read"]}}'
                    ),
                }
            ],
        },
        {"tool-drive": "drive"},
    )
    assert request is not None
    assert request.error_type == "authorization"
    assert request.subtype == "missing_scope"
    assert request.domain == "drive"
    assert request.missing_scopes == ("search:docs:read",)


def test_rejects_missing_scope_without_structured_scope_list():
    request = event_user_authorization_required(
        {
            "type": "agent.tool_result",
            "content": [
                {
                    "type": "text",
                    "text": (
                        'exit_code: 3\n--- stderr ---\n'
                        '{"ok":false,"identity":"user","error":'
                        '{"type":"authorization","subtype":"missing_scope"}}'
                    ),
                }
            ],
        }
    )
    assert request is None


def test_result_from_events_only_recovers_current_run():
    since = int(__import__("datetime").datetime.fromisoformat("2026-07-21T17:00:00+08:00").timestamp() * 1000)
    result = result_from_events(
        [
            {"type": "agent.message", "processed_at": "2026-07-21T16:59:00+08:00", "content": [{"type": "text", "text": "旧回复"}]},
            {"type": "agent.message", "processed_at": "2026-07-21T17:00:01+08:00", "content": [{"type": "text", "text": "新回复"}]},
            {"type": "session.status_idle", "processed_at": "2026-07-21T17:00:02+08:00"},
        ],
        since,
    )
    assert result.terminal == "idle"
    assert result.messages == ["新回复"]


# ---- session binding (卡点 B) ----
@respx.mock
async def test_create_session_injects_openid_via_environment_overrides():
    respx.get(f"{BASE}/environments/env-1").mock(
        return_value=httpx.Response(200, json={"id": "env-1", "config": {
            "type": "cloud", "networking": {"type": "unrestricted"},
            "env": {"KEEP_ME": "yes"},
        }})
    )
    route = respx.post(f"{BASE}/sessions").mock(return_value=httpx.Response(200, json={"id": "sesn-1"}))

    client = _client()
    session_id = await client.create_session(
        "agent-1", "env-1", vault_ids=["vlt-1"], env_overrides={"FEISHU_USER_OPEN_ID": "ou-message-user"}
    )
    await client.aclose()

    assert session_id == "sesn-1"
    body = route.calls.last.request.content
    import json
    sent = json.loads(body)
    assert sent["environment"]["type"] == "environment_with_overrides"
    assert sent["environment"]["config"]["env"] == {"KEEP_ME": "yes", "FEISHU_USER_OPEN_ID": "ou-message-user"}
    assert sent["vault_ids"] == ["vlt-1"]
    assert "environment_id" not in sent


@respx.mock
async def test_create_session_without_overrides_uses_environment_id():
    route = respx.post(f"{BASE}/sessions").mock(return_value=httpx.Response(200, json={"id": "sesn-2"}))
    client = _client()
    await client.create_session("agent-1", "env-1")
    await client.aclose()
    import json
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"agent": "agent-1", "environment_id": "env-1"}


@respx.mock
async def test_create_session_mounts_memory_store_resources():
    route = respx.post(f"{BASE}/sessions").mock(return_value=httpx.Response(200, json={"id": "sesn-3"}))
    client = _client()
    await client.create_session(
        "agent-1", "env-1",
        resources=[{"type": "memory_store", "memory_store_id": "mem-1", "instructions": "先读偏好"}],
    )
    await client.aclose()
    import json
    sent = json.loads(route.calls.last.request.content)
    assert sent["resources"][0]["memory_store_id"] == "mem-1"


# ---- system.message (卡点 C) ----
@respx.mock
async def test_send_message_appends_system_message_last():
    route = respx.post(f"{BASE}/sessions/sesn-1/events").mock(return_value=httpx.Response(200, json={"data": []}))
    client = _client()
    await client.send_message("sesn-1", "你好", system_message="你现在是销售经理")
    await client.aclose()
    import json
    sent = json.loads(route.calls.last.request.content)
    assert [e["type"] for e in sent["events"]] == ["user.message", "system.message"]
    assert sent["events"][-1]["content"][0]["text"] == "你现在是销售经理"


# ---- static bearer (卡点 A) ----
@respx.mock
async def test_create_static_bearer_credential_shape():
    route = respx.post(f"{BASE}/vaults/vlt-1/credentials").mock(return_value=httpx.Response(200, json={"id": "vcrd-1"}))
    client = _client()
    cred = await client.create_static_bearer_credential("vlt-1", "客户A MCP", "https://mcp/mcp", "tok")
    await client.aclose()
    assert cred == "vcrd-1"
    import json
    sent = json.loads(route.calls.last.request.content)
    assert sent["auth"] == {"type": "static_bearer", "mcp_server_url": "https://mcp/mcp", "token": "tok"}


@respx.mock
async def test_static_bearer_handshake_failure_propagates():
    respx.post(f"{BASE}/vaults/vlt-1/credentials").mock(
        return_value=httpx.Response(400, text="mcp unreachable", headers={"x-request-id": "req-9"})
    )
    client = _client()
    with pytest.raises(Exception) as excinfo:
        await client.create_static_bearer_credential("vlt-1", "客户A MCP", "https://mcp/mcp", "tok")
    await client.aclose()
    assert "req-9" in str(excinfo.value)


# ---- lark-cli：环境变量凭据 + 装 CLI 的 Environment（Bot 身份） ----
@respx.mock
async def test_create_environment_variable_credential_shape():
    # App Secret 走 environment_variable 凭据，不绑 MCP；Session 挂上 vault 后注入沙箱环境变量。
    route = respx.post(f"{BASE}/vaults/vlt-1/credentials").mock(
        return_value=httpx.Response(200, json={"id": "vcrd-lark"})
    )
    client = _client()
    cred = await client.create_environment_variable_credential(
        "vlt-1", "lark-cli-bot-app-secret", "LARKSUITE_CLI_APP_SECRET", "s3cr3t"
    )
    await client.aclose()
    assert cred == "vcrd-lark"
    import json
    sent = json.loads(route.calls.last.request.content)
    assert sent["auth"]["type"] == "environment_variable"
    assert sent["auth"]["secret_name"] == "LARKSUITE_CLI_APP_SECRET"
    assert sent["auth"]["secret_value"] == "s3cr3t"


@respx.mock
async def test_update_environment_credential_only_changes_value():
    # 轮换 App Secret：只 POST secret_value，凭据 id 不变、secret_name 不重复发。
    route = respx.post(f"{BASE}/vaults/vlt-1/credentials/vcrd-lark").mock(
        return_value=httpx.Response(200, json={})
    )
    client = _client()
    await client.update_environment_credential("vlt-1", "vcrd-lark", "new-secret")
    await client.aclose()
    import json
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"auth": {"type": "environment_variable", "secret_value": "new-secret"}}


@respx.mock
async def test_create_environment_embeds_setup_script_and_env():
    # 装 lark-cli 的 Environment：setup_script 拉二进制、env 写死 App Id（非敏感）。
    route = respx.post(f"{BASE}/environments").mock(
        return_value=httpx.Response(200, json={"id": "env-lark", "name": "ark-group-bot-app1"})
    )
    client = _client()
    created = await client.create_environment(
        "ark-group-bot-app1",
        env={"LARKSUITE_CLI_APP_ID": "cli_app1"},
        setup_script="echo install lark-cli",
        packages={"pip": ["pypdf==6.19.0"]},
    )
    await client.aclose()
    assert created["id"] == "env-lark"
    import json
    sent = json.loads(route.calls.last.request.content)
    assert sent["config"]["setup_script"] == "echo install lark-cli"
    assert sent["config"]["env"] == {"LARKSUITE_CLI_APP_ID": "cli_app1"}
    assert sent["config"]["packages"] == {"pip": ["pypdf==6.19.0"]}
    assert sent["config"]["type"] == "cloud"


@respx.mock
async def test_update_environment_posts_config():
    route = respx.post(f"{BASE}/environments/env-1").mock(
        return_value=httpx.Response(200, json={"id": "env-1", "name": "group-bot"})
    )
    client = _client()
    result = await client.update_environment(
        "env-1", {"type": "cloud", "packages": {"pip": ["pypdf==6.19.0"]}}
    )
    await client.aclose()
    assert result == {"id": "env-1", "name": "group-bot"}
    import json
    assert json.loads(route.calls.last.request.content) == {
        "config": {
            "type": "cloud",
            "packages": {"pip": ["pypdf==6.19.0"]},
        }
    }


# ---- agent update (更新 Agent，不新建) ----
@respx.mock
async def test_update_agent_sends_version_and_returns_new_version():
    # 方舟更新语义是 POST /agents/{id}（非 PUT/PATCH）。
    route = respx.post(f"{BASE}/agents/agent-1").mock(
        return_value=httpx.Response(200, json={"id": "agent-1", "name": "客户A", "version": 3})
    )
    client = _client()
    result = await client.update_agent("agent-1", {"name": "客户A", "system": "hi"}, version=2)
    await client.aclose()
    assert result == {"id": "agent-1", "name": "客户A", "version": "3"}
    import json
    sent = json.loads(route.calls.last.request.content)
    assert sent["version"] == 2
    assert sent["name"] == "客户A"
    assert sent["system"] == "hi"


# ---- memory store (卡点 D) ----
@respx.mock
async def test_create_memory_store_and_memory():
    respx.post(f"{BASE}/memory_stores").mock(return_value=httpx.Response(200, json={"id": "memstore-1"}))
    mem_route = respx.post(f"{BASE}/memory_stores/memstore-1/memories").mock(return_value=httpx.Response(200, json={"id": "m-1"}))
    client = _client()
    store_id = await client.create_memory_store("张三的记忆", "个人偏好")
    await client.create_memory(store_id, "/prefs.md", "喜欢简洁回复")
    await client.aclose()
    assert store_id == "memstore-1"
    import json
    sent = json.loads(mem_route.calls.last.request.content)
    assert sent == {"path": "/prefs.md", "content": "喜欢简洁回复"}


# ---- run: SSE before send ----
@respx.mock
async def test_run_opens_stream_before_sending_message():
    order: list[str] = []

    def stream_responder(request):
        order.append("stream")
        body = "\n".join([
            'data: {"type":"agent.message","content":[{"type":"text","text":"完成"}]}',
            "",
            'data: {"type":"session.status_idle"}',
            "",
        ])
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    def events_responder(request):
        order.append("events")
        return httpx.Response(200, json={"data": []})

    respx.get(f"{BASE}/sessions/session-1/events/stream").mock(side_effect=stream_responder)
    respx.post(f"{BASE}/sessions/session-1/events").mock(side_effect=events_responder)

    client = _client()
    result = await client.run("session-1", "你好", 5_000)
    await client.aclose()
    assert order == ["stream", "events"]
    assert result.terminal == "idle"
    assert result.messages == ["完成"]


# ---- files & session resources (多模态：上传文件 + 挂载到 Session 文件系统) ----
@respx.mock
async def test_upload_file_posts_multipart_with_purpose_agent():
    route = respx.post(f"{BASE}/files").mock(return_value=httpx.Response(200, json={"id": "file-1"}))
    client = _client()
    file_id = await client.upload_file("report.pdf", "application/pdf", b"%PDF-1.7 ...")
    await client.aclose()

    assert file_id == "file-1"
    req = route.calls.last.request
    # multipart/form-data：purpose=agent + 文件字段一起走 body。
    assert req.headers["content-type"].startswith("multipart/form-data")
    raw = req.content
    assert b'name="purpose"' in raw and b"agent" in raw
    assert b'filename="report.pdf"' in raw
    assert b"%PDF-1.7" in raw


@respx.mock
async def test_upload_file_raises_on_missing_id():
    respx.post(f"{BASE}/files").mock(return_value=httpx.Response(200, json={}))
    client = _client()
    with pytest.raises(Exception) as excinfo:
        await client.upload_file("a.txt", "text/plain", b"hi")
    await client.aclose()
    assert "File ID" in str(excinfo.value)


@respx.mock
async def test_upload_file_propagates_http_error():
    respx.post(f"{BASE}/files").mock(
        return_value=httpx.Response(413, text="too large", headers={"x-request-id": "req-up"})
    )
    client = _client()
    with pytest.raises(Exception) as excinfo:
        await client.upload_file("big.bin", "application/octet-stream", b"x" * 10)
    await client.aclose()
    err = str(excinfo.value)
    assert "413" in err and "req-up" in err


@respx.mock
async def test_add_session_file_mounts_to_uploads_path():
    route = respx.post(f"{BASE}/sessions/sesn-1/resources").mock(
        return_value=httpx.Response(200, json={})
    )
    client = _client()
    await client.add_session_file("sesn-1", "file-1", "abc123/report.pdf")
    await client.aclose()
    import json
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"type": "file", "file_id": "file-1", "mount_path": "abc123/report.pdf"}


@respx.mock
async def test_add_session_resource_url_encodes_session_id():
    # session_id 里若含特殊字符，路径应转义（safe='' → 连 / 也编码）。
    route = respx.post(f"{BASE}/sessions/sesn%2F1/resources").mock(
        return_value=httpx.Response(200, json={})
    )
    client = _client()
    await client.add_session_resource("sesn/1", {"type": "file", "file_id": "f-1"})
    await client.aclose()
    assert route.called
