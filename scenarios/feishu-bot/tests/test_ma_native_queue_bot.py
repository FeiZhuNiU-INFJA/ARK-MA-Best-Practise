"""方舟原生队列群聊 Bot 的行为测试。

与客户端串行方案不同，方舟原生队列 **不在客户端排队**：消息一到就 send_message 直发方舟 Session
（哪怕在 running），靠方舟侧「运行中待处理队列」吸收/合并；另起一个常驻消费协程读事件流，
每到回合结束(idle) 把该回合最后一条 agent.message 发回群。这套要验证的编排：
  - 多人 @bot 共享同一个 Session、只起一个消费协程；
  - 直发不排队（并发进入 _handle）；
  - 消费协程：一个回合内多条 agent.message 只发**最后一条**（合并语义）；
  - 409 RuntimeBusy 退避重试；404 Session 失效重建后重发；
  - /new 重置并停消费协程；未授权拦截；去重；群里未 @bot 丢弃。

做法同客户端串行方案：假 ArkClient / FeishuSender 替身注入，真实事件循环跑完整链路。
FakeArk._open_event_stream 产出预置事件后**挂起**（模拟长连接），避免外层 while busy 重连；
测试末尾统一 cancel 掉消费协程收尾。会话映射用 InMemorySessionMap 避免碰 ~/.arkagent。
"""
import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

_GROUP_BOT_DIR = Path(__file__).resolve().parents[1] / "cases" / "group-bot"
if str(_GROUP_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_GROUP_BOT_DIR))

import ma_native_queue_bot as native  # noqa: E402
import shared  # noqa: E402
from ma_native_queue_bot import ConcurrentGroupBot, _is_runtime_busy  # noqa: E402

from arkagent.ark import ArkError  # noqa: E402
from arkagent.feishu import HistoryMessage, IncomingMessage  # noqa: E402


# ---- 替身（fakes）----------------------------------------------------------

class _FakeStream:
    """假事件流：进入后产出预置事件，放完后挂起（模拟长连接不结束）。

    这很关键——_consume 的外层是 `while sessions.get(key)==session_id`，若 async for
    正常结束就会立刻重开流形成 busy loop。真机事件流是长连接、不会自然结束，故这里
    放完事件后 await 一个永不完成的 Event，把消费协程停在「等更多事件」的有效状态。
    """

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self._iterate()

    async def __aexit__(self, *_exc):
        return False

    async def _iterate(self):
        for event in self._events:
            yield event
        await asyncio.Event().wait()  # 挂起，模拟长连接


class FakeArk:
    """假方舟客户端：create_session / send_message / _open_event_stream 可编程。

    - stream_events[session_id]：该 session 事件流要产出的事件列表（默认空 = 只挂起）。
    - send_hook(session_id, text, attempt_idx)：注入 409/404 等；返回 None 表示成功。
    """

    def __init__(self):
        self.created = 0
        self.create_calls: list[tuple[str, str]] = []
        self.send_calls: list[tuple[str, str]] = []
        self.stream_opens: list[str] = []
        self.stream_events: dict[str, list] = {}
        self.send_hook = None

    async def create_session(self, agent_id: str, environment_id: str, **_kw) -> str:
        self.created += 1
        self.create_calls.append((agent_id, environment_id))
        return f"sesn-{self.created}"

    async def send_message(self, session_id: str, text: str, **_kw) -> None:
        idx = len([c for c in self.send_calls])  # 全局第几次发送
        self.send_calls.append((session_id, text))
        if self.send_hook is not None:
            self.send_hook(session_id, text, idx)

    def _open_event_stream(self, session_id: str):
        self.stream_opens.append(session_id)
        return _FakeStream(self.stream_events.get(session_id, []))


class FakeSender:
    def __init__(self, history_provider=None):
        self.chat_sends: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str]] = []  # (message_id, emoji)
        self.deleted_reactions: list[tuple[str, str]] = []  # (message_id, reaction_id)
        self._history_provider = history_provider
        self._reaction_seq = 0

    def send_to_chat(self, chat_id: str, text: str) -> None:
        self.chat_sends.append((chat_id, text))

    def react(self, message_id: str, emoji_type: str):
        self._reaction_seq += 1
        self.reactions.append((message_id, emoji_type))
        return f"rx-{self._reaction_seq}"

    def delete_reaction(self, message_id: str, reaction_id: str) -> None:
        self.deleted_reactions.append((message_id, reaction_id))

    def list_messages(self, message, *_a, **_kw) -> list:
        if self._history_provider is None:
            return []
        return self._history_provider(message)


