"""客户端串行群聊 Bot 的行为测试。

关注点不是「方舟/飞书 SDK 能不能通」（那要真机），而是 **SerialGroupBot 这层的编排是否
符合预期**：多人在同一个群 @bot 时，共享会话、串行处理、每人各得一条回复、去重、
窗口上下文拼装、/new 重置、未授权拦截、session 失效(404)重建等。

做法：用假的 ArkClient / FeishuSender 替身注入 SerialGroupBot，在一个真实事件循环里
把「消息 -> accept -> 串行队列 -> 回复」整条链路跑完，再断言替身记录到的调用序列。
会话映射用 InMemorySessionMap（同 SqliteSessionMap 接口）避免碰 ~/.arkagent。

场景模型：一个群 oc-team 里有两个人——
  - Alice: ou-alice
  - Bob:   ou-bob
两人先后 @bot，观察 bot 的响应是否符合「共享会话 + 串行 + 各回各」。
"""
import asyncio
import sys
import threading
from pathlib import Path

import pytest

_GROUP_BOT_DIR = Path(__file__).resolve().parents[1] / "cases" / "group-bot"
if str(_GROUP_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_GROUP_BOT_DIR))

import shared  # noqa: E402
from client_serial_bot import SerialGroupBot, _result_to_text  # noqa: E402

from arkagent.ark import ArkError, RunResult  # noqa: E402
from arkagent.feishu import HistoryMessage, IncomingMessage, ResourceRef  # noqa: E402


# ---- 替身（fakes）----------------------------------------------------------

class FakeArk:
    """假方舟客户端：记录 create_session / run 的调用；run 的返回可编程。

    - create_session：每次返回递增的 sesn-N，模拟方舟建会话。
    - run：默认回声「已处理: <input 最后一行>」，可用 run_hook 注入失败/404 等。
    """

    def __init__(self):
        self.created = 0
        self.create_calls: list[tuple[str, str]] = []
        self.create_kwargs: list[dict] = []  # 每次 create_session 的 vault_ids/env_overrides 等
        self.run_calls: list[tuple[str, str]] = []
        self.run_hook = None  # Optional[Callable[[session_id, input, attempt_idx], RunResult]]
        self.uploads: list[str] = []            # 上传过的文件名
        self.mounts: list[tuple[str, str, str]] = []  # (session_id, file_id, mount_path)

    async def create_session(self, agent_id: str, environment_id: str, **kw) -> str:
        self.created += 1
        self.create_calls.append((agent_id, environment_id))
        self.create_kwargs.append(kw)
        return f"sesn-{self.created}"

    async def upload_file(self, name: str, mime_type: str, data: bytes) -> str:
        self.uploads.append(name)
        return f"file-{len(self.uploads)}"

    async def add_session_file(self, session_id: str, file_id: str, mount_path: str) -> None:
        self.mounts.append((session_id, file_id, mount_path))

    async def run(self, session_id: str, actor_input: str, timeout_ms: int, **_kw) -> RunResult:
        idx = len(self.run_calls)
        self.run_calls.append((session_id, actor_input))
        if self.run_hook is not None:
            return self.run_hook(session_id, actor_input, idx)
        last_line = actor_input.strip().split("\n")[-1]
        return RunResult(terminal="idle", messages=[f"已处理: {last_line}"])


