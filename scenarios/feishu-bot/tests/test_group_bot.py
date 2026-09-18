"""群聊共享 Bot（cases/digital-employee）的窗口规则测试。

digital-employee 目录不是 Python 包（靠 shared.py 里的 sys.path 注入运行），这里在测试内
把该目录加入 sys.path 后直接 import shared，验证「倒数第二次 @bot → 当前」窗口逻辑。
"""
import sys
from pathlib import Path

import httpx

_GROUP_BOT_DIR = Path(__file__).resolve().parents[1] / "cases" / "digital-employee"
if str(_GROUP_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_GROUP_BOT_DIR))

import shared  # noqa: E402
from arkagent.feishu import HistoryMessage, IncomingMessage, QuotedMessage, ResourceRef  # noqa: E402


def _quoted(mid: str, *, depth: int = 1, name: str = "", text: str = "") -> QuotedMessage:
    return QuotedMessage(
        message_id=mid,
        sender_open_id=f"ou-{mid}",
        sender_name=name or mid,
        text=text or mid,
        depth=depth,
    )


def _hist(mid: str, ts: int, *, at_bot: bool = False, is_from_bot: bool = False, text: str = "",
          resources: tuple[ResourceRef, ...] = ()) -> HistoryMessage:
    return HistoryMessage(
        message_id=mid,
        sender_open_id=f"ou-{mid}",
        sender_name=mid,
        sender_type="app" if is_from_bot else "user",
        text=text or mid,
        create_time=ts,
        at_bot=at_bot,
        is_from_bot=is_from_bot,
        resources=resources,
    )


def _trigger(**overrides) -> IncomingMessage:
    base = dict(
        event_id="evt-cur",
        message_id="om-cur",
        chat_id="oc-1",
        chat_type="group",
        thread_id="",
        user_open_id="ou-cur",
        user_name="小明",
        tenant_key="t-1",
        text="现在轮到我了",
        mentioned_bot=True,
        create_time=2_000,
    )
    base.update(overrides)
    return IncomingMessage(**base)


def test_should_handle_direct_text_only():
    assert shared.should_handle(
        _trigger(chat_type="p2p", text="请帮我总结", mentioned_bot=False)
    )
    assert not shared.should_handle(
        _trigger(
            chat_type="p2p",
            text="",
            mentioned_bot=False,
            content_type="file",
            resources=(_ref("fk-pdf", "report.pdf"),),
        )
    )
    assert not shared.should_handle(
        _trigger(
            chat_type="p2p",
            text="[unsupported message]",
            mentioned_bot=False,
            content_type="share_doc",
        )
    )


def test_should_handle_mentioned_group_attachment():
    assert shared.should_handle(
        _trigger(
            text="",
            content_type="file",
            resources=(_ref("fk-pdf", "report.pdf"),),
        )
    )


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
        "【最新对话】",
        "m1: 老板说要周报",   # sender_name 作为显示名
        "m2: 我补充一句",
        "小明: 整理成周报",   # 当前请求用 SDK 解析出的发言人显示名，作为最后一行
    ]


def test_build_windowed_input_without_history_is_current_only():
    text = shared.build_windowed_input(_trigger(text="你好"), [])
    assert "conversation_context" not in text
    assert text == "【最新对话】\n小明: 你好"


def test_build_windowed_input_current_falls_back_to_open_id_without_name():
    # SDK 没解析出显示名（私聊/解析失败）时，当前行退回 open_id，与历史行同一口径。
    text = shared.build_windowed_input(_trigger(user_name="", text="你好"), [])
    assert text == "【最新对话】\nou-cur: 你好"


# ---- 引用块注入 + 去重 ------------------------------------------------------

