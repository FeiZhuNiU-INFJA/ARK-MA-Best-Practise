"""Seedance 接入链路验证：模拟 MA 与飞书，不调用生成模型。"""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from arkagent.ark import ArkClient, ArkError, ArkRunTimeout, RunResult
from arkagent.case_registry import list_cases, load_case
from arkagent.config import parse_env_text, serialize_env
from arkagent.feishu import FeishuSender, IncomingMessage, ResourceRef
from cases.seedance.config import load_case_config
from cases.seedance.cards import build_card
from cases.seedance.gateway import SeedanceGateway, conversation
from cases.seedance.resources import build_payloads, provision
from cases.seedance.store import SeedanceStore
from cases.seedance.progress import ProgressReporter
from cases.seedance.protocol import (
    TURN_CONTRACT, current_events, deliverables, select_files, stage_label,
)


def env(**updates):
    return {
        "ARK_API_KEY": "test-key", "FEISHU_APP_ID": "test-app",
        "FEISHU_APP_SECRET": "test-secret", "SEEDANCE_AGENT_ID": "agent-test",
        "SEEDANCE_ENVIRONMENT_ID": "env-test", "SEEDANCE_AGENT_MODEL_ID": "main-model",
        "SEEDANCE_IMAGE_MODEL_ID": "image-model", "SEEDANCE_VIDEO_MODEL_ID": "video-model",
        "SEEDANCE_ARTIFACT_POLL_ATTEMPTS": "3", "SEEDANCE_ARTIFACT_POLL_INTERVAL_MS": "1",
        **updates,
    }


def message(number=1, **updates):
    return IncomingMessage(
        event_id=f"event-{number}", message_id=f"message-{number}", chat_id="chat-test",
        chat_type="p2p", thread_id="", user_open_id="user-test", user_name="测试用户",
        tenant_key="test", text="生成咖啡图片和视频", mentioned_bot=False, **updates,
    )


def artifact(ident="image-1", name="visual.jpg", **updates):
    return {
        "id": ident, "filename": name, "status": "active", "bytes": 4,
        "download_url": f"https://files.example/{ident}", **updates,
    }


class FakeArk:
    def __init__(self):
        self.created = []
        self.runs = []
        self.uploads = []
        self.status = "idle"
        self.files = [artifact(), artifact("video-1", "teaser.mp4")]
        self.pages = []
        self.error = None
        self.events = []
        self.run_options = []
        self.last_reply = (
            "完成：[图片](sandbox:/mnt/session/outputs/visual.jpg) "
            "[视频](sandbox:/mnt/session/outputs/teaser.mp4)"
        )

    async def create_session(self, *args, **kwargs):
        self.created.append((args, kwargs))
        return f"session-{len(self.created)}"

    async def get_session(self, _ident):
        return {"status": self.status}

    async def run(self, session_id, prompt, timeout, **kwargs):
        self.runs.append((session_id, prompt, timeout))
        self.run_options.append(kwargs)
        self.events.append(history_event("user.message", prompt))
        if self.error:
            raise self.error
        self.events.extend([
            history_event("agent.message", self.last_reply),
            history_event("session.status_idle"),
        ])
        return RunResult(terminal="idle", messages=[self.last_reply])

    async def list_session_events(self, _session_id):
        return list(self.events)

    async def list_agent_files(self, _session_id):
        return self.pages.pop(0) if self.pages else list(self.files)

    async def download_file(self, _url, **kwargs):
        return b"data"

    async def upload_file(self, name, mime, data):
        self.uploads.append((name, mime, data))
        return f"input-{len(self.uploads)}"

    async def add_session_file(self, *_args):
        raise AssertionError("必须在创建 Session 时挂载，禁止动态添加")


class FakeSender:
    def __init__(self):
        self.texts, self.images, self.files = [], [], []
        self.uploads = []
        self.fail_upload = False
        self.quoted = None
        self.cards, self.patches, self.card_snapshots = [], [], []
        self.fail_card = False
        self.fail_patch = False
        self.thread_texts = []

    @property
    def card_texts(self):
        return [json.dumps(card, ensure_ascii=False) for card in self.card_snapshots]

    def send_interactive_card(self, chat_id, card):
        if self.fail_card:
            raise RuntimeError("card failed")
        ident = f"card-{len(self.cards) + 1}"
        self.cards.append((chat_id, ident))
        self.card_snapshots.append(json.loads(json.dumps(card)))
        return ident

    def send_interactive_card_in_thread(self, message_id, card):
        return self.send_interactive_card(message_id, card), f"topic-{message_id}"

    def patch_interactive_card(self, message_id, card):
        if self.fail_patch:
            raise RuntimeError("patch failed")
        self.patches.append(message_id)
        self.card_snapshots.append(json.loads(json.dumps(card)))

    def reply(self, message_id, text):
        self.texts.append((message_id, text))

    def reply_in_thread(self, message_id, text):
        self.thread_texts.append((message_id, text))
        self.reply(message_id, text)
        return f"topic-{message_id}"

    def upload_image(self, data):
        if self.fail_upload:
            raise RuntimeError("upload failed")
        self.uploads.append(("image", data))
        return "image-key"

    def upload_file(self, name, data, file_type):
        self.uploads.append((file_type, name, data))
        return "file-key"

    def reply_image(self, *args):
        self.images.append(args)

    def reply_file(self, *args):
        self.files.append(args)

    def get_message(self, _message_id):
        return self.quoted

    def download_resource(self, *_args):
        return b"reference"


