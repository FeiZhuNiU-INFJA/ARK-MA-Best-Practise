"""话题 Session Bot 的隔离与路由测试。"""
import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

_GROUP_BOT_DIR = Path(__file__).resolve().parents[1] / "cases" / "group-bot"
if str(_GROUP_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_GROUP_BOT_DIR))

import shared  # noqa: E402
import topic_session_bot as topic_bot  # noqa: E402
from topic_session_bot import (  # noqa: E402
    TopicSessionBot,
    _is_runtime_busy,
    _with_roster_history_names,
    _with_roster_name,
    build_parser,
    select_topic_delta,
    to_topic_key,
)

from arkagent.ark import ArkError, RunResult  # noqa: E402
from arkagent.feishu import HistoryMessage, IncomingMessage  # noqa: E402


class FakeArk:
    def __init__(self):
        self.created = 0
        self.run_calls: list[tuple[str, str]] = []

    async def create_session(self, _agent_id: str, _environment_id: str, **_kwargs) -> str:
        self.created += 1
        return f"sesn-{self.created}"

    async def run(self, session_id: str, actor_input: str, _timeout_ms: int) -> RunResult:
        self.run_calls.append((session_id, actor_input))
        return RunResult("idle", [f"ok-{len(self.run_calls)}"])

    async def upload_file(self, _name: str, _mime: str, _data: bytes) -> str:
        return "file-1"

    async def add_session_file(self, _session_id: str, _file_id: str, _path: str) -> None:
        return None


class FakeSender:
    def __init__(self, history_provider=None):
        self.thread_replies: list[tuple[str, str]] = []
        self.chat_sends: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str]] = []
        self.deleted_reactions: list[tuple[str, str]] = []
        self.list_messages_calls = 0
        self._history_provider = history_provider
        self.rosters: dict[str, dict[str, str]] = {}

    def reply_in_thread(self, message_id: str, text: str, _roster=None) -> None:
        self.thread_replies.append((message_id, text))

    def send_to_chat(self, chat_id: str, text: str, _roster=None) -> None:
        self.chat_sends.append((chat_id, text))

    def react(self, message_id: str, emoji_type: str) -> str:
        self.reactions.append((message_id, emoji_type))
        return f"rx-{len(self.reactions)}"

    def delete_reaction(self, message_id: str, reaction_id: str) -> None:
        self.deleted_reactions.append((message_id, reaction_id))

    def chat_roster(self, chat_id: str) -> dict:
        return self.rosters.get(chat_id, {})

    def list_messages(self, _message) -> list:
        self.list_messages_calls += 1
        return self._history_provider(_message) if self._history_provider else []


def _config() -> shared.GroupBotConfig:
    return shared.GroupBotConfig(
        ark_api_key="k",
        ark_base_url="https://ark.example/api/v3",
        ark_agent_id="agent-1",
        ark_environment_id="env-1",
        feishu_app_id="app-1",
        feishu_app_secret="secret",
        session_timeout_ms=600000,
        authorized_open_ids=(),
    )


def _msg(
    text: str,
    *,
    mid: str,
    eid: str,
    root_id: str = "",
    thread_id: str = "",
    mentioned_bot: bool = False,
    user_name: str = "Alice",
) -> IncomingMessage:
    return IncomingMessage(
        event_id=eid,
        message_id=mid,
        chat_id="oc-team",
        chat_type="group",
        thread_id=thread_id,
        user_open_id="ou-alice",
        user_name=user_name,
        tenant_key="tenant-1",
        text=text,
        mentioned_bot=mentioned_bot,
        create_time=1000,
        root_id=root_id,
    )


@pytest.fixture()
def loop():
    event_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=event_loop.run_forever, daemon=True)
    thread.start()
    yield event_loop
    event_loop.call_soon_threadsafe(event_loop.stop)
    thread.join(timeout=2)
    event_loop.close()


def _make_bot(loop, *, sender=None):
    ark = FakeArk()
    sender = sender or FakeSender()
    sessions = shared.InMemorySessionMap()
    return TopicSessionBot(_config(), ark, sender, loop, sessions), ark, sender, sessions


def _drain(loop, predicate, tries: int = 30) -> None:
    for _ in range(tries):
        if predicate():
            return
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(timeout=2)
    assert predicate()


def test_main_timeline_mention_creates_topic_session_and_thread_reply(loop):
    bot, ark, sender, sessions = _make_bot(loop)
    message = _msg("@群助手 帮我整理", mid="om-root-a", eid="ev-1", mentioned_bot=True)

    assert bot.accept(message) is True
    _drain(loop, lambda: len(sender.thread_replies) == 1)

    assert ark.created == 1
    assert sessions.get(to_topic_key(message)) == "sesn-1"
    assert sender.thread_replies == [("om-root-a", "ok-1")]
    assert ark.run_calls[0][1] == "【最新对话】\nAlice: @群助手 帮我整理"
    assert sender.list_messages_calls == 0


