"""群聊共享 Bot（cases/group-bot）的窗口规则测试。

group-bot 目录不是 Python 包（靠 shared.py 里的 sys.path 注入运行），这里在测试内
把该目录加入 sys.path 后直接 import shared，验证「倒数第二次 @bot → 当前」窗口逻辑。
"""
import sys
from pathlib import Path

_GROUP_BOT_DIR = Path(__file__).resolve().parents[1] / "cases" / "group-bot"
if str(_GROUP_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_GROUP_BOT_DIR))

import shared  # noqa: E402
from arkagent.feishu import HistoryMessage, IncomingMessage, QuotedMessage  # noqa: E402


def _quoted(mid: str, *, depth: int = 1, name: str = "", text: str = "") -> QuotedMessage:
    return QuotedMessage(
        message_id=mid,
        sender_open_id=f"ou-{mid}",
        sender_name=name or mid,
        text=text or mid,
        depth=depth,
    )


def _hist(mid: str, ts: int, *, at_bot: bool = False, is_from_bot: bool = False, text: str = "") -> HistoryMessage:
    return HistoryMessage(
        message_id=mid,
        sender_open_id=f"ou-{mid}",
        sender_name=mid,
        sender_type="app" if is_from_bot else "user",
        text=text or mid,
        create_time=ts,
        at_bot=at_bot,
        is_from_bot=is_from_bot,
    )


def _trigger(**overrides) -> IncomingMessage:
    base = dict(
        event_id="evt-cur",
        message_id="om-cur",
        chat_id="oc-1",
        chat_type="group",
        thread_id="",
        user_open_id="ou-cur",
        tenant_key="t-1",
        text="现在轮到我了",
        mentioned_bot=True,
        create_time=2_000,
    )
    base.update(overrides)
    return IncomingMessage(**base)


def test_window_starts_at_last_history_trigger():
    # 历史里最近一条 @bot 是 m3；窗口应从 m3 起（含 m3、m4），把上一轮触发后的增量都带上。
    history = [
        _hist("m1", 100),
        _hist("m2", 200, at_bot=True),  # 更早的一次触发
        _hist("m3", 300, at_bot=True),  # 最近一次触发 = 「倒数第二次 @bot」
        _hist("m4", 400),               # 两次触发之间/之后的闲聊
    ]
    window = shared.select_window(history)
    assert [item.message_id for item in window] == ["m3", "m4"]


def test_window_filters_bot_replies():
    history = [
        _hist("m1", 100, at_bot=True),
        _hist("bot1", 150, is_from_bot=True, text="上一轮回复"),
        _hist("m2", 200),
    ]
    window = shared.select_window(history)
    assert [item.message_id for item in window] == ["m1", "m2"]
    assert all(not item.is_from_bot for item in window)


def test_window_fallback_to_recent_when_no_trigger():
    history = [_hist(f"m{i}", i * 10) for i in range(1, 16)]  # 15 条，无任何 @bot
    window = shared.select_window(history)
    assert len(window) == shared.FALLBACK_WINDOW_MESSAGES
    assert window[-1].message_id == "m15"  # 取最近 N 条


def test_window_empty_history():
    assert shared.select_window([]) == []


def test_build_windowed_input_is_plain_transcript_ending_with_current():
    # 一行一个发言人「名字: 内容」，最后一行必须是当前 @bot 的这条请求。
    history = [_hist("m1", 100, at_bot=True, text="老板说要周报"), _hist("m2", 200, text="我补充一句")]
    text = shared.build_windowed_input(_trigger(text="整理成周报"), history)
    # 不再有任何 XML 包裹
    assert "conversation_context" not in text
    assert "current_actor" not in text and "current_request" not in text
    lines = text.split("\n")
    assert lines == [
        "m1: 老板说要周报",   # sender_name 作为显示名
        "m2: 我补充一句",
        "ou-cur: 整理成周报",  # 当前请求只有 open_id 可用，作为最后一行
    ]


def test_build_windowed_input_without_history_is_current_only():
    text = shared.build_windowed_input(_trigger(text="你好"), [])
    assert "conversation_context" not in text
    # 无历史时就只有当前发言人一行
    assert text == "ou-cur: 你好"


# ---- 引用块注入 + 去重 ------------------------------------------------------

def test_build_windowed_input_injects_quote_before_current():
    # 被引用消息（窗口外）应作为 `[引用 名字: 内容]` 插在当前请求行之前。
    quote_chain = [_quoted("om-q1", name="老板", text="这个方案定了")]
    text = shared.build_windowed_input(_trigger(text="收到，我来落地"), [], quote_chain)
    lines = text.split("\n")
    assert lines == [
        "[引用 老板: 这个方案定了]",
        "ou-cur: 收到，我来落地",
    ]