def test_build_windowed_input_injects_quote_before_current():
    # 被引用消息（窗口外）应作为 `[引用 名字: 内容]` 插在当前请求行之前。
    quote_chain = [_quoted("om-q1", name="老板", text="这个方案定了")]
    text = shared.build_windowed_input(_trigger(text="收到，我来落地"), [], quote_chain)
    lines = text.split("\n")
    assert lines == [
        "【最新对话】",
        "[引用 老板: 这个方案定了]",
        "小明: 收到，我来落地",
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
    assert lines[1:4] == [
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
        "【最新对话】",
        "m1: 老板说要周报",
        "m2: 补充一句",
        "小明: 整理周报",
    ]
    assert not any(line.startswith("[引用") for line in lines)


def test_build_windowed_input_keeps_quote_outside_window():
    # 引用的是窗口外的老消息（不在 history 里）：应保留注入。
    history = [_hist("m1", 100, at_bot=True, text="最近的话题")]
    quote_chain = [_quoted("om-old", name="张三", text="很久以前说的话")]
    text = shared.build_windowed_input(_trigger(text="翻到这条"), history, quote_chain)
    lines = text.split("\n")
    assert lines == [
        "【最新对话】",
        "m1: 最近的话题",
        "[引用 张三: 很久以前说的话]",
        "小明: 翻到这条",
    ]


def test_build_windowed_input_no_quote_chain_is_backward_compatible():
    # 不传 quote_chain 时行为与旧版一致（只有历史 + 当前行）。
    history = [_hist("m1", 100, at_bot=True, text="hi")]
    text = shared.build_windowed_input(_trigger(text="继续"), history)
    assert text.split("\n") == ["【最新对话】", "m1: hi", "小明: 继续"]


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
        "【最新对话】",
        "[话题前情 om-r0: 发起话题前的一句]",
        "[话题前情 om-root: 就基于这条发起了话题]",
        "t1: 话题里的讨论",
        "小明: 接着聊",
    ]


def test_build_windowed_input_dedups_thread_context_already_in_window():
    # 话题前情里若有消息已作为窗口历史行出现（根消息偶尔会被 thread 容器带出），不重复注入。
    history = [_hist("om-root", 60, at_bot=True, text="话题根")]
    thread_context = [_hist("om-root", 60, text="话题根")]  # 与窗口里的 om-root 同 id
    text = shared.build_windowed_input(
        _trigger(thread_id="th-1", text="继续"), history, None, thread_context
    )
    lines = text.split("\n")
    assert lines == ["【最新对话】", "om-root: 话题根", "小明: 继续"]
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
        "【最新对话】",
        "[话题前情 om-root: 话题根：讨论报价]",
        "小明: 我引用了话题根",
    ]
    assert not any(line.startswith("[引用") for line in lines)


def test_build_windowed_input_no_thread_context_is_backward_compatible():
    # 不传 thread_context 时行为与旧版一致（无话题前情块）。
    history = [_hist("m1", 100, at_bot=True, text="hi")]
    text = shared.build_windowed_input(_trigger(text="继续"), history)
    assert text.split("\n") == ["【最新对话】", "m1: hi", "小明: 继续"]
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
    assert "在单聊中，用户不需要 @ 你" in config["system"]
    assert "单聊是你与当前用户之间的独立会话" in config["system"]
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
    # 两种执行模式共用同一 store，接口方法名/签名须一致（含附件去重四方法）。
    for name in (
        "get", "save", "reset", "claim_event",
        "get_attachment", "save_attachment",
        "is_attachment_mounted", "mark_attachment_mounted",
        "get_memory_store", "save_memory_store",
        "get_session_memory_scope", "save_session_memory_scope",
    ):
        assert hasattr(shared.SqliteSessionMap, name)
        assert hasattr(shared.InMemorySessionMap, name)


def test_sqlite_memory_scope_bindings_persist_across_reopen(tmp_path):
    db = str(tmp_path / "sessions.db")
    first = shared.SqliteSessionMap(db)
    first.save_memory_store("tenant", "group", "chat", "store-1")
    first.save_session_memory_scope(
        "session-1", "tenant", "group", "chat", "store-1"
    )
    first.close()

    second = shared.SqliteSessionMap(db)
    assert second.get_memory_store("tenant", "group", "chat") == "store-1"
    assert second.get_session_memory_scope("session-1") == {
        "tenant_key": "tenant",
        "scope_type": "group",
        "scope_id": "chat",
        "store_id": "store-1",
    }
    second.close()


# ---- 附件去重：文件缓存层（file_key → file_id）+ 挂载记录层（session, file_key）----

def test_sqlite_session_map_attachment_cache_reuses_file_id(tmp_path):
    db = str(tmp_path / "sessions.db")
    store = shared.SqliteSessionMap(db)
    assert store.get_attachment("fk-1") is None       # 空库：没上传过
    store.save_attachment("fk-1", "file-1")
    assert store.get_attachment("fk-1") == "file-1"    # 命中缓存，可跳过下载 + 上传
    store.save_attachment("fk-1", "file-1b")           # 重传拿到新 file_id → 覆盖
    assert store.get_attachment("fk-1") == "file-1b"
    store.close()