def test_unmentioned_followup_waits_for_next_mention_and_enters_window(loop):
    def history(_message):
        return [
            HistoryMessage(
                "om-root", "ou-alice", "Alice", "user", "@群助手 开始", 1000, at_bot=True
            ),
            HistoryMessage(
                "om-followup", "ou-alice", "Alice", "user", "我喜欢紫色", 2000
            ),
        ]

    sender = FakeSender(history_provider=history)
    bot, ark, sender, _sessions = _make_bot(loop, sender=sender)
    root = _msg("@群助手 开始", mid="om-root", eid="ev-1", mentioned_bot=True)
    bot.accept(root)
    _drain(loop, lambda: len(sender.thread_replies) == 1)

    followup = _msg(
        "再压缩成三点",
        mid="om-followup",
        eid="ev-2",
        root_id="om-root",
        thread_id="omt-thread",
        mentioned_bot=False,
    )
    assert bot.accept(followup) is False
    assert len(sender.thread_replies) == 1
    assert len(ark.run_calls) == 1

    trigger = _msg(
        "@群助手 我喜欢什么颜色",
        mid="om-trigger",
        eid="ev-3",
        root_id="om-root",
        thread_id="omt-thread",
        mentioned_bot=True,
    )
    assert bot.accept(trigger) is True
    _drain(loop, lambda: len(sender.thread_replies) == 2)

    assert sender.list_messages_calls == 1
    assert ark.created == 1
    assert [session_id for session_id, _ in ark.run_calls] == ["sesn-1", "sesn-1"]
    assert ark.run_calls[1][1].splitlines() == [
        "【最新对话】",
        "Alice: 我喜欢紫色",
        "Alice: @群助手 我喜欢什么颜色",
    ]
    assert sender.thread_replies[-1][0] == "om-trigger"


def test_topic_delta_excludes_previous_mention_without_ten_message_limit():
    history = [
        HistoryMessage(
            "om-old", "ou-alice", "Alice", "user", "@群助手 上一轮", 1000, at_bot=True
        ),
        *[
            HistoryMessage(
                f"om-{index}", "ou-alice", "Alice", "user", f"补充 {index}", 1100 + index
            )
            for index in range(12)
        ],
    ]

    delta = select_topic_delta(history)

    assert [item.message_id for item in delta] == [f"om-{index}" for index in range(12)]


def test_roster_name_replaces_open_id_before_building_input():
    message = _msg(
        "@群助手 我叫什么",
        mid="om-name",
        eid="ev-name",
        mentioned_bot=True,
        user_name="",
    )

    resolved = _with_roster_name(message, {"俞麟": "ou-alice"})

    assert resolved.user_name == "俞麟"
    assert message.user_name == ""


def test_roster_name_replaces_open_id_on_file_history():
    history = [
        HistoryMessage(
            "om-file",
            "ou-alice",
            "",
            "user",
            "[文件：main.pdf]",
            1000,
        )
    ]

    resolved = _with_roster_history_names(history, {"俞麟": "ou-alice"})

    assert resolved[0].sender_name == "俞麟"
    assert history[0].sender_name == ""


def test_different_root_messages_use_different_sessions(loop):
    bot, ark, sender, _sessions = _make_bot(loop)
    bot.accept(_msg("@群助手 任务 A", mid="om-a", eid="ev-a", mentioned_bot=True))
    bot.accept(_msg("@群助手 任务 B", mid="om-b", eid="ev-b", mentioned_bot=True))
    _drain(loop, lambda: len(sender.thread_replies) == 2)

    assert ark.created == 2
    assert {session_id for session_id, _ in ark.run_calls} == {"sesn-1", "sesn-2"}


def test_unrelated_main_message_and_unknown_thread_are_ignored(loop):
    bot, ark, sender, _sessions = _make_bot(loop)

    assert bot.accept(_msg("群里闲聊", mid="om-flat", eid="ev-1")) is False
    assert bot.accept(
        _msg(
            "别人的话题",
            mid="om-other",
            eid="ev-2",
            root_id="om-unknown-root",
            thread_id="omt-unknown",
        )
    ) is False

    assert ark.run_calls == []
    assert sender.thread_replies == []