def gateway(tmp_path, **config):
    ark, sender = FakeArk(), FakeSender()
    store = SeedanceStore(str(tmp_path / "seedance.db"))
    bot = SeedanceGateway(load_case_config(env(**config)), ark, sender, asyncio.get_running_loop(), store)
    return bot, ark, sender


def history_event(kind, text="", **updates):
    return {
        "id": uuid4().hex, "type": kind, "processed_at": datetime.now(timezone.utc).isoformat(),
        "content": [{"type": "text", "text": text}], **updates,
    }


async def wait_state(bot, predicate):
    while not predicate(bot.store.card(conversation(message()))):
        await asyncio.sleep(0)


def test_case_discovery_and_official_configuration():
    assert "seedance" in list_cases()
    assert load_case("seedance").gateway_module.__name__ == "cases.seedance.gateway"
    agent, environment = build_payloads(env())
    assert agent["base_agent"] == "ultra_vision_agent_preview"
    assert agent["model"]["reasoning_effort"] == "high"
    assert agent["tools"][0]["configs"] == [{"name": "web_search", "enabled": False}]
    assert "env" not in environment["config"]
    assert "test-secret" not in json.dumps(agent)
    with pytest.raises(RuntimeError, match="SEEDANCE_VIDEO_MODEL_ID"):
        build_payloads(env(SEEDANCE_VIDEO_MODEL_ID="${SEEDANCE_VIDEO_MODEL_ID}"))
    with pytest.raises(RuntimeError, match="SEEDANCE_SESSION_TIMEOUT_MS"):
        load_case_config(env(SEEDANCE_SESSION_TIMEOUT_MS="0"))
    assert deliverables(
        "[图](sandbox:/mnt/session/outputs/%E4%B8%BB%E8%A7%86%E8%A7%89.jpg)", final=True,
    ) == {"主视觉.jpg"}


async def test_generation_round_trip_reuse_dedup_and_explicit_files(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    await bot.handle(message())
    assert len(ark.created) == len(ark.runs) == 1
    assert len(sender.images) == len(sender.files) == 1
    assert sender.uploads[-1][0] == "mp4"
    assert not sender.texts
    assert not any("sandbox:" in text for text in sender.card_texts)
    assert not any("部分文件" in text for text in sender.card_texts)
    assert len(sender.cards) == 1 and set(sender.patches) == {"card-1"}
    assert sender.card_snapshots[-1]["header"]["template"] == "green"
    await bot.handle(message())  # 重复飞书事件不再生成或发送
    assert len(ark.runs) == 1
    await bot.handle(replace(message(2), text="把光线改成傍晚"))
    assert len(ark.created) == 1 and len(ark.runs) == 2
    assert len(sender.images) == 1  # 原产物不会在下一轮重复回传
    await bot.handle(replace(message(3), text="/files"))
    assert len(ark.runs) == 2 and len(sender.images) == 2
    assert len(sender.cards) == 1


async def test_new_reference_recreates_session_and_carries_context(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    await bot.handle(message())
    reference = ResourceRef("ref-key", "../../brand.jpg", "image")
    await bot.handle(replace(message(2), text="", content_type="image", resources=(reference,)))
    assert len(ark.runs) == 1 and not ark.uploads
    await bot.handle(replace(message(3), text="按新参考图重新创作"))
    assert len(ark.created) == 2
    mounted = ark.created[-1][1]["resources"]
    assert {item["file_id"] for item in mounted} == {"input-1", "image-1", "video-1"}
    assert all(item["mount_path"].startswith("/mnt/session/uploads/") for item in mounted)
    assert not any("../" in item["mount_path"] for item in mounted)
    assert "最近创作上下文" in ark.runs[-1][1]
    assert ark.uploads[0][0] == "brand.jpg"
    await bot.handle(replace(message(4), text="再改一次"))
    assert len(ark.created) == 2 and len(ark.uploads) == 1


async def test_explicit_quote_uses_current_chat_only(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    # 走真实 FeishuSender 的 SDK 对象归一化，避免仅用手写 dict 掩盖字段缺失。
    sdk_item = SimpleNamespace(
        message_id="quoted", chat_id="chat-test", msg_type="file",
        sender=SimpleNamespace(id="user-test", sender_type="user"),
        body=SimpleNamespace(content=json.dumps({"file_key": "reference", "file_name": "reference.mp4"})),
    )
    sdk_sender = FeishuSender.__new__(FeishuSender)
    sdk_sender._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        message=SimpleNamespace(get=lambda _: SimpleNamespace(
            success=lambda: True, data=SimpleNamespace(items=[sdk_item])
        ))
    )))
    sender.quoted = sdk_sender.get_message("quoted")
    assert sender.quoted["chat_id"] == "chat-test"
    sender.get_message = sdk_sender.get_message
    await bot.handle(replace(message(), reply_to_message_id="quoted"))
    assert ark.uploads[0][0] == "reference.mp4"
    bot2, ark2, sender2 = gateway(tmp_path / "other")
    sender2.quoted = {**sender.quoted, "chat_id": "other-chat"}
    await bot2.handle(replace(message(), reply_to_message_id="quoted"))
    assert not ark2.uploads