def test_sqlite_session_map_attachment_cache_persists_across_reopen(tmp_path):
    # file_id 与 Session 无关、可跨会话/跨重启复用：重开实例仍命中缓存。
    db = str(tmp_path / "sessions.db")
    first = shared.SqliteSessionMap(db)
    first.save_attachment("fk-x", "file-x")
    first.close()

    second = shared.SqliteSessionMap(db)
    assert second.get_attachment("fk-x") == "file-x"
    second.close()


def test_sqlite_session_map_mount_record_is_per_session(tmp_path):
    db = str(tmp_path / "sessions.db")
    store = shared.SqliteSessionMap(db)
    assert store.is_attachment_mounted("sesn-1", "fk-1") is False
    store.mark_attachment_mounted("sesn-1", "fk-1")
    assert store.is_attachment_mounted("sesn-1", "fk-1") is True    # 本 session 已挂
    store.mark_attachment_mounted("sesn-1", "fk-1")                 # 幂等：重复标记不报错
    assert store.is_attachment_mounted("sesn-1", "fk-1") is True
    # 同一资源在另一个 session 仍未挂载（挂载记录以 session 为粒度）。
    assert store.is_attachment_mounted("sesn-2", "fk-1") is False
    store.close()


def test_inmemory_session_map_dedup_methods():
    store = shared.InMemorySessionMap()
    # 文件缓存层
    assert store.get_attachment("fk-1") is None
    store.save_attachment("fk-1", "file-1")
    assert store.get_attachment("fk-1") == "file-1"
    # 挂载记录层
    assert store.is_attachment_mounted("sesn-1", "fk-1") is False
    store.mark_attachment_mounted("sesn-1", "fk-1")
    assert store.is_attachment_mounted("sesn-1", "fk-1") is True
    assert store.is_attachment_mounted("sesn-2", "fk-1") is False


def test_sqlite_session_map_persists_user_oauth_and_session_vaults(tmp_path):
    db = str(tmp_path / "sessions.db")
    first = shared.SqliteSessionMap(db)
    first.save_user_oauth(
        "tenant-1",
        "ou-alice",
        "vlt-user",
        "cred-user",
        "refresh-secret",
        123456,
        ("offline_access", "calendar:calendar:read"),
    )
    first.save_session_vaults("sesn-1", ["vlt-bot", "vlt-user"])
    first.save_session_user_token(
        "sesn-1", "tenant-1", "ou-alice", 123456
    )
    first.close()

    second = shared.SqliteSessionMap(db)
    oauth = second.get_user_oauth("tenant-1", "ou-alice")
    assert oauth["vault_id"] == "vlt-user"
    assert oauth["credential_id"] == "cred-user"
    assert oauth["refresh_token"] == "refresh-secret"
    assert oauth["scopes"] == ("offline_access", "calendar:calendar:read")
    assert second.get_session_vaults("sesn-1") == ("vlt-bot", "vlt-user")
    assert second.get_session_user_token("sesn-1") == (
        "tenant-1",
        "ou-alice",
        123456,
    )
    second.close()


# ---- 多模态：附件路径/名清洗 + 挂载编排 + 输入拼接 ----------------------------

def _ref(file_key: str, file_name: str, res_type: str = "file") -> ResourceRef:
    return ResourceRef(file_key=file_key, file_name=file_name, type=res_type)


def test_multimodal_enabled_defaults_on_and_respects_off_values(monkeypatch):
    monkeypatch.delenv("GROUP_BOT_MULTIMODAL", raising=False)
    assert shared.multimodal_enabled() is True  # 默认开启
    for off in ("0", "false", "no", "off", "OFF", " False "):
        monkeypatch.setenv("GROUP_BOT_MULTIMODAL", off)
        assert shared.multimodal_enabled() is False
    monkeypatch.setenv("GROUP_BOT_MULTIMODAL", "1")
    assert shared.multimodal_enabled() is True


def test_safe_filename_strips_path_and_control_chars():
    # 分隔符/控制字符替成 _，前导点去掉；空名回退 attachment-N。
    assert shared._safe_filename("../../etc/passwd", 0) == "_.._etc_passwd"  # 无路径穿越
    assert shared._safe_filename("a/b\\c.txt", 0) == "a_b_c.txt"
    assert shared._safe_filename("...hidden", 0) == "hidden"
    assert shared._safe_filename("", 2) == "attachment-3"
    assert shared._safe_filename("   ", 0) == "attachment-1"


def test_mount_path_is_stable_and_collision_resistant():
    p1 = shared._mount_path("fk-1", "a.pdf")
    p1_again = shared._mount_path("fk-1", "a.pdf")
    p2 = shared._mount_path("fk-2", "a.pdf")  # 同名不同 file_key → 不同目录
    assert p1 == p1_again                          # 稳定可复现
    assert p1.endswith("/a.pdf") and p2.endswith("/a.pdf")
    assert p1.split("/")[0] != p2.split("/")[0]    # 前缀哈希不同，不覆盖