def _config(**overrides) -> shared.GroupBotConfig:
    base = dict(
        ark_api_key="k",
        ark_base_url="https://ark.example/api/v3",
        ark_agent_id="agent-1",
        ark_environment_id="env-1",
        feishu_app_id="app-1",
        feishu_app_secret="sec-1",
        session_timeout_ms=600000,
        authorized_open_ids=(),
    )
    base.update(overrides)
    return shared.GroupBotConfig(**base)


def _msg(user: str, text: str, *, mid: str, eid: str, thread_id: str = "", chat_id: str = "oc-team",
         chat_type: str = "group", ts: int = 1000, mentioned_bot: bool = True) -> IncomingMessage:
    return IncomingMessage(
        event_id=eid,
        message_id=mid,
        chat_id=chat_id,
        chat_type=chat_type,
        thread_id=thread_id,
        user_open_id=user,
        tenant_key="t-1",
        text=text,
        mentioned_bot=mentioned_bot,
        create_time=ts,
    )


def _text_event(eid: str, text: str) -> dict:
    return {"id": eid, "type": "agent.message", "content": [{"type": "text", "text": text}]}


# ---- 事件循环 fixture ------------------------------------------------------

@pytest.fixture()
def loop():
    lp = asyncio.new_event_loop()
    t = threading.Thread(target=lp.run_forever, daemon=True)
    t.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)
    t.join(timeout=2)
    lp.close()


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    # 409 退避默认 2s 起步，测试里改成毫秒级，避免拖慢。
    monkeypatch.setattr(native, "BACKOFF_BASE_S", 0.01)
    monkeypatch.setattr(native, "BACKOFF_CAP_S", 0.02)


def _drain(lp: asyncio.AbstractEventLoop, timeout: float = 2.0) -> None:
    for _ in range(2):
        fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0), lp)
        fut.result(timeout=timeout)


def _wait_until(pred, lp, *, tries: int = 60, timeout: float = 2.0) -> bool:
    # 每轮 drain 一次 loop（跑完已排任务）再睡一小会儿真实时间——409 退避用的是
    # asyncio.sleep 真实定时器，纯 loop 往返不推进它，必须让时间真的流逝。
    for _ in range(tries):
        if pred():
            return True
        _drain(lp, timeout=timeout)
        time.sleep(0.01)
    return pred()


def _make_bot(loop, *, config=None, sender=None, ark=None, sessions=None):
    ark = ark or FakeArk()
    sender = sender or FakeSender()
    sessions = sessions if sessions is not None else shared.InMemorySessionMap()
    bot = ConcurrentGroupBot(config or _config(), ark, sender, loop, sessions=sessions)
    return bot, ark, sender, sessions


def _shutdown(bot, loop) -> None:
    """停掉所有常驻消费协程，避免遗留 pending 任务/挂起流噪声。"""
    async def _stop_all():
        for key_str in list(bot._consumers.keys()):  # noqa: SLF001 - 测试收尾
            task = bot._consumers.pop(key_str, None)
            if task is not None:
                task.cancel()
    asyncio.run_coroutine_threadsafe(_stop_all(), loop).result(timeout=2)
    _drain(loop)


# ---- 1. 多人 @bot 共享一个 Session + 一个消费协程（直发不排队）--------------

def test_two_users_share_one_session_and_consumer(loop):
    bot, ark, sender, sessions = _make_bot(loop)
    try:
        bot.accept(_msg("ou-alice", "@bot 帮忙看下", mid="om-1", eid="ev-1", ts=1000))
        bot.accept(_msg("ou-bob", "@bot 我也是", mid="om-2", eid="ev-2", ts=2000))

        assert _wait_until(lambda: len(ark.send_calls) >= 2, loop)
        # 只建了一个共享 Session（Bob 复用），只起一个消费协程。
        assert ark.created == 1
        assert {sid for sid, _ in ark.send_calls} == {"sesn-1"}
        assert len(bot._consumers) == 1  # noqa: SLF001
    finally:
        _shutdown(bot, loop)


# ---- 1b. 「稍等」表情回执：触发消息下贴 OneSecond，且不再发建会话文字回执 --------