async def test_delayed_registration_partial_results_and_upload_fallback(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    ark.pages = [[], [artifact()], [artifact(), artifact("video-1", "teaser.mp4")]]
    await bot.handle(message())
    assert len(sender.images) == len(sender.files) == 1
    bot2, ark2, sender2 = gateway(tmp_path / "fallback")
    sender2.fail_upload = True
    ark2.files = [artifact()]
    await bot2.handle(message())
    assert any("https://files.example/image-1" in text for text in sender2.card_texts)
    assert any("部分文件仍在登记" in text for text in sender2.card_texts)
    assert sender2.card_snapshots[-1]["header"]["template"] == "orange"
    sender2.fail_upload = False
    await bot2.handle(replace(message(2), text="/files"))
    assert len(sender2.images) == 1 and len(ark2.runs) == 1
    assert sender2.card_snapshots[-1]["header"]["template"] == "orange"  # 视频仍未登记
    ark2.files.append(artifact("video-1", "teaser.mp4"))
    await bot2.handle(replace(message(3), text="/files"))
    assert sender2.card_snapshots[-1]["header"]["template"] == "green"
    assert "部分文件仍在登记" not in sender2.card_texts[-1]


async def test_oversize_download_link_and_terminal_partial_output(tmp_path):
    bot, ark, sender = gateway(tmp_path, SEEDANCE_MAX_OUTPUT_BYTES="3")
    await bot.handle(message())
    assert not sender.uploads
    assert sender.card_texts[-1].count("下载链接有时效") == 2
    async def failed_run(*args, **kwargs):
        return RunResult(terminal="failed", messages=[
            "生成图片成功，视频失败。[图片](sandbox:/mnt/session/outputs/visual.jpg)",
        ])
    bot2, ark2, sender2 = gateway(tmp_path / "failed")
    ark2.run = failed_run
    ark2.files = [artifact()]
    await bot2.handle(message())
    assert len(sender2.images) == 1
    assert any("未全部完成" in text for text in sender2.card_texts)
    assert sender2.card_snapshots[-1]["header"]["template"] == "red"
    assert bot2.store.card(conversation(message()))["phase"] == "failed"


async def test_timeout_retains_session_and_does_not_resubmit_running_job(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    ark.error = ArkRunTimeout("Session 运行超时")
    ark.status = "running"
    await bot.handle(message())
    assert bot.store.get_session(conversation(message())) == "session-1"
    assert bot.store.card(conversation(message()))["phase"] == "timed_out"
    assert sender.card_snapshots[-1]["header"]["template"] == "orange"
    assert "等待超时" in sender.card_texts[-1] and "30 分钟" in sender.card_texts[-1]
    assert "任务未被取消" in sender.card_texts[-1]
    assert "无需重复提交" in sender.card_texts[-1]
    await bot.handle(message())  # 等待超时也必须去重，不能再次提交云端任务
    await bot.handle(message(2))
    assert len(ark.runs) == 1
    await bot.handle(replace(message(3), text="/status"))
    await bot.handle(replace(message(4), text="/files"))
    assert any("正在创作" in text for text in sender.card_texts)
    assert not sender.images  # 没有最终交付清单时不能把检索素材当成产物
    assert sender.card_snapshots[-1]["header"]["template"] == "orange"
    assert bot.store.card(conversation(message()))["phase"] == "timed_out"


@pytest.mark.parametrize(
    ("status", "phase", "color"),
    [("running", "timed_out", "orange"), ("idle", "done", "green"),
     ("failed", "failed", "red"), ("terminated", "failed", "red"),
     ("unknown", "timed_out", "orange")],
)
async def test_status_reconciles_wait_timeout_after_restart(tmp_path, status, phase, color):
    bot, ark, sender = gateway(tmp_path)
    ark.error = ArkRunTimeout("Session 运行超时")
    ark.status = "running"
    await bot.handle(message())
    for task in list(bot._tasks):
        task.cancel()
    await asyncio.gather(*list(bot._tasks), return_exceptions=True)
    restarted = SeedanceGateway(
        bot.config, ark, sender, asyncio.get_running_loop(), SeedanceStore(str(tmp_path / "seedance.db")),
    )
    ark.status = status
    await restarted.handle(replace(message(2), text="/status"))
    assert restarted.store.card(conversation(message()))["phase"] == phase
    assert sender.card_snapshots[-1]["header"]["template"] == color
    assert len(ark.runs) == len(ark.created) == len(sender.cards) == 1


async def test_non_timeout_ark_error_still_displays_failure(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    ark.error = ArkError("方舟事件流失败", status_code=403)
    await bot.handle(message())
    assert bot.store.card(conversation(message()))["phase"] == "failed"
    assert sender.card_snapshots[-1]["header"]["template"] == "red"
    assert "等待超时" not in sender.card_texts[-1]


@pytest.mark.parametrize("cause", ["deadline", "read_timeout"])
@pytest.mark.parametrize("recovered", [None, RunResult(terminal="idle"), RunResult(terminal="failed")])
async def test_ark_run_distinguishes_unrecovered_timeout_from_cloud_terminal(cause, recovered):
    requests = []

    class PendingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b": ready\n\n"
            if cause == "read_timeout":
                raise httpx.ReadTimeout("stream read timed out")
            await asyncio.Event().wait()

    def serve(request):
        requests.append(request)
        if request.url.path.endswith("/stream"):
            return httpx.Response(200, stream=PendingStream())
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        ark = ArkClient("test-key", "https://ark.example/api/v3", client=http)
        ark._recover_timed_out_run = AsyncMock(return_value=recovered)
        if recovered is None:
            with pytest.raises(ArkRunTimeout) as raised:
                await ark.run("session-test", "创作要求", 50)
            assert isinstance(raised.value, ArkError)
        else:
            assert await ark.run("session-test", "创作要求", 50) is recovered
        ark._recover_timed_out_run.assert_awaited_once()
    assert sum(request.method == "POST" for request in requests) == 1


async def test_status_is_available_during_generation(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    started, finish = asyncio.Event(), asyncio.Event()
    async def run(*_args, **_kwargs):
        started.set()
        await finish.wait()
        return RunResult(terminal="idle", messages=["完成"])
    ark.run = run
    ark.status = "running"
    task = asyncio.create_task(bot.handle(message()))
    await asyncio.wait_for(started.wait(), 1)
    try:
        await asyncio.wait_for(bot.handle(replace(message(2), text="/status")), 1)
        assert any("当前创作：正在创作" in text for text in sender.card_texts)
    finally:
        finish.set()
        await task


async def test_reset_and_sender_isolation_survive_restart(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    await bot.handle(message())
    await bot.handle(replace(message(2), user_open_id="second-user"))
    assert len(ark.created) == 2
    restarted = SeedanceStore(str(tmp_path / "seedance.db"))
    assert restarted.get_session(conversation(message())) == "session-1"
    assert restarted.delivered("session-1", "image-1")
    await bot.handle(replace(message(3), text="/new"))
    assert bot.store.get_session(conversation(message())) is None
    assert bot.store.get_session(conversation(replace(message(), user_open_id="second-user"))) == "session-2"
    assert not bot.store.context(conversation(message()))


async def test_accept_filters_mentions_and_allowlist(tmp_path):
    bot, ark, sender = gateway(tmp_path, SEEDANCE_AUTHORIZED_OPEN_IDS="user-test")
    bot.loop = Mock()
    bot.accept(replace(message(), chat_type="group"))
    bot.accept(replace(message(), user_open_id="unauthorized"))
    bot.loop.call_soon_threadsafe.assert_not_called()
    bot.accept(replace(message(), chat_type="group", mentioned_bot=True))
    bot.loop.call_soon_threadsafe.assert_called_once()


async def test_group_topic_root_aliases_restart_and_new_main_requests(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    first = replace(message(), chat_type="group", mentioned_bot=True)
    await bot.handle(first)
    assert bot.store.topic(first.chat_id, "topic-message-1") == first.message_id
    assert len(sender.cards) == 1 and not sender.texts
    assert all(args[-1] is True for args in sender.images + sender.files)

    # root_id 与真实 thread_id 任意一个存在都应命中同一会话，且不用 @。
    await bot.handle(replace(message(2), chat_type="group", root_id=first.message_id, text="改成傍晚"))
    await bot.handle(replace(message(3), chat_type="group", thread_id="topic-message-1", text="再改一次"))
    assert len(ark.created) == 1 and len(ark.runs) == 3 and len(sender.cards) == 1
    restarted = SeedanceGateway(
        bot.config, ark, sender, asyncio.get_running_loop(), SeedanceStore(str(tmp_path / "seedance.db")),
    )
    await restarted.handle(replace(message(4), chat_type="group", thread_id="topic-message-1", text="重启后继续"))
    assert len(ark.created) == 1 and len(sender.cards) == 1
    await restarted.handle(replace(message(5), chat_type="group", mentioned_bot=True))
    assert len(ark.created) == 2 and len(sender.cards) == 2
    await restarted.handle(replace(message(6), chat_type="group", thread_id="unknown-topic"))
    assert len(ark.runs) == 5


async def test_registered_topic_still_checks_sender_and_allowlist(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    first = replace(message(), chat_type="group", mentioned_bot=True)
    await bot.handle(first)
    second = replace(message(2), chat_type="group", thread_id="topic-message-1", user_open_id="second-user")
    await bot.handle(second)
    assert len(ark.created) == 2 and len(sender.cards) == 2
    limited, _, _ = gateway(tmp_path / "limited", SEEDANCE_AUTHORIZED_OPEN_IDS="user-test")
    limited.store.save_topic("chat-test", first.message_id, "topic-message-1")
    limited.loop = Mock()
    limited.accept(second)
    limited.loop.call_soon_threadsafe.assert_not_called()
    limited.accept(replace(second, user_open_id="user-test"))
    limited.loop.call_soon_threadsafe.assert_called_once()


async def test_progress_and_status_patch_same_card_without_overwriting_progress(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    started, finish = asyncio.Event(), asyncio.Event()

    async def run(*args, on_progress, **kwargs):
        await on_progress("tool.started GenVideo")
        started.set()
        await finish.wait()
        return RunResult(terminal="idle", messages=["视频完成"])

    ark.run = run
    ark.status = "running"
    task = asyncio.create_task(bot.handle(message()))
    await asyncio.wait_for(started.wait(), 1)
    try:
        await asyncio.wait_for(wait_state(bot, lambda state: state.get("stage") == "正在生成视频。"), 1)
        await asyncio.wait_for(bot.handle(replace(message(2), text="/status")), 1)
        state = bot.store.card(conversation(message()))
        assert state["phase"] == "running" and state["detail"] == "正在生成视频。"
        assert "当前创作：正在创作" in state["notice"]
        assert len(sender.cards) == 1 and not sender.texts
    finally:
        finish.set()
        await task
    assert sender.card_snapshots[-1]["header"]["template"] == "green"
    assert set(sender.patches) == {"card-1"}


@pytest.mark.parametrize("failure", ["create", "patch"])
async def test_card_failure_falls_back_in_topic_without_stopping_generation(tmp_path, failure):
    bot, ark, sender = gateway(tmp_path)
    sender.fail_card = failure == "create"
    sender.fail_patch = failure == "patch"

    async def run(*args, on_progress, **kwargs):
        before = len(sender.texts)
        await on_progress("tool.started GenVideo")
        assert len(sender.texts) == before  # 降级时工具进度不刷屏
        return RunResult(terminal="idle", messages=["成功生成视频 " + ark.last_reply])

    ark.run = run
    first = replace(message(), chat_type="group", mentioned_bot=True)
    await bot.handle(first)
    assert len(ark.created) == 1 and len(sender.images) == len(sender.files) == 1
    assert any("成功生成视频" in text for _, text in sender.texts)
    assert sum("im:message:update" in text for _, text in sender.texts) == 1
    assert sender.thread_texts == sender.texts
    assert bot.store.topic("chat-test", "topic-message-1") == first.message_id
    sender.fail_card = sender.fail_patch = False
    await bot.handle(replace(message(2), chat_type="group", thread_id="topic-message-1"))
    assert len(ark.created) == 1
    assert sender.card_snapshots[-1]["header"]["template"] == "green"


def test_large_card_keeps_complete_links_and_stays_within_patch_budget():
    urls = ["https://files.example/" + str(index) + "a" * 3000 for index in range(100)]
    card = build_card({
        "phase": "running", "request": "需求" * 10000, "result": "结果" * 10000,
        "notice": "状态" * 10000, "detail": "进度" * 10000,
        "files": [{"id": str(i), "name": "产物.mp4", "url": url} for i, url in enumerate(urls)],
    }, group=True)
    encoded = json.dumps(card, ensure_ascii=False)
    assert len(encoded.encode()) < 28000
    assert urls[0] in encoded
    assert urls[3] not in encoded
    assert "其余产物可用 /files" in encoded
    assert card["schema"] == "2.0" and card["config"]["update_multi"]


async def test_old_files_cannot_overwrite_card_while_new_references_upload(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    await bot.handle(message())
    reference = ResourceRef("new-ref", "new.jpg", "image")
    await bot.handle(replace(message(2), text="", resources=(reference,)))
    started, finish = asyncio.Event(), asyncio.Event()
    upload = ark.upload_file

    async def blocked_upload(*args):
        started.set()
        await finish.wait()
        return await upload(*args)

    ark.upload_file = blocked_upload
    task = asyncio.create_task(bot.handle(replace(message(3), text="按新参考图重新创作")))
    await asyncio.wait_for(started.wait(), 1)
    try:
        await asyncio.wait_for(bot.handle(replace(message(4), text="/files")), 1)
        state = bot.store.card(conversation(message()))
        assert state["phase"] == "running" and not state["files"]
        assert state["request"] == "按新参考图重新创作"
    finally:
        finish.set()
        await task
    assert bot.store.card(conversation(message()))["attached_session"] == "session-2"


async def test_links_outside_card_budget_are_still_delivered_in_topic(tmp_path):
    bot, ark, sender = gateway(tmp_path, SEEDANCE_MAX_OUTPUT_BYTES="3")
    ark.files = [artifact(str(i), f"clip-{i}.mp4") for i in range(10)]
    ark.last_reply = "\n".join(
        f"[片段{i}](sandbox:/mnt/session/outputs/clip-{i}.mp4)" for i in range(10)
    )
    await bot.handle(replace(message(), chat_type="group", mentioned_bot=True))
    assert len(sender.cards) == 1
    assert sender.texts == sender.thread_texts
    assert len(sender.texts) == 2
    assert "https://files.example/8" in sender.texts[0][1]
    assert "https://files.example/9" in sender.texts[1][1]
    assert all(bot.store.delivered("session-1", str(i)) for i in range(10))


async def test_final_manifest_filters_eighty_work_files_and_reference_mounts(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    ark.files = [artifact(f"search-{i}", f"search_{i}.jpg") for i in range(80)]
    ark.files += [artifact("draft", "draft.mp4"), artifact("final", "final.mp4")]
    ark.last_reply = (
        '[deliverables]\n["/mnt/session/outputs/final.mp4"]\n[/deliverables]\n'
        "成片完成：[视频](sandbox:/mnt/session/outputs/final.mp4)"
    )
    await bot.handle(message())
    assert not sender.images and len(sender.files) == 1
    assert bot.store.card(conversation(message()))["expected"] == ["final.mp4"]
    assert "[deliverables]" not in sender.card_texts[-1]
    assert ark.run_options[0]["system_message"] == TURN_CONTRACT
    await bot.handle(replace(message(2), text="/files"))
    assert not sender.images and len(sender.files) == 2
    await bot.handle(replace(message(3), text="", resources=(ResourceRef("ref", "new.jpg", "image"),)))
    await bot.handle(replace(message(4), text="用新素材修改"))
    assert {item["file_id"] for item in ark.created[-1][1]["resources"]} == {"input-1", "final"}


async def test_missing_manifest_does_not_send_media_or_guess_by_extension(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    ark.last_reply = "仍在准备素材，尚未交付成片。"
    await bot.handle(message())
    await bot.handle(replace(message(2), text="/files"))
    assert not sender.images and not sender.files
    assert "尚未确认最终交付物" in sender.card_texts[-1]


async def test_explicit_manifest_survives_separate_final_summary_and_unicode_filename(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    ark.files = [artifact("final", "最终成片 v2.mp4"), artifact()]
    async def run(*args, **kwargs):
        return RunResult(terminal="idle", messages=[
            '[deliverables]["/mnt/session/outputs/最终成片 v2.mp4"][/deliverables]',
            "质检完成，可以交付。",
        ])
    ark.run = run
    await bot.handle(message())
    assert len(sender.files) == 1 and not sender.images
    assert bot.store.card(conversation(message()))["files"][0]["name"] == "最终成片 v2.mp4"
    await bot.handle(replace(message(2), text="/files"))
    assert len(sender.files) == 2 and not sender.images


async def test_pending_newest_version_does_not_return_older_video(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    ark.last_reply = "[视频](sandbox:/mnt/session/outputs/teaser.mp4)"
    ark.files = [
        artifact("older", "teaser.mp4", created_at=100),
        artifact("newest", "teaser.mp4", created_at=101, download_url=""),
    ]
    await bot.handle(message())
    assert not sender.files and not sender.images
    assert bot.store.card(conversation(message()))["missing"] == ["teaser.mp4"]
    assert bot.store.card(conversation(message()))["phase"] == "partial"


async def test_latest_final_response_restores_only_legacy_deliverables(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    bot.store.save_session(conversation(message()), "legacy", "agent-test")
    bot.store.save_card(conversation(message()), {
        "phase": "done", "files": [{"id": "work", "name": "search_1.jpg"}],
    })
    ark.events = [
        history_event("user.message", "旧需求"),
        history_event("agent.message", "[旧图](sandbox:/mnt/session/outputs/visual.jpg)"),
        history_event("session.status_idle"),
        history_event("user.message", "新视频"),
        history_event("agent.message", "正在检查 /mnt/session/outputs/visual.jpg"),
        history_event("agent.message", "[成片](sandbox:/mnt/session/outputs/teaser.mp4)"),
        history_event("session.status_idle"),
    ]
    await bot.handle(replace(message(2), text="/files"))
    assert not sender.images and len(sender.files) == 1
    assert bot.store.card(conversation(message()))["files"] == [{"id": "video-1", "name": "teaser.mp4"}]


async def test_timeout_observer_tracks_stages_and_delivers_final_without_resubmitting(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    bot.observer_interval = 0.01
    ark.error = ArkRunTimeout("等待超时")
    ark.status = "running"
    await bot.handle(message())
    ark.events.append(history_event(
        "agent.tool_use", name="Bash", input={"description": "ASS字幕全量重装配并合成"},
    ))
    await asyncio.wait_for(wait_state(bot, lambda state: state.get("stage") == "正在合成视频。"), 1)
    assert bot.store.card(conversation(message()))["phase"] == "timed_out"
    ark.events.extend([
        history_event("agent.tool_use", name="Bash", input={"description": "最终成片规格响度质检"}),
        history_event("agent.message", '[deliverables]["/mnt/session/outputs/teaser.mp4"][/deliverables]\n成片完成'),
        history_event("session.status_idle"),
    ])
    await asyncio.wait_for(wait_state(bot, lambda state: state.get("phase") == "done"), 1)
    assert len(ark.runs) == 1 and not sender.images and len(sender.files) == 1
    assert bot.store.card(conversation(message()))["stage"] == "正在检查成片质量。"
    assert "已用时" in sender.card_texts[-1] and "更新于" in sender.card_texts[-1]


async def test_restart_recovers_legacy_timeout_and_updates_original_card(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    await bot.handle(message())
    bot.store.save_card(conversation(message()), {
        "phase": "timed_out", "card_id": "card-1", "request": "原需求",
        "files": [
            {"id": "search", "name": "search_1.jpg"},
            {"id": "video-1", "name": "teaser.mp4"},
        ],
    })
    ark.events = [
        history_event("user.message", "原需求"),
        history_event("agent.message", "[最终视频](sandbox:/mnt/session/outputs/teaser.mp4)"),
        history_event("session.status_idle"),
    ]
    restarted = SeedanceGateway(bot.config, ark, sender, asyncio.get_running_loop(), bot.store)
    await restarted.recover_active_jobs()
    await asyncio.wait_for(wait_state(restarted, lambda state: state.get("phase") == "done"), 1)
    assert len(ark.runs) == len(sender.cards) == 1
    assert len(sender.images) == len(sender.files) == 1
    assert restarted.store.card(conversation(message()))["expected"] == ["teaser.mp4"]
    assert "search_1.jpg" not in sender.card_texts[-1]


@pytest.mark.parametrize("cloud_status", ["running", "failed"])
async def test_startup_reconciles_old_failure_card_only_if_cloud_still_running(tmp_path, cloud_status):
    bot, ark, sender = gateway(tmp_path)
    bot.observer_interval = 0.01
    bot.store.save_session(conversation(message()), "legacy", "agent-test")
    bot.store.save_card(conversation(message()), {
        "phase": "failed", "card_id": "card-existing", "request": "旧版等待超时",
        "terminal": "failed", "files": [],
    })
    ark.events = [history_event("user.message", "旧版等待超时")]
    ark.status = cloud_status
    await bot.recover_active_jobs()
    if cloud_status == "running":
        await asyncio.wait_for(wait_state(bot, lambda state: state.get("turn_id")), 1)
        assert bot.store.card(conversation(message()))["phase"] == "timed_out"
        assert bot.store.card(conversation(message()))["terminal"] is None
        ark.events.extend([
            history_event("agent.message", '[deliverables]["/mnt/session/outputs/teaser.mp4"][/deliverables]'),
            history_event("session.status_idle"),
        ])
        await asyncio.wait_for(wait_state(bot, lambda state: state.get("phase") == "done"), 1)
        assert len(sender.files) == 1 and not sender.images
    else:
        assert bot.store.card(conversation(message()))["phase"] == "failed"
        assert not bot._tasks
    assert not ark.runs and not ark.created


async def test_new_turn_never_uses_old_manifest_or_old_terminal(tmp_path):
    bot, ark, sender = gateway(tmp_path)
    bot.observer_interval = 0.01
    await bot.handle(message())
    ark.error = ArkRunTimeout("等待超时")
    # 模拟发送前超时：历史只有旧回合，不能据此显示完成或回传旧文件。
    async def before_send(*args, **kwargs):
        ark.runs.append(args)
        raise ark.error
    ark.run = before_send
    await bot.handle(replace(message(2), text="下一轮视频"))
    await bot.handle(replace(message(3), text="/files"))
    assert len(sender.images) == len(sender.files) == 1
    assert bot.store.card(conversation(message()))["phase"] == "timed_out"
    assert bot.store.card(conversation(message())).get("expected") is None
    await bot.handle(replace(message(4), text="/new"))
    await asyncio.gather(*list(bot._tasks))
    assert bot.store.card(conversation(message()))["phase"] == "waiting"


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("正在调用工具：VisualSearch", "正在检索参考素材。"),
        ("Agent 说：[stage] 脚本分镜", "正在编写脚本和分镜。"),
        ("正在执行：ASS字幕全量重装配并合成", "正在合成视频。"),
        ("正在执行：生成ASS字幕", "正在制作字幕。"),
        ("正在执行：最终成片规格响度质检", "正在检查成片质量。"),
        ("工具执行未成功，Agent 正在尝试恢复", "正在修复问题并重试。"),
        ("正在调用工具：Read", ""),
    ],
)
def test_stage_summaries_cover_real_tool_descriptions(text, label):
    assert stage_label(text) == label


def test_manifest_is_exact_and_never_extracts_progress_paths():
    assert deliverables("正在检查 /mnt/session/outputs/draft.mp4") is None
    assert deliverables("[草稿](sandbox:/mnt/session/outputs/draft.mp4)") is None
    assert deliverables("[外部](https://example.com/mnt/session/outputs/final.mp4)", final=True) is None
    assert deliverables('[deliverables]["/mnt/session/uploads/ref.jpg"][/deliverables]', final=True) is None
    assert deliverables('[deliverables]["/mnt/session/outputs/../draft.jpg"][/deliverables]') is None
    assert deliverables("[deliverables][][/deliverables]") == set()
    assert deliverables("[图](sandbox:/mnt/session/outputs/主视觉%20v2.jpg)", final=True) == {"主视觉 v2.jpg"}
    assert deliverables("[deliverables]not json[/deliverables]\n[图](sandbox:/mnt/session/outputs/work.jpg)", final=True) is None


def test_newest_exact_filename_wins_including_pending_replacement():
    files = [
        artifact("old", "final.mp4", created_at="2026-11-12T10:00:00Z"),
        artifact("new", "final.mp4", created_at="2026-11-12T11:00:00Z"),
        artifact("draft", "draft.mp4", created_at="2026-11-12T12:00:00Z"),
    ]
    assert [item["id"] for item in select_files(files, {"final.mp4"})] == ["new"]
    files[1]["download_url"] = ""
    assert select_files(files, {"final.mp4"})[0]["id"] == "new"
    assert not select_files(files, {"final_mp4"})


def test_event_boundary_requires_current_user_with_timezone():
    events = [
        history_event("user.message", processed_at="2026-11-12T13:00:00+08:00"),
        history_event("session.status_idle", processed_at="2026-11-12T13:10:00+08:00"),
    ]
    boundary = int(datetime.fromisoformat("2026-11-12T13:05:00+08:00").timestamp() * 1000)
    assert not current_events(events, boundary)
    events.append(history_event("user.message", processed_at="2026-11-12T13:11:00+08:00"))
    assert current_events(events, boundary) == events[-1:]
    assert not current_events(events, 0, previous_user_id=events[-1]["id"])


async def test_reporter_keeps_latest_throttled_stage_and_heartbeats():
    updates = []
    ready = asyncio.Event()
    async def update(label):
        updates.append(label)
        if len(updates) >= 3:
            ready.set()
    reporter = ProgressReporter(update, interval=0.01)
    try:
        await reporter.feed("[stage] 检索素材")
        while not updates:
            await asyncio.sleep(0)
        await reporter.feed("[stage] 生成图片")
        await reporter.feed("[stage] 合成视频")
        await asyncio.wait_for(ready.wait(), 1)
        assert updates[:3] == ["正在检索参考素材。", "正在合成视频。", "正在合成视频。"]
    finally:
        await reporter.stop()


async def test_reporter_stop_waits_for_in_flight_card_patch():
    entered, release = asyncio.Event(), asyncio.Event()
    async def update(label):
        entered.set()
        await release.wait()
    reporter = ProgressReporter(update, interval=0.01)
    await reporter.feed("[stage] 合成视频")
    await asyncio.wait_for(entered.wait(), 1)
    stopping = asyncio.create_task(reporter.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await asyncio.wait_for(stopping, 1)


class FakeResources:
    def __init__(self, fail_agent=False):
        self.calls = []
        self.fail_agent = fail_agent

    async def find(self, collection, name):
        return ""

    async def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path == "/environments":
            return {"id": "env-created"}
        if method == "POST" and path == "/agents":
            if self.fail_agent:
                raise ArkError("denied", status_code=403)
            return {"id": "agent-created"}
        if method == "GET" and path.startswith("/agents/"):
            return {"base_agent": "ultra_vision_agent_preview", "version": 4}
        return {}


async def test_provision_reuses_ids_and_saves_partial_progress(tmp_path):
    path = tmp_path / "config.env"
    values = env(SEEDANCE_AGENT_ID="", SEEDANCE_ENVIRONMENT_ID="")
    path.write_text(serialize_env(values))
    client = FakeResources(fail_agent=True)
    with pytest.raises(ArkError):
        await provision(client, values, path)
    saved = parse_env_text(path.read_text())
    assert saved["SEEDANCE_ENVIRONMENT_ID"] == "env-created"
    assert saved["FEISHU_APP_SECRET"] == "test-secret"
    assert path.stat().st_mode & 0o777 == 0o600
    client = FakeResources()
    updates = await provision(client, saved, path)
    assert not any(method == "POST" and resource == "/environments" for method, resource, _ in client.calls)
    saved.update(updates)
    client = FakeResources()
    await provision(client, saved, path)
    update = next(body for method, resource, body in client.calls if method == "POST" and resource == "/agents/agent-created")
    assert update["version"] == 4 and "base_agent" not in update
    assert saved["SEEDANCE_AGENT_ID"] == "agent-created"

async def test_provision_rejects_general_agent_before_modifying_environment(tmp_path):
    client = FakeResources()
    async def general_agent(method, path, body=None):
        client.calls.append((method, path, body))
        return {"base_agent": "general-agent", "version": 2}
    client.request = general_agent
    with pytest.raises(RuntimeError, match="不是 Seedance"):
        await provision(client, env(), tmp_path / "config.env")
    assert [method for method, _, _ in client.calls] == ["GET"]


async def test_bounded_download_does_not_forward_api_key():
    requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(200, content=b"0123456789")
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        ark = ArkClient("must-stay-private", "https://ark.example/api/v3", client=http)
        with pytest.raises(ValueError, match="大小上限"):
            await ark.download_file("https://files.example/item", max_bytes=3)
        assert await ark.download_file("https://files.example/item", max_bytes=10) == b"0123456789"
    assert all("authorization" not in request.headers for request in requests)


def test_feishu_media_upload_and_thread_reply_contract():
    calls = []
    def uploaded_image(request):
        assert request.request_body.image_type == "message"
        assert request.request_body.image.read() == b"image"
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(image_key="image-key"))
    def uploaded_file(request):
        assert request.request_body.file_type == "mp4"
        assert request.request_body.file_name == "video.mp4"
        assert request.request_body.file.read() == b"video"
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(file_key="file-key"))
    def replied(request):
        calls.append(request)
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(thread_id="thread"))
    sender = FeishuSender.__new__(FeishuSender)
    sender._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        image=SimpleNamespace(create=uploaded_image), file=SimpleNamespace(create=uploaded_file),
        message=SimpleNamespace(reply=replied),
    )))
    assert sender.upload_image(b"image") == "image-key"
    assert sender.upload_file("video.mp4", b"video", "mp4") == "file-key"
    sender.reply_image("message", "image-key", True)
    sender.reply_file("message", "file-key", True)
    assert [request.request_body.msg_type for request in calls] == ["image", "file"]
    assert all(request.request_body.reply_in_thread is True for request in calls)