def test_session_visible_path_prefixes_upload_root():
    assert shared.session_visible_path("abc/a.pdf") == "/mnt/session/uploads/abc/a.pdf"
    assert shared.session_visible_path("/abc/a.pdf") == "/mnt/session/uploads/abc/a.pdf"


async def test_prepare_attachments_uploads_text_and_image_files():
    msg = _trigger(resources=(_ref("fk-md", "notes.md"), _ref("img-1", "img-1.jpg", "image")))
    downloaded = {"fk-md": b"hello \xe4\xb8\xad\xe6\x96\x87", "img-1": b"\x89PNG..."}
    uploaded: list[str] = []

    async def _download(ref):
        return downloaded[ref.file_key]

    async def _upload(name, mime, data):
        uploaded.append(name)
        return f"file-{name}"

    prepared, notices = await shared.prepare_attachments(msg, _download, _upload)
    assert notices == []
    md, img = prepared
    assert md.name == "notes.md" and md.file_id == "file-notes.md"
    assert img.name == "img-1.jpg" and img.file_id == "file-img-1.jpg"
    assert uploaded == ["notes.md", "img-1.jpg"]


async def test_prepare_attachments_degrades_on_download_error():
    msg = _trigger(resources=(_ref("bad", "boom.pdf"), _ref("ok", "ok.pdf")))

    async def _download(ref):
        if ref.file_key == "bad":
            raise RuntimeError("下载附件失败 230002")
        return b"%PDF-1.7"

    async def _upload(name, mime, data):
        return f"file-{name}"

    prepared, notices = await shared.prepare_attachments(msg, _download, _upload)
    # 坏的降级成 notice，好的照常上传，互不影响。
    assert [p.name for p in prepared] == ["ok.pdf"]
    assert len(notices) == 1 and "boom.pdf" in notices[0]


async def test_prepare_attachments_rejects_oversized_single_file():
    big = b"x" * (shared.MAX_SINGLE_FILE_BYTES + 1)
    msg = _trigger(resources=(_ref("fk", "big.bin"),))

    async def _download(ref):
        return big

    async def _upload(name, mime, data):  # 不该被调用
        raise AssertionError("超限文件不应上传")

    prepared, notices = await shared.prepare_attachments(msg, _download, _upload)
    assert prepared == []
    assert len(notices) == 1 and "40 MB" in notices[0]


async def test_prepare_attachments_non_utf8_text_is_uploaded_without_decoding():
    msg = _trigger(resources=(_ref("fk", "notes.txt"),))

    async def _download(ref):
        return b"\xff\xfe\x00bad"  # 非 UTF-8

    async def _upload(name, mime, data):
        assert data == b"\xff\xfe\x00bad"
        return "file-notes"

    prepared, notices = await shared.prepare_attachments(msg, _download, _upload)
    assert notices == []
    assert prepared[0].file_id == "file-notes"


async def test_prepare_attachments_reuses_cached_file_id_skips_download_upload():
    # 文件缓存层命中：同一 file_key 已上传过，直接复用 file_id，不再下载/上传。
    msg = _trigger(resources=(_ref("fk-dup", "report.pdf"),))
    downloads: list[str] = []
    uploads: list[str] = []

    async def _download(ref):
        downloads.append(ref.file_key)
        return b"%PDF-1.7"

    async def _upload(name, mime, data):
        uploads.append(name)
        return f"file-{name}"

    cache = {"fk-dup": "file-cached"}
    prepared, notices = await shared.prepare_attachments(
        msg,
        _download,
        _upload,
        lookup_file_id=lambda fk: cache.get(fk),
        save_file_id=lambda fk, fid: cache.__setitem__(fk, fid),
    )
    assert notices == []
    assert len(prepared) == 1
    item = prepared[0]
    assert item.file_id == "file-cached"          # 复用缓存里的 file_id
    assert item.file_key == "fk-dup"
    assert item.mount_path == shared._mount_path("fk-dup", "report.pdf")  # 路径由 file_key 决定，稳定
    assert downloads == [] and uploads == []       # 命中缓存：一次下载/上传都没发生