def test_new_replaces_only_current_topic_session(loop):
    bot, ark, sender, sessions = _make_bot(loop)
    root_a = _msg("@群助手 A", mid="om-a", eid="ev-a", mentioned_bot=True)
    root_b = _msg("@群助手 B", mid="om-b", eid="ev-b", mentioned_bot=True)
    bot.accept(root_a)
    bot.accept(root_b)
    _drain(loop, lambda: len(sender.thread_replies) == 2)

    old_b = sessions.get(to_topic_key(root_b))
    reset_a = _msg(
        "@群助手 /new",
        mid="om-a-reset",
        eid="ev-a-reset",
        root_id="om-a",
        thread_id="omt-a",
        mentioned_bot=True,
    )
    assert bot.accept(reset_a) is True
    _drain(loop, lambda: len(sender.thread_replies) == 3)

    assert sessions.get(to_topic_key(root_a)) == "sesn-3"
    assert sessions.get(to_topic_key(root_b)) == old_b
    assert len(ark.run_calls) == 2


class _FakeStream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self._iterate()

    async def __aexit__(self, *_exc):
        return False

    async def _iterate(self):
        for event in self._events:
            yield event
        await asyncio.Event().wait()


class NativeFakeArk(FakeArk):
    def __init__(self):
        super().__init__()
        self.send_calls: list[tuple[str, str]] = []
        self.send_hook = None
        self.stream_events: dict[str, list[dict]] = {}
        self.stream_opens: list[str] = []

    async def send_message(self, session_id: str, actor_input: str) -> None:
        index = len(self.send_calls)
        self.send_calls.append((session_id, actor_input))
        if self.send_hook is not None:
            self.send_hook(session_id, actor_input, index)

    def _open_event_stream(self, session_id: str):
        self.stream_opens.append(session_id)
        return _FakeStream(self.stream_events.get(session_id, []))


def _text_event(event_id: str, text: str) -> dict:
    return {
        "id": event_id,
        "type": "agent.message",
        "content": [{"type": "text", "text": text}],
    }


def _make_native_bot(loop, *, ark=None, sender=None, sessions=None):
    ark = ark or NativeFakeArk()
    sender = sender or FakeSender()
    sessions = sessions or shared.InMemorySessionMap()
    bot = TopicSessionBot(
        _config(),
        ark,
        sender,
        loop,
        sessions,
        execution_mode="native-queue",
    )
    return bot, ark, sender, sessions


def _wait_until(loop, predicate, tries: int = 80) -> None:
    for _ in range(tries):
        if predicate():
            return
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(timeout=2)
        time.sleep(0.005)
    assert predicate()


def _shutdown_native(bot, loop) -> None:
    async def _stop_all():
        for key_str in list(bot._consumers):  # noqa: SLF001 - 测试清理
            task = bot._consumers.pop(key_str)  # noqa: SLF001
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run_coroutine_threadsafe(_stop_all(), loop).result(timeout=2)


def test_execution_mode_parser_defaults_to_serial_and_accepts_native_queue():
    parser = build_parser()

    assert parser.parse_args([]).execution_mode == "serial"
    assert (
        parser.parse_args(["--execution-mode", "native-queue"]).execution_mode
        == "native-queue"
    )


def test_native_queue_same_topic_shares_session_and_different_topics_do_not(loop):
    bot, ark, _sender, _sessions = _make_native_bot(loop)
    try:
        first = _msg(
            "@群助手 一",
            mid="om-a1",
            eid="ev-a1",
            root_id="om-root-a",
            thread_id="omt-a",
            mentioned_bot=True,
        )
        second = _msg(
            "@群助手 二",
            mid="om-a2",
            eid="ev-a2",
            root_id="om-root-a",
            thread_id="omt-a",
            mentioned_bot=True,
        )
        other = _msg(
            "@群助手 三",
            mid="om-b1",
            eid="ev-b1",
            root_id="om-root-b",
            thread_id="omt-b",
            mentioned_bot=True,
        )
        bot.accept(first)
        bot.accept(second)
        bot.accept(other)
        _wait_until(loop, lambda: len(ark.send_calls) == 3)

        assert ark.created == 2
        assert ark.send_calls[0][0] == ark.send_calls[1][0]
        assert ark.send_calls[2][0] != ark.send_calls[0][0]
        assert len(bot._consumers) == 2  # noqa: SLF001
    finally:
        _shutdown_native(bot, loop)


def test_native_consumer_replies_with_last_message_in_topic(loop):
    ark = NativeFakeArk()
    ark.stream_events["sesn-1"] = [
        _text_event("e1", "处理中"),
        _text_event("e2", "最终答复"),
        {"id": "e3", "type": "session.status_idle"},
    ]
    bot, _ark, sender, _sessions = _make_native_bot(loop, ark=ark)
    try:
        bot.accept(
            _msg(
                "@群助手 给方案",
                mid="om-root",
                eid="ev-1",
                mentioned_bot=True,
            )
        )
        _wait_until(loop, lambda: sender.thread_replies == [("om-root", "最终答复")])

        assert all(text != "处理中" for _, text in sender.thread_replies)
        assert sender.deleted_reactions == [("om-root", "rx-1")]
    finally:
        _shutdown_native(bot, loop)