def test_build_windowed_input_nested_quote_marks_depth():
    # 直接引用不加层号；引用的引用标 `第N层`。
    quote_chain = [
        _quoted("om-q1", depth=1, name="A", text="第一层"),
        _quoted("om-q2", depth=2, name="B", text="第二层"),
        _quoted("om-q3", depth=3, name="C", text="第三层"),
    ]
    text = shared.build_windowed_input(_trigger(text="看这段"), [], quote_chain)
    lines = text.split("\n")
    assert lines[:3] == [
        "[引用 A: 第一层]",
        "[引用·第2层 B: 第二层]",
        "[引用·第3层 C: 第三层]",
    ]


def test_build_windowed_input_dedups_quote_already_in_window():
    # 被引用的消息若已作为普通历史行出现在窗口里，就不再重复注入引用块。
    history = [_hist("m1", 100, at_bot=True, text="老板说要周报"), _hist("m2", 200, text="补充一句")]
    quote_chain = [_quoted("m1", name="老板", text="老板说要周报")]  # 与窗口里的 m1 同 id
    text = shared.build_windowed_input(_trigger(text="整理周报"), history, quote_chain)
    lines = text.split("\n")
    # m1 只作为历史行出现一次，引用块不再重复它
    assert lines == [
        "m1: 老板说要周报",
        "m2: 补充一句",
        "ou-cur: 整理周报",
    ]
    assert not any(line.startswith("[引用") for line in lines)


def test_build_windowed_input_keeps_quote_outside_window():
    # 引用的是窗口外的老消息（不在 history 里）：应保留注入。
    history = [_hist("m1", 100, at_bot=True, text="最近的话题")]
    quote_chain = [_quoted("om-old", name="张三", text="很久以前说的话")]
    text = shared.build_windowed_input(_trigger(text="翻到这条"), history, quote_chain)
    lines = text.split("\n")
    assert lines == [
        "m1: 最近的话题",
        "[引用 张三: 很久以前说的话]",
        "ou-cur: 翻到这条",
    ]


def test_build_windowed_input_no_quote_chain_is_backward_compatible():
    # 不传 quote_chain 时行为与旧版一致（只有历史 + 当前行）。
    history = [_hist("m1", 100, at_bot=True, text="hi")]
    text = shared.build_windowed_input(_trigger(text="继续"), history)
    assert text.split("\n") == ["m1: hi", "ou-cur: 继续"]


# ---- 话题前情块注入 + 去重 --------------------------------------------------

def test_build_windowed_input_injects_thread_context_first():
    # 话题前情（根消息 + 根之前几条主时间线）作为 `[话题前情 ...]` 拼在最前面。
    thread_context = [
        _hist("om-r0", 50, text="发起话题前的一句"),
        _hist("om-root", 60, text="就基于这条发起了话题"),
    ]
    history = [_hist("t1", 100, at_bot=True, text="话题里的讨论")]
    text = shared.build_windowed_input(
        _trigger(thread_id="th-1", text="接着聊"), history, None, thread_context
    )
    lines = text.split("\n")
    assert lines == [
        "[话题前情 om-r0: 发起话题前的一句]",
        "[话题前情 om-root: 就基于这条发起了话题]",
        "t1: 话题里的讨论",
        "ou-cur: 接着聊",
    ]


def test_build_windowed_input_dedups_thread_context_already_in_window():
    # 话题前情里若有消息已作为窗口历史行出现（根消息偶尔会被 thread 容器带出），不重复注入。
    history = [_hist("om-root", 60, at_bot=True, text="话题根")]
    thread_context = [_hist("om-root", 60, text="话题根")]  # 与窗口里的 om-root 同 id
    text = shared.build_windowed_input(
        _trigger(thread_id="th-1", text="继续"), history, None, thread_context
    )
    lines = text.split("\n")
    assert lines == ["om-root: 话题根", "ou-cur: 继续"]
    assert not any(line.startswith("[话题前情") for line in lines)


def test_build_windowed_input_thread_context_and_quote_dedup_together():
    # 前情块先注入并占位；引用块若指向同一条前情消息，则被去重不重复出现。
    thread_context = [_hist("om-root", 60, text="话题根：讨论报价")]
    quote_chain = [_quoted("om-root", name="老板", text="话题根：讨论报价")]  # 与前情同 id
    text = shared.build_windowed_input(
        _trigger(thread_id="th-1", text="我引用了话题根"), [], quote_chain, thread_context
    )
    lines = text.split("\n")
    assert lines == [
        "[话题前情 om-root: 话题根：讨论报价]",
        "ou-cur: 我引用了话题根",
    ]
    assert not any(line.startswith("[引用") for line in lines)