async def test_prepare_attachments_saves_file_id_to_cache_on_first_upload():
    # 缓存未命中：正常下载 + 上传，且把 file_key → file_id 写回缓存供后续复用。
    msg = _trigger(resources=(_ref("fk-new", "a.pdf"),))
    cache: dict[str, str] = {}

    async def _download(ref):
        return b"%PDF-1.7"

    async def _upload(name, mime, data):
        return f"file-{name}"

    prepared, notices = await shared.prepare_attachments(
        msg,
        _download,
        _upload,
        lookup_file_id=lambda fk: cache.get(fk),
        save_file_id=lambda fk, fid: cache.__setitem__(fk, fid),
    )
    assert notices == []
    assert prepared[0].file_id == "file-a.pdf"
    assert cache == {"fk-new": "file-a.pdf"}       # 首次上传后落缓存


# ---- collect_round_resources：收齐触发消息 + 窗口历史 + 话题前情里的附件 ----------

def _href(mid: str, file_key: str, file_name: str, res_type: str = "file") -> ResourceRef:
    return ResourceRef(file_key=file_key, file_name=file_name, type=res_type, message_id=mid)


def test_collect_round_resources_only_trigger_when_no_history():
    msg = _trigger(resources=(_ref("fk-1", "a.pdf"),))
    refs = shared.collect_round_resources(msg)
    assert [r.file_key for r in refs] == ["fk-1"]


def test_collect_round_resources_picks_up_file_from_window_history():
    # 关键场景：文件是单独一条历史消息发的，触发消息只有正文（无附件）。
    msg = _trigger(text="说说这个 PDF", resources=())
    history = [
        _hist("om-file", 100, resources=(_href("om-file", "fk-pdf", "office-requirements.pdf"),)),
        _hist("om-ask", 200, at_bot=True),  # @bot 让窗口从这里起，但文件在更早的 om-file
    ]
    refs = shared.collect_round_resources(msg, history)
    # select_window 从最近一次 at_bot（om-ask）起，om-file 在其之前——但收附件用的是
    # select_window 的产物，这里 om-file 落在窗口外，故不应被收（转录范围一致性）。
    assert refs == []


def test_collect_round_resources_file_inside_window_is_collected():
    msg = _trigger(text="说说这个 PDF", resources=())
    history = [
        _hist("om-ask0", 50, at_bot=True),   # 更早一次触发，窗口从下一条 at_bot 起
        _hist("om-file", 100, resources=(_href("om-file", "fk-pdf", "office-requirements.pdf"),)),
        _hist("om-ask", 200, at_bot=True),
    ]
    # 最近 at_bot 是 om-ask（index 2），窗口 = [om-ask]，om-file 不在窗口内。
    assert shared.collect_round_resources(msg, history) == []
    # 若文件消息发生在最近一次触发之后（进窗口），则应被收进来。
    history2 = [
        _hist("om-ask", 100, at_bot=True),
        _hist("om-file", 200, resources=(_href("om-file", "fk-pdf", "office-requirements.pdf"),)),
    ]
    refs = shared.collect_round_resources(msg, history2)
    assert refs == [
        ResourceRef(file_key="fk-pdf", file_name="office-requirements.pdf", type="file", message_id="om-file"),
    ]


def test_collect_round_resources_dedups_by_file_key_trigger_first():
    # 同一 file_key 在触发消息和窗口历史都出现，只收一次，触发消息的排最前。
    msg = _trigger(resources=(_href("om-cur", "fk-dup", "report.pdf"),))
    history = [
        _hist("om-cur0", 100, at_bot=True),
        _hist("om-file", 200, resources=(_href("om-file", "fk-dup", "report-copy.pdf"),)),
    ]
    refs = shared.collect_round_resources(msg, history)
    assert [r.file_key for r in refs] == ["fk-dup"]
    assert refs[0].message_id == "om-cur"   # 触发消息优先，保留其 message_id


def test_collect_round_resources_includes_thread_context():
    msg = _trigger(resources=())
    thread_ctx = [
        _hist("om-root", 10, resources=(_href("om-root", "fk-root", "spec.pdf"),)),
    ]
    refs = shared.collect_round_resources(msg, history=[], thread_context=thread_ctx)
    assert [r.file_key for r in refs] == ["fk-root"]
    assert refs[0].message_id == "om-root"


def test_collect_round_resources_merges_all_three_sources_ordered():
    msg = _trigger(resources=(_href("om-cur", "fk-cur", "cur.pdf"),))
    history = [
        _hist("om-h", 100, at_bot=True, resources=(_href("om-h", "fk-hist", "hist.pdf"),)),
    ]
    thread_ctx = [
        _hist("om-t", 10, resources=(_href("om-t", "fk-thread", "thread.pdf"),)),
    ]
    refs = shared.collect_round_resources(msg, history, thread_ctx)
    # 顺序：触发消息 → 窗口历史 → 话题前情。
    assert [r.file_key for r in refs] == ["fk-cur", "fk-hist", "fk-thread"]