def test_native_queue_retries_409_without_rebuilding(loop, monkeypatch):
    monkeypatch.setattr(topic_bot, "BACKOFF_BASE_S", 0.001)
    monkeypatch.setattr(topic_bot, "BACKOFF_CAP_S", 0.002)
    ark = NativeFakeArk()

    def send_hook(_session_id, _text, index):
        if index < 2:
            raise ArkError("RuntimeBusy", status_code=409)

    ark.send_hook = send_hook
    bot, _ark, _sender, _sessions = _make_native_bot(loop, ark=ark)
    try:
        bot.accept(
            _msg("@群助手 忙吗", mid="om-root", eid="ev-1", mentioned_bot=True)
        )
        _wait_until(loop, lambda: len(ark.send_calls) == 3)

        assert ark.created == 1
        assert {session_id for session_id, _ in ark.send_calls} == {"sesn-1"}
    finally:
        _shutdown_native(bot, loop)


def test_native_queue_rebuilds_404_and_starts_new_consumer(loop):
    ark = NativeFakeArk()
    sessions = shared.InMemorySessionMap()
    message = _msg(
        "@群助手 继续",
        mid="om-current",
        eid="ev-1",
        root_id="om-root",
        thread_id="omt-a",
        mentioned_bot=True,
    )
    key = to_topic_key(message)
    sessions.save(key, "sesn-stale")

    def send_hook(session_id, _text, _index):
        if session_id == "sesn-stale":
            raise ArkError("not found", status_code=404)

    ark.send_hook = send_hook
    bot, _ark, _sender, _sessions = _make_native_bot(
        loop, ark=ark, sessions=sessions
    )
    try:
        bot.accept(message)
        _wait_until(loop, lambda: len(ark.send_calls) == 2)

        assert ark.send_calls[0][0] == "sesn-stale"
        assert ark.send_calls[1][0] == "sesn-1"
        assert sessions.get(key) == "sesn-1"
        assert "sesn-stale" in ark.stream_opens
        assert "sesn-1" in ark.stream_opens
    finally:
        _shutdown_native(bot, loop)


def test_native_persisted_session_recovers_consumer(loop):
    ark = NativeFakeArk()
    sessions = shared.InMemorySessionMap()
    message = _msg(
        "@群助手 继续",
        mid="om-current",
        eid="ev-1",
        root_id="om-root",
        thread_id="omt-a",
        mentioned_bot=True,
    )
    sessions.save(to_topic_key(message), "sesn-persisted")
    bot, _ark, _sender, _sessions = _make_native_bot(
        loop, ark=ark, sessions=sessions
    )
    try:
        bot.accept(message)
        _wait_until(loop, lambda: len(ark.send_calls) == 1)

        assert ark.created == 0
        assert ark.send_calls[0][0] == "sesn-persisted"
        assert ark.stream_opens == ["sesn-persisted"]
        assert len(bot._consumers) == 1  # noqa: SLF001
    finally:
        _shutdown_native(bot, loop)


def test_native_new_replaces_only_current_topic_and_stops_old_consumer(loop):
    bot, ark, sender, sessions = _make_native_bot(loop)
    first = _msg(
        "@群助手 开始",
        mid="om-a1",
        eid="ev-a1",
        root_id="om-root-a",
        thread_id="omt-a",
        mentioned_bot=True,
    )
    try:
        bot.accept(first)
        _wait_until(loop, lambda: len(ark.send_calls) == 1)
        old_session = sessions.get(to_topic_key(first))

        reset = _msg(
            "@群助手 /new",
            mid="om-a2",
            eid="ev-a2",
            root_id="om-root-a",
            thread_id="omt-a",
            mentioned_bot=True,
        )
        bot.accept(reset)
        _wait_until(loop, lambda: len(sender.thread_replies) == 1)

        assert sessions.get(to_topic_key(first)) != old_session
        assert ark.created == 2
        assert len(bot._consumers) == 1  # noqa: SLF001
        assert "已重置当前话题" in sender.thread_replies[0][1]
    finally:
        _shutdown_native(bot, loop)


def test_is_runtime_busy_detection():
    assert _is_runtime_busy(ArkError("busy", status_code=409))
    assert _is_runtime_busy(RuntimeError("RuntimeBusy"))
    assert not _is_runtime_busy(ArkError("missing", status_code=404))