class FakeSender:
    """假飞书发送器：记录 reply / send_to_chat / react / delete_reaction / 历史读取。

    history_provider(message) -> list[HistoryMessage]，用于喂窗口上下文；默认无历史。
    """

    def __init__(self, history_provider=None):
        self.replies: list[tuple[str, str]] = []        # (message_id, text)
        self.chat_sends: list[tuple[str, str]] = []      # (chat_id, text)
        self.reactions: list[tuple[str, str]] = []       # (message_id, emoji)
        self.deleted_reactions: list[tuple[str, str]] = []  # (message_id, reaction_id)
        self._history_provider = history_provider
        self._reaction_seq = 0
        self.downloads: list[tuple[str, str, str]] = []  # (message_id, file_key, type)
        self.download_data: dict[str, bytes] = {}         # file_key -> bytes
        self.rosters: dict[str, dict[str, str]] = {}      # chat_id -> {name: open_id}
        self.roster_calls: list[str] = []                 # 记录 chat_roster 被查了几次
        self.reply_rosters: list[dict | None] = []        # 每次 reply 收到的名册
        self.send_rosters: list[dict | None] = []         # 每次 send_to_chat 收到的名册

    def reply(self, message_id: str, text: str, roster=None) -> None:
        self.replies.append((message_id, text))
        self.reply_rosters.append(roster)

    def send_to_chat(self, chat_id: str, text: str, roster=None) -> None:
        self.chat_sends.append((chat_id, text))
        self.send_rosters.append(roster)

    def chat_roster(self, chat_id: str) -> dict:
        self.roster_calls.append(chat_id)
        return self.rosters.get(chat_id, {})

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

    def download_resource(self, message_id: str, file_key: str, resource_type: str) -> bytes:
        self.downloads.append((message_id, file_key, resource_type))
        return self.download_data.get(file_key, b"binary-bytes")


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
         chat_type: str = "group", ts: int = 1000, mentioned_bot: bool = True,
         resources: tuple = (), user_name: str = "") -> IncomingMessage:
    return IncomingMessage(
        event_id=eid,
        message_id=mid,
        chat_id=chat_id,
        chat_type=chat_type,
        thread_id=thread_id,
        user_open_id=user,
        user_name=user_name,
        tenant_key="t-1",
        text=text,
        mentioned_bot=mentioned_bot,
        create_time=ts,
        resources=resources,
    )


# ---- 事件循环 fixture：在后台线程跑一个真实 loop（贴近生产 accept 跨线程投递）----

@pytest.fixture()
def loop():
    lp = asyncio.new_event_loop()
    t = threading.Thread(target=lp.run_forever, daemon=True)
    t.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)
    t.join(timeout=2)
    lp.close()


def _drain(lp: asyncio.AbstractEventLoop, timeout: float = 2.0) -> None:
    """等 loop 上已排的任务都跑完：accept 是异步投递，断言前需要 barrier。

    连做两次「切到 loop 线程再让出」，确保 call_soon_threadsafe 排的 _enqueue 和它创建的
    串行任务都被执行过一轮。"""
    for _ in range(2):
        fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0), lp)
        fut.result(timeout=timeout)


def _make_bot(loop, *, config=None, sender=None, ark=None, sessions=None):
    ark = ark or FakeArk()
    sender = sender or FakeSender()
    sessions = sessions if sessions is not None else shared.InMemorySessionMap()
    bot = SerialGroupBot(config or _config(), ark, sender, loop, sessions=sessions)
    return bot, ark, sender, sessions


def _wait_until(pred, lp, *, tries: int = 20, timeout: float = 2.0) -> bool:
    """反复 drain 直到 pred() 为真或超过 tries；用于等串行链路（含多轮）跑完。"""
    for _ in range(tries):
        if pred():
            return True
        _drain(lp, timeout=timeout)
    return pred()


# ---- 1. 两个人先后 @bot：共享同一个 Session，各得一条回复 --------------------

def test_two_users_share_one_session_each_gets_reply(loop):
    bot, ark, sender, _ = _make_bot(loop)

    assert bot.accept(_msg("ou-alice", "@bot 帮我查下今天排期", mid="om-1", eid="ev-1", ts=1000)) is True
    assert bot.accept(_msg("ou-bob", "@bot 我也要一份", mid="om-2", eid="ev-2", ts=2000)) is True

    assert _wait_until(lambda: len(sender.replies) >= 2, loop)

    # 只建了一个共享 Session（第二个人复用），验证「一个群共享一个 session」。
    assert ark.created == 1
    assert {sid for sid, _ in ark.run_calls} == {"sesn-1"}
    # 两个人各收到一条回复，且都 reply 到各自的原消息。
    assert [mid for mid, _ in sender.replies] == ["om-1", "om-2"]


# ---- 2. 串行顺序：Alice 先入队则 Alice 先被 run（不因 Bob 抢跑）--------------

def test_serial_order_follows_arrival(loop):
    order: list[str] = []
    ark = FakeArk()

    def hook(session_id, actor_input, idx):
        order.append(actor_input.strip().split("\n")[-1])
        return RunResult(terminal="idle", messages=[f"ok-{idx}"])

    ark.run_hook = hook
    bot, ark, sender, _ = _make_bot(loop, ark=ark)

    bot.accept(_msg("ou-alice", "第一个", mid="om-1", eid="ev-1", ts=1000))
    bot.accept(_msg("ou-bob", "第二个", mid="om-2", eid="ev-2", ts=2000))

    assert _wait_until(lambda: len(order) >= 2, loop)
    assert order == ["ou-alice: 第一个", "ou-bob: 第二个"]