def test_collect_round_resources_empty_when_nothing():
    assert shared.collect_round_resources(_trigger(resources=())) == []


def _mounted(name: str, mount_path: str) -> shared.PreparedAttachment:
    return shared.PreparedAttachment(name=name, mount_path=mount_path, file_id=f"file-{name}")


def test_build_windowed_input_appends_mounted_paths_after_current():
    prepared = [_mounted("报告.pdf", "abc/报告.pdf")]
    text = shared.build_windowed_input(_trigger(text="看这份报告"), [], prepared=prepared)
    lines = text.split("\n")
    assert lines[:2] == ["【最新对话】", "小明: 看这份报告"]
    assert "【文件挂载】" in text
    assert "报告.pdf： /mnt/session/uploads/abc/报告.pdf" in text


def test_build_windowed_input_default_instruction_when_no_text_but_attachment():
    # 纯图片消息（text 为空）+ 附件：当前行给一句默认指令，不留空 “名字: ”。
    prepared = [_mounted("img-1.jpg", "abc/img-1.jpg")]
    text = shared.build_windowed_input(_trigger(text=""), [], prepared=prepared)
    lines = text.split("\n")
    assert lines[1].startswith("小明: 请读取并总结")
    assert lines[1] != "小明: "


def test_build_windowed_input_notices_reported_verbatim():
    text = shared.build_windowed_input(
        _trigger(text="看看"), [], notices=["附件「boom.pdf」未能处理：下载失败"]
    )
    assert "另外：" in text
    assert "- 附件「boom.pdf」未能处理：下载失败" in text


def test_build_windowed_input_no_attachments_is_backward_compatible():
    # 不传 prepared/notices 时与纯文本路径完全一致（无附件块）。
    history = [_hist("m1", 100, at_bot=True, text="hi")]
    text = shared.build_windowed_input(_trigger(text="继续"), history)
    assert text.split("\n") == ["【最新对话】", "m1: hi", "小明: 继续"]
    assert "挂载" not in text and "另外" not in text


# ---- lark-cli：会话级环境变量 + 开关 + 资源置备（Environment/Vault）------------

def _lark_config(**overrides) -> shared.GroupBotConfig:
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


def test_lark_cli_enabled_requires_vault():
    # 配了 Vault 才算就绪；没配则退回纯对话（不注入定位变量、不挂 vault）。
    assert shared.lark_cli_enabled(_lark_config(lark_vault_id="vlt-1")) is True
    assert shared.lark_cli_enabled(_lark_config(lark_vault_id="")) is False


def test_build_lark_session_env_group_injects_location_only():
    # 群聊：只补「这条消息在哪个群/话题/触发消息」的 Bot 定位上下文，绝不注入任何用户身份。
    env = shared.build_lark_session_env(
        _trigger(chat_id="oc-9", thread_id="th-9", message_id="om-9", create_time=1234),
        "cli-app",
    )
    assert env["LARKSUITE_CLI_APP_ID"] == "cli-app"
    assert env["FEISHU_APP_ID"] == ""
    assert env["FEISHU_CONVERSATION_TYPE"] == "group"
    assert env["FEISHU_CHAT_ID"] == "oc-9"
    assert env["FEISHU_THREAD_ID"] == "th-9"
    assert env["FEISHU_TRIGGER_MESSAGE_ID"] == "om-9"
    assert env["FEISHU_TRIGGER_CREATE_TIME"] == "1234"
    assert env["FEISHU_IDENTITY_MODE"] == "bot_only"
    assert env["LARKSUITE_CLI_STRICT_MODE"] == "bot"
    # 关掉 CLI 更新/技能提示噪声，避免污染 shell 输出。
    assert env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] == "1"
    # 绝不注入任何个人身份 / 用户 token。
    assert not any("USER" in key or "OPEN_ID" in key for key in env)


def test_build_lark_session_env_p2p_marks_direct_and_omits_thread():
    env = shared.build_lark_session_env(_trigger(chat_type="p2p", thread_id=""))
    assert env["FEISHU_CONVERSATION_TYPE"] == "direct"
    assert env["FEISHU_IDENTITY_MODE"] == "bot_with_user_oauth"
    assert env["FEISHU_USER_OPEN_ID"] == "ou-cur"
    assert env["LARKSUITE_CLI_STRICT_MODE"] == "off"
    assert "FEISHU_THREAD_ID" not in env  # 没有话题就不带这个键