def test_build_windowed_input_no_thread_context_is_backward_compatible():
    # 不传 thread_context 时行为与旧版一致（无话题前情块）。
    history = [_hist("m1", 100, at_bot=True, text="hi")]
    text = shared.build_windowed_input(_trigger(text="继续"), history)
    assert text.split("\n") == ["m1: hi", "ou-cur: 继续"]
    assert not any(line.startswith("[话题前情") for line in text.split("\n"))


def test_shared_group_key_excludes_sender():
    a = shared.to_group_key(_trigger(user_open_id="ou-1"))
    b = shared.to_group_key(_trigger(user_open_id="ou-2"))
    assert a == b  # 共享会话键不含发言人
    assert a.as_str() == "t-1:oc-1:-"


# ---- Agent 身份：system prompt 里写入 bot 名字 -------------------------------

def test_build_group_system_injects_bot_name():
    system = shared.build_group_system("小方")
    assert "你在群里的名字是「小方」" in system
    assert "@小方" in system  # 转录里 @小方 = 在叫自己


def test_build_group_system_falls_back_to_default_name_when_blank():
    system = shared.build_group_system("  ")
    assert f"你在群里的名字是「{shared.DEFAULT_BOT_DISPLAY_NAME}」" in system
    assert "「」" not in system  # 不出现空名指代


def test_build_group_agent_config_uses_bot_name_in_system():
    config = shared.build_group_agent_config(bot_name="小方")
    assert "@小方" in config["system"]
    assert config["name"] == shared.GROUP_BOT_NAME


# ---- SqliteSessionMap：持久化 + 去重 + 失效重建兜底 --------------------------

def _key(chat_id: str = "oc-1", thread_id: str = "") -> shared.GroupConversationKey:
    return shared.GroupConversationKey(tenant_key="t-1", chat_id=chat_id, thread_id=thread_id)


def test_sqlite_session_map_save_get_reset(tmp_path):
    db = str(tmp_path / "sessions.db")
    store = shared.SqliteSessionMap(db)
    key = _key()

    assert store.get(key) is None            # 空库：查不到
    store.save(key, "sesn-1")
    assert store.get(key) == "sesn-1"        # 存了能查到
    store.save(key, "sesn-2")                # 覆盖（会话重建场景）
    assert store.get(key) == "sesn-2"
    store.reset(key)
    assert store.get(key) is None            # 重置后清空
    store.close()


def test_sqlite_session_map_persists_across_reopen(tmp_path):
    # 模拟 gateway 重启：新开一个连到同一个 db 的实例，映射仍在。
    db = str(tmp_path / "sessions.db")
    key = _key()
    first = shared.SqliteSessionMap(db)
    first.save(key, "sesn-persist")
    first.close()

    second = shared.SqliteSessionMap(db)
    assert second.get(key) == "sesn-persist"
    second.close()


def test_sqlite_session_map_keys_are_isolated(tmp_path):
    db = str(tmp_path / "sessions.db")
    store = shared.SqliteSessionMap(db)
    store.save(_key(chat_id="oc-1"), "sesn-a")
    store.save(_key(chat_id="oc-2"), "sesn-b")
    assert store.get(_key(chat_id="oc-1")) == "sesn-a"
    assert store.get(_key(chat_id="oc-2")) == "sesn-b"
    store.close()


def test_sqlite_session_map_claim_event_dedups(tmp_path):
    db = str(tmp_path / "sessions.db")
    store = shared.SqliteSessionMap(db)
    assert store.claim_event("evt-1") is True    # 第一次见到
    assert store.claim_event("evt-1") is False   # 重投被拦
    assert store.claim_event("evt-2") is True     # 另一个 event 放行
    assert store.claim_event("") is True          # 无 event_id 无从去重，放行
    store.close()


def test_sqlite_session_map_claim_event_dedups_across_reopen(tmp_path):
    # 去重跨重启仍生效：重开实例后重投的同一 event 仍被拦。
    db = str(tmp_path / "sessions.db")
    first = shared.SqliteSessionMap(db)
    assert first.claim_event("evt-x") is True
    first.close()

    second = shared.SqliteSessionMap(db)
    assert second.claim_event("evt-x") is False
    second.close()


def test_sqlite_session_map_matches_inmemory_interface():
    # 两个 demo 靠鸭子类型互换，接口方法名/签名须一致。
    for name in ("get", "save", "reset", "claim_event"):
        assert hasattr(shared.SqliteSessionMap, name)
        assert hasattr(shared.InMemorySessionMap, name)