def test_ack_reaction_added_and_no_text_receipt(loop):
    bot, ark, sender, _ = _make_bot(loop)
    try:
        bot.accept(_msg("ou-alice", "@bot 帮我处理", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: len(sender.reactions) >= 1, loop)
        # 在触发消息下贴了「稍等」表情。
        assert sender.reactions == [("om-1", "OneSecond")]
        # 首次建会话不再发那句「正在为本群创建共享会话」的文字回执。
        assert not any("创建共享会话" in t for _, t in sender.chat_sends)
    finally:
        _shutdown(bot, loop)


# ---- 2. 消费协程：一个回合内多条 agent.message 只发最后一条（合并语义）------

def test_consumer_merges_and_sends_last_message_of_turn(loop):
    ark = FakeArk()
    ark.stream_events["sesn-1"] = [
        _text_event("e1", "让我先查一下"),   # 回合中的中间播报
        _text_event("e2", "这是最终答复"),   # 回合最后一条
        {"id": "e3", "type": "session.status_idle"},  # 回合结束
    ]
    bot, ark, sender, _ = _make_bot(loop, ark=ark)
    try:
        bot.accept(_msg("ou-alice", "@bot 出个方案", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: any(t == "这是最终答复" for _, t in sender.chat_sends), loop)
        # 只发了回合末尾那条，中间播报「让我先查一下」不单独发。
        assert ("oc-team", "让我先查一下") not in sender.chat_sends
        # 回复回到群会话（send_to_chat），不 reply 到某条消息。
        assert ("oc-team", "这是最终答复") in sender.chat_sends
    finally:
        _shutdown(bot, loop)


# ---- 3. 会话出错事件 → 回执错误提示 ------------------------------------------

def test_consumer_reports_session_error(loop):
    ark = FakeArk()
    ark.stream_events["sesn-1"] = [{"id": "e1", "type": "session.error"}]
    bot, ark, sender, _ = _make_bot(loop, ark=ark)
    try:
        bot.accept(_msg("ou-alice", "@bot 触发错误", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: any("执行出错" in t for _, t in sender.chat_sends), loop)
    finally:
        _shutdown(bot, loop)


# ---- 3b. 「稍等」表情撤回：回合结束/出错/重置/发送失败四条收尾路径 --------------
#
# 方舟原生队列做不到「哪条回复对应哪条触发消息」的 1:1 撤回，故按群 key 批量收尾：
# _ack 贴表情记进 _pending_reactions，回合真正产出回复(idle+pending)、会话出错、
# /new 重置、_handle 兜底异常时整批 _clear_reactions。这里逐条覆盖这四个触发点，
# 外加「空 idle 不撤回」这个易错边界（刚建会话、消息还没被消费时别把表情提前撤掉）。

def test_reactions_withdrawn_after_turn_idle(loop):
    # 回合真正产出回复(agent.message + idle) → 发出合并回复 → 撤回该回合的「稍等」表情。
    ark = FakeArk()
    ark.stream_events["sesn-1"] = [
        _text_event("e1", "这是最终答复"),
        {"id": "e2", "type": "session.status_idle"},
    ]
    bot, ark, sender, _ = _make_bot(loop, ark=ark)
    try:
        bot.accept(_msg("ou-alice", "@bot 出个方案", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: ("om-1", "rx-1") in sender.deleted_reactions, loop)
        # 贴过一个、撤回一个，一一对上。
        assert sender.reactions == [("om-1", "OneSecond")]
        assert sender.deleted_reactions == [("om-1", "rx-1")]
    finally:
        _shutdown(bot, loop)


def test_reactions_kept_on_empty_idle(loop):
    # 空 idle（回合里没产出任何 agent.message）不撤回——避免把刚贴上的「稍等」提前撤掉。
    ark = FakeArk()
    ark.stream_events["sesn-1"] = [{"id": "e1", "type": "session.status_idle"}]
    bot, ark, sender, _ = _make_bot(loop, ark=ark)
    try:
        bot.accept(_msg("ou-alice", "@bot 只贴不撤", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: len(sender.reactions) >= 1, loop)
        _drain(loop)
        # 表情还在，未被空 idle 撤回。
        assert sender.deleted_reactions == []
    finally:
        _shutdown(bot, loop)


def test_reactions_withdrawn_on_session_error(loop):
    # 会话出错(session.error) → 回执错误提示 → 撤回本 key 遗留的「稍等」表情。
    ark = FakeArk()
    ark.stream_events["sesn-1"] = [{"id": "e1", "type": "session.error"}]
    bot, ark, sender, _ = _make_bot(loop, ark=ark)
    try:
        bot.accept(_msg("ou-alice", "@bot 触发错误", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: ("om-1", "rx-1") in sender.deleted_reactions, loop)
    finally:
        _shutdown(bot, loop)


def test_reactions_withdrawn_on_new_command(loop):
    # 首条消息贴了「稍等」但事件流一直挂起（回合没结束，表情留在 _pending_reactions）；
    # /new 停消费协程后没人再撤回，由 /new 分支兜底 _clear_reactions 清掉。
    bot, ark, sender, sessions = _make_bot(loop)  # 空事件流 = 挂起，表情不会被 idle 撤走
    try:
        bot.accept(_msg("ou-alice", "@bot 先聊", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: len(sender.reactions) >= 1, loop)
        assert not sender.deleted_reactions  # 回合没结束，暂不撤回

        bot.accept(_msg("ou-alice", "/new", mid="om-2", eid="ev-2", ts=2000))
        assert _wait_until(lambda: ("om-1", "rx-1") in sender.deleted_reactions, loop)
    finally:
        _shutdown(bot, loop)


def test_reactions_withdrawn_on_post_failure(loop):
    # 发送一直 409 直到重试耗尽 → _handle 抛异常 → 兜底 _clear_reactions 撤回「稍等」。
    ark = FakeArk()

    def hook(session_id, text, idx):
        raise ArkError("busy 409 RuntimeBusy", status_code=409, body="{}")  # 一直忙

    ark.send_hook = hook
    bot, ark, sender, _ = _make_bot(loop, ark=ark)
    try:
        bot.accept(_msg("ou-alice", "@bot 一直忙", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: ("om-1", "rx-1") in sender.deleted_reactions, loop)
        # 同时也回执了失败提示。
        assert any("执行失败" in t for _, t in sender.chat_sends)
    finally:
        _shutdown(bot, loop)


# ---- 4. 去重：同一 event_id 重投只处理一次 -----------------------------------

def test_duplicate_event_is_dropped(loop):
    bot, ark, sender, _ = _make_bot(loop)
    try:
        first = bot.accept(_msg("ou-alice", "@bot 你好", mid="om-1", eid="ev-dup", ts=1000))
        second = bot.accept(_msg("ou-alice", "@bot 你好", mid="om-1", eid="ev-dup", ts=1000))
        assert first is True
        assert second is False
        assert _wait_until(lambda: len(ark.send_calls) >= 1, loop)
        assert len(ark.send_calls) == 1
    finally:
        _shutdown(bot, loop)


# ---- 5. 群里未 @bot 丢弃 ------------------------------------------------------

def test_group_message_without_mention_is_ignored(loop):
    bot, ark, sender, _ = _make_bot(loop)
    try:
        handled = bot.accept(
            _msg("ou-alice", "闲聊一句", mid="om-1", eid="ev-1", ts=1000, mentioned_bot=False)
        )
        assert handled is False
        _drain(loop)
        assert ark.send_calls == []
        assert ark.created == 0
    finally:
        _shutdown(bot, loop)


# ---- 6. 窗口上下文：直发的 input 也是「上一次 @bot → 现在」的转录 -------------

def test_windowed_input_includes_group_transcript(loop):
    def history(_message):
        return [
            HistoryMessage("m-a", "ou-alice", "Alice", "user", "客户要报价", 900, at_bot=True),
            HistoryMessage("m-b", "ou-bob", "Bob", "user", "我发下参数", 950),
        ]

    sender = FakeSender(history_provider=history)
    bot, ark, sender, _ = _make_bot(loop, sender=sender)
    try:
        bot.accept(_msg("ou-alice", "汇总一下", mid="om-cur", eid="ev-1", ts=1000))
        assert _wait_until(lambda: len(ark.send_calls) >= 1, loop)
        sent_input = ark.send_calls[0][1]
        assert sent_input.split("\n") == [
            "Alice: 客户要报价",
            "Bob: 我发下参数",
            "ou-alice: 汇总一下",
        ]
    finally:
        _shutdown(bot, loop)


# ---- 7. 409 RuntimeBusy：退避重试直到成功 ------------------------------------

def test_409_runtime_busy_is_retried_with_backoff(loop):
    ark = FakeArk()

    def hook(session_id, text, idx):
        if idx < 2:  # 前两次发送遇到队列满
            raise ArkError("busy 409 RuntimeBusy", status_code=409, body="{}")
        # 第三次成功（返回 None）

    ark.send_hook = hook
    bot, ark, sender, _ = _make_bot(loop, ark=ark)
    try:
        bot.accept(_msg("ou-alice", "@bot 高并发", mid="om-1", eid="ev-1", ts=1000))
        # 三次尝试（2 次 409 + 1 次成功），都打到同一个 Session。
        assert _wait_until(lambda: len(ark.send_calls) >= 3, loop)
        assert [sid for sid, _ in ark.send_calls[:3]] == ["sesn-1", "sesn-1", "sesn-1"]
        assert ark.created == 1  # 409 不该导致重建 Session
    finally:
        _shutdown(bot, loop)


# ---- 8. 409 超过最大重试：抛出 → _handle 兜底回执 ----------------------------

def test_409_exhausts_retries_then_reports_failure(loop):
    ark = FakeArk()

    def hook(session_id, text, idx):
        raise ArkError("busy 409 RuntimeBusy", status_code=409, body="{}")  # 一直忙

    ark.send_hook = hook
    bot, ark, sender, _ = _make_bot(loop, ark=ark)
    try:
        bot.accept(_msg("ou-alice", "@bot 一直忙", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: any("执行失败" in t for _, t in sender.chat_sends), loop)
        # MAX_409_RETRIES(5) + 首次 = 共 6 次尝试后放弃。
        assert len(ark.send_calls) == native.MAX_409_RETRIES + 1
    finally:
        _shutdown(bot, loop)


# ---- 9. Session 失效(404)：重建新 Session 换新 session_id 重发 ----------------

def test_session_404_is_rebuilt_and_reposted(loop):
    ark = FakeArk()

    def hook(session_id, text, idx):
        if session_id == "sesn-stale":  # 旧会话已失效
            raise ArkError("session not found", status_code=404, body="{}")
        # 新会话发送成功

    ark.send_hook = hook
    sessions = shared.InMemorySessionMap()
    key = shared.GroupConversationKey("t-1", "oc-team", "")
    sessions.save(key, "sesn-stale")  # 模拟重启后 SQLite 里留着旧 session_id
    bot, ark, sender, sessions = _make_bot(loop, ark=ark, sessions=sessions)
    try:
        bot.accept(_msg("ou-alice", "@bot 在吗", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: sessions.get(key) not in (None, "sesn-stale"), loop)
        # 首发用 stale 触发 404 → 重建 sesn-1 → 用新 session 再发成功。
        assert ark.created == 1
        assert ark.send_calls[0][0] == "sesn-stale"
        assert ark.send_calls[-1][0] == "sesn-1"
        assert sessions.get(key) == "sesn-1"
    finally:
        _shutdown(bot, loop)


# ---- 10. /new 重置：停消费协程 + 清映射 + 回执 -------------------------------

def test_new_command_resets_and_stops_consumer(loop):
    bot, ark, sender, sessions = _make_bot(loop)
    key = shared.GroupConversationKey("t-1", "oc-team", "")
    try:
        bot.accept(_msg("ou-alice", "@bot 先聊", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: len(bot._consumers) == 1, loop)  # noqa: SLF001

        bot.accept(_msg("ou-alice", "/new", mid="om-2", eid="ev-2", ts=2000))
        assert _wait_until(lambda: any("已重置" in t for _, t in sender.chat_sends), loop)
        assert _wait_until(lambda: len(bot._consumers) == 0, loop)  # noqa: SLF001 - 消费协程已停
        assert sessions.get(key) is None  # 映射已清
    finally:
        _shutdown(bot, loop)


# ---- 11. 未授权用户被拦截：不建会话、不发送 ----------------------------------

def test_unauthorized_user_rejected(loop):
    cfg = _config(authorized_open_ids=("ou-alice",))
    bot, ark, sender, _ = _make_bot(loop, config=cfg)
    try:
        bot.accept(_msg("ou-bob", "@bot 放我进去", mid="om-1", eid="ev-1", ts=1000))
        assert _wait_until(lambda: any("未授权" in t for _, t in sender.chat_sends), loop)
        assert ark.send_calls == []
        assert ark.created == 0
    finally:
        _shutdown(bot, loop)


# ---- 12. _is_runtime_busy：结构化 409 与字符串兜底都识别 ---------------------

def test_is_runtime_busy_detection():
    assert _is_runtime_busy(ArkError("x", status_code=409)) is True
    assert _is_runtime_busy(ArkError("RuntimeBusy", status_code=None)) is True
    assert _is_runtime_busy(RuntimeError("boom 409 something")) is True
    assert _is_runtime_busy(ArkError("not found", status_code=404)) is False
    assert _is_runtime_busy(RuntimeError("unrelated")) is False