def test_authorization_prefers_stable_user_id_and_accepts_legacy_open_id():
    message = _trigger(user_open_id="ou-new-app", user_id="u-stable")

    assert shared.is_authorized(
        _lark_config(authorized_user_ids=("u-stable",)), message
    ) is True
    assert shared.is_authorized(
        _lark_config(authorized_open_ids=("ou-new-app",)), message
    ) is True
    assert shared.is_authorized(
        _lark_config(authorized_user_ids=("u-other",)), message
    ) is False


class _FakeArkProvision:
    """假方舟客户端：只覆盖 lark-cli 置备用到的环境/Vault/凭据接口，记录调用便于断言幂等。"""

    def __init__(self, *, environments=None, vaults=None, credentials=None):
        self._environments = list(environments or [])
        self._vaults = list(vaults or [])
        self._credentials = list(credentials or [])
        self.created_environments: list[dict] = []
        self.updated_environments: list[tuple[str, dict]] = []
        self.created_vaults: list[str] = []
        self.created_credentials: list[tuple] = []
        self.updated_credentials: list[tuple] = []
        self.deleted_credentials: list[tuple] = []

    async def list_environments(self) -> list[dict]:
        return self._environments

    async def create_environment(
        self, name, env=None, setup_script=None, packages=None
    ) -> dict:
        self.created_environments.append(
            {
                "name": name,
                "env": env,
                "setup_script": setup_script,
                "packages": packages,
            }
        )
        created = {"id": f"env-{len(self.created_environments)}", "name": name}
        self._environments.append(created)
        return created

    async def update_environment(self, environment_id, config) -> dict:
        self.updated_environments.append((environment_id, config))
        return {"id": environment_id}

    async def list_vaults(self) -> list[dict]:
        return self._vaults

    async def create_vault(self, display_name) -> str:
        self.created_vaults.append(display_name)
        vault_id = f"vlt-{len(self.created_vaults)}"
        self._vaults.append({"id": vault_id, "display_name": display_name})
        return vault_id

    async def list_credentials(self, vault_id) -> list[dict]:
        return [c for c in self._credentials if c.get("vault_id") == vault_id]

    async def create_environment_variable_credential(
        self, vault_id, display_name, secret_name, secret_value
    ) -> str:
        self.created_credentials.append((vault_id, display_name, secret_name, secret_value))
        return f"cred-{len(self.created_credentials)}"

    async def update_environment_credential(self, vault_id, credential_id, secret_value) -> None:
        self.updated_credentials.append((vault_id, credential_id, secret_value))

    async def delete_credential(self, vault_id, credential_id) -> None:
        self.deleted_credentials.append((vault_id, credential_id))


async def test_ensure_lark_cli_environment_creates_with_setup_script_and_app_id():
    ark = _FakeArkProvision()
    env_id = await shared.ensure_lark_cli_environment(ark, "cli_app1")
    assert env_id == "env-1"
    created = ark.created_environments[0]
    assert created["env"]["LARKSUITE_CLI_APP_ID"] == "cli_app1"  # App Id 明文进 Environment
    assert created["setup_script"] == shared.LARK_CLI_SETUP_SCRIPT  # 装 CLI 的脚本
    assert created["packages"] == {"pip": ["pypdf==6.19.0"]}


async def test_ensure_lark_cli_environment_is_idempotent_by_name():
    # 同名 Environment 已存在就原地同步配置，不再新建。
    name = shared._sanitize_name(
        f"ark-group-bot-tenant-token-v3-cli_app1-lark-cli-{shared.LARK_CLI_VERSION}"
    )[:60]
    ark = _FakeArkProvision(environments=[{"id": "env-existing", "name": name}])
    env_id = await shared.ensure_lark_cli_environment(ark, "cli_app1")
    assert env_id == "env-existing"
    assert ark.created_environments == []  # 没新建
    assert ark.updated_environments[0][0] == "env-existing"
    assert ark.updated_environments[0][1]["packages"] == {
        "pip": ["pypdf==6.19.0"]
    }


async def test_ensure_lark_cli_vault_creates_tenant_token_credential():
    ark = _FakeArkProvision()
    vault_id = await shared.ensure_lark_cli_vault(ark, "cli_app1", "tenant-token")
    assert vault_id == "vlt-1"
    # Vault 仅保存短期 token；App Secret 留在 Bot 主机。
    assert ark.created_credentials == [
        (
            "vlt-1",
            shared.LARK_CLI_CREDENTIAL_NAME,
            "LARKSUITE_CLI_TENANT_ACCESS_TOKEN",
            "tenant-token",
        )
    ]