# ---- 3. 去重：同一 event_id 重投只处理一次 -----------------------------------

def test_duplicate_event_is_dropped(loop):
    bot, ark, sender, _ = _make_bot(loop)

    first = bot.accept(_msg("ou-alice", "@bot 你好", mid="om-1", eid="ev-dup", ts=1000))
    second = bot.accept(_msg("ou-alice", "@bot 你好", mid="om-1", eid="ev-dup", ts=1000))

    assert first is True
    assert second is False  # 去重命中，直接在 accept 拒掉
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)
    assert len(sender.replies) == 1
    assert len(ark.run_calls) == 1


# ---- 4. 群里没 @bot 的消息被丢弃（should_handle=False）-----------------------

def test_group_message_without_mention_is_ignored(loop):
    bot, ark, sender, _ = _make_bot(loop)
    handled = bot.accept(
        _msg("ou-alice", "你们看了球赛没", mid="om-1", eid="ev-1", ts=1000, mentioned_bot=False)
    )
    assert handled is False
    _drain(loop)
    assert ark.run_calls == []
    assert sender.replies == []


# ---- 5. 窗口上下文：把「上一次 @bot -> 现在」的群聊转录拼进 input -------------

def test_windowed_input_includes_group_transcript(loop):
    captured: list[str] = []
    ark = FakeArk()
    ark.run_hook = lambda sid, inp, idx: (captured.append(inp), RunResult("idle", ["ok"]))[1]

    def history(_message):
        # 上一次 @bot（m-a）之后，Bob 补了一句闲聊（m-b），然后 Alice 现在 @bot。
        return [
            HistoryMessage("m-a", "ou-alice", "Alice", "user", "老板要季度总结", 900, at_bot=True),
            HistoryMessage("m-b", "ou-bob", "Bob", "user", "我这边数据准备好了", 950),
        ]

    sender = FakeSender(history_provider=history)
    bot, ark, sender, _ = _make_bot(loop, ark=ark, sender=sender)

    bot.accept(_msg("ou-alice", "整理成一页纸", mid="om-cur", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(captured) >= 1, loop)

    lines = captured[0].split("\n")
    assert lines == [
        "Alice: 老板要季度总结",
        "Bob: 我这边数据准备好了",
        "ou-alice: 整理成一页纸",  # 当前请求作为最后一行
    ]


# ---- 6. /new 重置会话：不 run，回执文案，且下一条会新建 Session --------------

def test_new_command_resets_session(loop):
    bot, ark, sender, sessions = _make_bot(loop)

    bot.accept(_msg("ou-alice", "@bot 先聊聊", mid="om-1", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)
    assert ark.created == 1

    bot.accept(_msg("ou-alice", "/new", mid="om-2", eid="ev-2", ts=2000))
    assert _wait_until(lambda: any("已重置" in t for _, t in sender.replies), loop)
    # /new 不应触发方舟 run
    assert len(ark.run_calls) == 1
    # 会话已从映射里清掉
    assert sessions.get(shared.GroupConversationKey("t-1", "oc-team", "")) is None

    # 下一条消息会重新建 Session（created 变成 2）
    bot.accept(_msg("ou-bob", "@bot 新话题", mid="om-3", eid="ev-3", ts=3000))
    assert _wait_until(lambda: ark.created >= 2, loop)


def test_new_command_with_at_prefix_resets_session(loop):
    # 群里必须 @bot 消息才进得来，正文因此带 `@群助手 ` 前缀——重置判定要能剥掉它再命中 /new。
    bot, ark, sender, sessions = _make_bot(loop)

    bot.accept(_msg("ou-alice", "@bot 先聊聊", mid="om-1", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)
    assert ark.created == 1

    bot.accept(_msg("ou-alice", "@群助手 /new", mid="om-2", eid="ev-2", ts=2000))
    assert _wait_until(lambda: any("已重置" in t for _, t in sender.replies), loop)
    # 仍不触发方舟 run，且会话已被清掉
    assert len(ark.run_calls) == 1
    assert sessions.get(shared.GroupConversationKey("t-1", "oc-team", "")) is None


# ---- 7. 未授权用户被拦截：不 run，回执提示 -----------------------------------

def test_unauthorized_user_rejected(loop):
    cfg = _config(authorized_open_ids=("ou-alice",))
    bot, ark, sender, _ = _make_bot(loop, config=cfg)

    bot.accept(_msg("ou-bob", "@bot 让我进来", mid="om-1", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)
    assert any("未授权" in t for _, t in sender.replies)
    assert ark.run_calls == []


# ---- 8. Session 失效(404)兜底：重建新 Session 并重跑本轮 ----------------------

def test_session_404_is_rebuilt_and_retried(loop):
    ark = FakeArk()

    def hook(session_id, actor_input, idx):
        if idx == 0:  # 第一次 run：命中过期会话
            raise ArkError("session not found", status_code=404, body="{}")
        return RunResult(terminal="idle", messages=["重建后成功"])

    ark.run_hook = hook
    # 预置一个「已存在但已失效」的会话，模拟重启后 SQLite 里留着旧 session_id。
    sessions = shared.InMemorySessionMap()
    key = shared.GroupConversationKey("t-1", "oc-team", "")
    sessions.save(key, "sesn-stale")
    bot, ark, sender, sessions = _make_bot(loop, ark=ark, sessions=sessions)

    bot.accept(_msg("ou-alice", "@bot 在吗", mid="om-1", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)

    # 命中 404 后重建了新 Session，并覆盖落库。
    assert ark.created == 1                       # 重建了一次
    assert len(ark.run_calls) == 2                # 失败一次 + 重跑一次
    assert ark.run_calls[0][0] == "sesn-stale"    # 首跑用旧的
    assert ark.run_calls[1][0] == "sesn-1"        # 重跑用新的
    assert sessions.get(key) == "sesn-1"          # 映射已更新为新会话
    assert sender.replies == [("om-1", "重建后成功")]


# ---- 9. 稍等表情：处理中贴 OneSecond，回复后撤回 -----------------------------

def test_ack_reaction_added_then_removed(loop):
    bot, ark, sender, _ = _make_bot(loop)
    bot.accept(_msg("ou-alice", "@bot 处理下", mid="om-1", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(sender.deleted_reactions) >= 1, loop)

    assert sender.reactions == [("om-1", "OneSecond")]      # 贴了稍等
    assert sender.deleted_reactions == [("om-1", "rx-1")]   # 回复后撤回


# ---- 10. 不同话题(thread)各自独立成会话 --------------------------------------

def test_threads_are_separate_sessions(loop):
    bot, ark, sender, sessions = _make_bot(loop)

    bot.accept(_msg("ou-alice", "@bot 话题A", mid="om-1", eid="ev-1", thread_id="th-A", ts=1000))
    bot.accept(_msg("ou-bob", "@bot 话题B", mid="om-2", eid="ev-2", thread_id="th-B", ts=2000))
    assert _wait_until(lambda: len(sender.replies) >= 2, loop)

    # 两个话题各建一个 Session，键彼此隔离。
    assert ark.created == 2
    assert sessions.get(shared.GroupConversationKey("t-1", "oc-team", "th-A")) is not None
    assert sessions.get(shared.GroupConversationKey("t-1", "oc-team", "th-B")) is not None


# ---- 11. run 结果转文本的边界（失败/空回复都应抛，触发 _run 的兜底回执）-------

def test_result_to_text_raises_on_failed_or_empty():
    with pytest.raises(RuntimeError):
        _result_to_text(RunResult(terminal="failed", messages=["x"]))
    with pytest.raises(RuntimeError):
        _result_to_text(RunResult(terminal="idle", messages=[]))
    assert _result_to_text(RunResult(terminal="idle", messages=["a", "b"])) == "b"


# ---- 12. Agent 执行失败：兜底回执把异常回给发问人（不吞、不崩队列）-----------

def test_failure_is_reported_to_user(loop):
    ark = FakeArk()

    def hook(session_id, actor_input, idx):
        return RunResult(terminal="failed", messages=[])  # _result_to_text 会抛

    ark.run_hook = hook
    bot, ark, sender, _ = _make_bot(loop, ark=ark)

    bot.accept(_msg("ou-alice", "@bot 触发失败", mid="om-1", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)
    assert any("执行失败" in t for _, t in sender.replies)


# ---- 13. 多模态：带文件的消息先下载上传挂载，再把路径拼进 input ---------------

def test_attachment_downloaded_uploaded_mounted_before_run(loop, monkeypatch):
    monkeypatch.setenv("GROUP_BOT_MULTIMODAL", "1")
    captured: list[str] = []
    ark = FakeArk()
    ark.run_hook = lambda sid, inp, idx: (captured.append(inp), RunResult("idle", ["ok"]))[1]
    sender = FakeSender()
    sender.download_data["fk-pdf"] = b"%PDF-1.7 ..."
    bot, ark, sender, _ = _make_bot(loop, ark=ark, sender=sender)

    msg = _msg(
        "ou-alice", "@bot 看看这份报告", mid="om-1", eid="ev-1", ts=1000,
        resources=(ResourceRef(file_key="fk-pdf", file_name="报告.pdf", type="file"),),
    )
    bot.accept(msg)
    assert _wait_until(lambda: len(captured) >= 1, loop)

    # 下载 → 上传 → 挂到本 Session，且挂载发生在 run（拿到 input）之前。
    assert sender.downloads == [("om-1", "fk-pdf", "file")]
    assert ark.uploads == ["报告.pdf"]
    assert len(ark.mounts) == 1
    session_id, file_id, mount_path = ark.mounts[0]
    assert session_id == "sesn-1" and file_id == "file-1" and mount_path.endswith("/报告.pdf")
    # 正文把挂载路径拼进去了，供 Agent 读取。
    assert "文件已挂载到（请用文件工具读取）：" in captured[0]
    assert f"/mnt/session/uploads/{mount_path}" in captured[0]


def test_attachment_from_history_message_is_mounted(loop, monkeypatch):
    # 复现「读文件有 bug」：文件是 om-file 单独发的，触发消息 om-cur 只有正文、无附件。
    # 附件应从落进窗口的历史消息里收出来，按各自 message_id 下载并挂到本 Session。
    monkeypatch.setenv("GROUP_BOT_MULTIMODAL", "1")
    captured: list[str] = []
    ark = FakeArk()
    ark.run_hook = lambda sid, inp, idx: (captured.append(inp), RunResult("idle", ["ok"]))[1]

    def history(_message):
        return [
            HistoryMessage(
                "om-file", "ou-alice", "Alice", "user", "[文件：office-requirements.pdf]", 900,
                resources=(ResourceRef(
                    file_key="fk-pdf", file_name="office-requirements.pdf", type="file",
                    message_id="om-file",
                ),),
            ),
        ]

    sender = FakeSender(history_provider=history)
    sender.download_data["fk-pdf"] = b"%PDF-1.7 ..."
    bot, ark, sender, _ = _make_bot(loop, ark=ark, sender=sender)

    # 触发消息本身没有附件，只有正文。
    bot.accept(_msg("ou-bob", "@bot 说说这个 PDF", mid="om-cur", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(captured) >= 1, loop)

    # 关键：按历史文件所属消息 om-file 下载（而非触发消息 om-cur），并挂到本 Session。
    assert sender.downloads == [("om-file", "fk-pdf", "file")]
    assert ark.uploads == ["office-requirements.pdf"]
    assert len(ark.mounts) == 1
    assert "文件已挂载到（请用文件工具读取）：" in captured[0]


def test_attachment_ignored_when_multimodal_disabled(loop, monkeypatch):
    monkeypatch.setenv("GROUP_BOT_MULTIMODAL", "off")
    captured: list[str] = []
    ark = FakeArk()
    ark.run_hook = lambda sid, inp, idx: (captured.append(inp), RunResult("idle", ["ok"]))[1]
    sender = FakeSender()
    bot, ark, sender, _ = _make_bot(loop, ark=ark, sender=sender)

    bot.accept(_msg(
        "ou-alice", "@bot 看看这份报告", mid="om-1", eid="ev-1", ts=1000,
        resources=(ResourceRef(file_key="fk-pdf", file_name="报告.pdf", type="file"),),
    ))
    assert _wait_until(lambda: len(captured) >= 1, loop)

    # 关闭多模态：不下载、不上传、不挂载，正文只留忽略提示。
    assert sender.downloads == []
    assert ark.uploads == [] and ark.mounts == []
    assert "[附件已忽略：多模态未开启]" in captured[0]


def test_same_attachment_not_redownloaded_or_remounted_in_session(loop, monkeypatch):
    # 同一个 file_key 在同一 session 里两轮引用：第一轮下载+上传+挂载，第二轮全部跳过。
    monkeypatch.setenv("GROUP_BOT_MULTIMODAL", "1")
    captured: list[str] = []
    ark = FakeArk()
    ark.run_hook = lambda sid, inp, idx: (captured.append(inp), RunResult("idle", ["ok"]))[1]
    sender = FakeSender()
    sender.download_data["fk-pdf"] = b"%PDF-1.7 ..."
    bot, ark, sender, _ = _make_bot(loop, ark=ark, sender=sender)

    res = (ResourceRef(file_key="fk-pdf", file_name="报告.pdf", type="file"),)
    bot.accept(_msg("ou-alice", "@bot 第一次发", mid="om-1", eid="ev-1", ts=1000, resources=res))
    assert _wait_until(lambda: len(captured) >= 1, loop)
    bot.accept(_msg("ou-bob", "@bot 再看下同一个文件", mid="om-2", eid="ev-2", ts=2000, resources=res))
    assert _wait_until(lambda: len(captured) >= 2, loop)

    # 只下载一次、只上传一次、只挂载一次——第二轮命中缓存 + 挂载记录，全跳过。
    assert sender.downloads == [("om-1", "fk-pdf", "file")]
    assert ark.uploads == ["报告.pdf"]
    assert len(ark.mounts) == 1
    # 但两轮正文都带上了同一条挂载路径，供 Agent 继续引用。
    _, _, mount_path = ark.mounts[0]
    assert all(f"/mnt/session/uploads/{mount_path}" in text for text in captured[:2])


# ---- 14. lark-cli：配了 Vault 时建 Session 挂 vault + 注入定位环境变量 --------

def test_create_session_mounts_lark_vault_and_env_when_enabled(loop):
    # 配了 lark_vault_id：建 Session 时挂上该 Vault，并注入本群/话题定位环境变量。
    bot, ark, sender, _ = _make_bot(loop, config=_config(lark_vault_id="vlt-lark"))
    bot.accept(_msg("ou-alice", "@bot 把这条整理进文档", mid="om-1", eid="ev-1",
                    thread_id="th-9", ts=1000))
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)

    assert ark.created == 1
    kw = ark.create_kwargs[0]
    assert kw["vault_ids"] == ["vlt-lark"]                 # 挂上存 App Secret 的 Vault
    assert kw["env_overrides"]["FEISHU_CHAT_ID"] == "oc-team"
    assert kw["env_overrides"]["FEISHU_THREAD_ID"] == "th-9"
    assert kw["env_overrides"]["FEISHU_TRIGGER_MESSAGE_ID"] == "om-1"


def test_create_session_no_lark_vault_stays_plain(loop):
    # 没配 Vault：不挂 vault、不注入定位变量，Agent 退回纯对话。
    bot, ark, sender, _ = _make_bot(loop)  # 默认 lark_vault_id=""
    bot.accept(_msg("ou-alice", "@bot 你好", mid="om-1", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)

    kw = ark.create_kwargs[0]
    assert kw.get("vault_ids") is None
    assert kw.get("env_overrides") is None


# ---- 15. 可点击 @：群回复时取本群名册并透传给 reply ---------------------------

def test_group_reply_passes_chat_roster(loop):
    # 群聊回复：先取本群名册，再把它透传给 sender.reply，供出站把 @人名 渲成可点击提及。
    sender = FakeSender()
    sender.rosters["oc-team"] = {"张三": "ou-zhangsan"}
    bot, _, sender, _ = _make_bot(loop, sender=sender)

    bot.accept(_msg("ou-alice", "@bot 让张三跟进", mid="om-1", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)

    assert sender.roster_calls == ["oc-team"]                  # 取了本群名册
    assert sender.reply_rosters[-1] == {"张三": "ou-zhangsan"}  # 名册被透传给 reply


def test_p2p_reply_skips_roster(loop):
    # 私聊没有 @ 别人的语义：不取名册，透传给发送的名册为空。
    sender = FakeSender()
    bot, _, sender, _ = _make_bot(loop, sender=sender)

    bot.accept(_msg("ou-alice", "你好", mid="om-1", eid="ev-1", ts=1000, chat_type="p2p"))
    assert _wait_until(lambda: len(sender.chat_sends) >= 1, loop)

    assert sender.roster_calls == []           # 私聊不拉名册
    assert sender.send_rosters[-1] == {}        # 透传空名册


def test_roster_fetch_failure_does_not_break_reply(loop):
    # 名册拉取抛错：兜底退回不 @（空名册），回复照常发出。
    sender = FakeSender()

    def _boom(_chat_id):
        raise RuntimeError("roster api down")

    sender.chat_roster = _boom  # type: ignore[assignment]
    bot, _, sender, _ = _make_bot(loop, sender=sender)

    bot.accept(_msg("ou-alice", "@bot 找张三", mid="om-1", eid="ev-1", ts=1000))
    assert _wait_until(lambda: len(sender.replies) >= 1, loop)

    assert sender.reply_rosters[-1] == {}   # 名册失败 → 空名册，但回复没丢