async def test_ensure_lark_cli_vault_rotates_existing_token():
    # 同名 Vault + 同名凭据已存在：原地更新短期 token，不新建。
    vault_name = shared._sanitize_name("ark-group-bot-tenant-token-v3-cli_app1")[:100]
    ark = _FakeArkProvision(
        vaults=[{"id": "vlt-existing", "display_name": vault_name}],
        credentials=[{
            "vault_id": "vlt-existing",
            "id": "cred-old",
            "display_name": shared.LARK_CLI_CREDENTIAL_NAME,
            "auth_type": "environment_variable",
            "secret_name": "LARKSUITE_CLI_TENANT_ACCESS_TOKEN",
        }],
    )
    vault_id = await shared.ensure_lark_cli_vault(ark, "cli_app1", "new-token")
    assert vault_id == "vlt-existing"
    assert ark.created_vaults == [] and ark.created_credentials == []  # 都复用
    assert ark.updated_credentials == [("vlt-existing", "cred-old", "new-token")]


async def test_ensure_lark_cli_vault_migrates_legacy_secret_name():
    vault_name = shared._sanitize_name("ark-group-bot-tenant-token-v3-cli_app1")[:100]
    ark = _FakeArkProvision(
        vaults=[{"id": "vlt-existing", "display_name": vault_name}],
        credentials=[{
            "vault_id": "vlt-existing",
            "id": "cred-old",
            "display_name": shared.LARK_CLI_CREDENTIAL_NAME,
            "auth_type": "environment_variable",
            "secret_name": "FEISHU_APP_SECRET",
        }],
    )

    vault_id = await shared.ensure_lark_cli_vault(ark, "cli_app1", "new-token")

    assert vault_id == "vlt-existing"
    assert ark.deleted_credentials == [("vlt-existing", "cred-old")]
    assert ark.created_credentials == [
        (
            "vlt-existing",
            shared.LARK_CLI_CREDENTIAL_NAME,
            "LARKSUITE_CLI_TENANT_ACCESS_TOKEN",
            "new-token",
        )
    ]


async def test_fetch_feishu_tenant_access_token_parses_value_and_expiry():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"code": 0, "tenant_access_token": "t-token", "expire": 7200},
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        token = await shared.fetch_feishu_tenant_access_token(
            "cli-app", "app-secret", client
        )

    assert token == shared.FeishuTenantToken("t-token", 7200)


async def test_update_lark_cli_vault_token_updates_matching_credential():
    ark = _FakeArkProvision(
        credentials=[{
            "vault_id": "vlt-1",
            "id": "cred-1",
            "display_name": shared.LARK_CLI_CREDENTIAL_NAME,
            "auth_type": "environment_variable",
            "secret_name": shared.LARK_CLI_SECRET_ENV_NAME,
        }]
    )

    await shared.update_lark_cli_vault_token(ark, "vlt-1", "fresh-token")

    assert ark.updated_credentials == [("vlt-1", "cred-1", "fresh-token")]


# ---- is_reset_command：/new 指令识别（剥掉开头的 @提及前缀再比对）------------

def test_is_reset_command_plain_new():
    # 私聊直接发 /new，没有 @ 前缀，照样命中。
    assert shared.is_reset_command("/new") is True


def test_is_reset_command_with_bot_mention_prefix():
    # 群里必须 @bot，正文因此带 `@群助手 ` 前缀——剥掉后仍是 /new，应命中。
    assert shared.is_reset_command("@群助手 /new") is True


def test_is_reset_command_with_multiple_mention_prefixes():
    # 开头连续多个 @名字（@bot 后又顺手 @了人）也要能剥干净。
    assert shared.is_reset_command("@群助手 @张三 /new") is True


def test_is_reset_command_tolerates_surrounding_whitespace():
    assert shared.is_reset_command("  @群助手   /new  ") is True


def test_is_reset_command_rejects_new_with_trailing_text():
    # /new 后面还带内容，就不是纯重置指令，不该命中。
    assert shared.is_reset_command("@群助手 /new 顺便看看文件") is False


def test_is_reset_command_rejects_non_command():
    assert shared.is_reset_command("@群助手 你好") is False
    assert shared.is_reset_command("") is False
    assert shared.is_reset_command("newton /new") is False  # @ 不在开头、且非 mention 前缀
