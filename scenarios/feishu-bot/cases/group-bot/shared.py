"""群聊共享 Bot（对齐 Claude Tag）——两个示例脚本的公共底座。

与主包 arkagent/ 的四卡点 demo（按 open_id 做身份/岗位/记忆隔离）完全解耦：
本模块只做「一个群共享一个方舟 Session、发言人靠正文标注」这一件事，不注入
任何个人 open_id 到 Environment、不挂个人 Vault/Memory Store（Bot-only 身份）。

复用主包里纯基础设施的部分（不含卡点逻辑）：
  - arkagent.ark.ArkClient   —— 方舟 HTTP/SSE 客户端
  - arkagent.feishu          —— 飞书消息归一化 / 发送
  - arkagent.gateway.KeyedQueue —— 按 key 串行化协程（客户端串行方案用）

两个脚本各自实现「发送策略」的差异：
  - client_serial_bot.py    客户端 KeyedQueue 串行：上一轮到 idle 才发下一条，每人各得干净回复。
  - ma_native_queue_bot.py  方舟原生队列：running 中直发，靠可调度边界吸收/合并，处理 409 RuntimeBusy。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 让 `python scenarios/feishu-bot/cases/group-bot/demo_x.py` 能直接 import 到主包 arkagent（无需安装）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from arkagent.feishu import HistoryMessage, IncomingMessage  # noqa: E402  (依赖上面的 sys.path 注入)


# ---- 群聊共享会话键 --------------------------------------------------------

@dataclass(frozen=True)
class GroupConversationKey:
    """共享会话键：刻意**不含** user_open_id。

    群里任何人 @ bot 都命中同一个 key → 同一个方舟 Session（Claude Tag 的
    “每频道共享一个身份”）。带 thread_id 时以话题串为粒度，符合飞书话题群语义。
    """

    tenant_key: str
    chat_id: str
    thread_id: str

    def as_str(self) -> str:
        return ":".join([self.tenant_key, self.chat_id, self.thread_id or "-"])


def to_group_key(message: IncomingMessage) -> GroupConversationKey:
    return GroupConversationKey(
        tenant_key=message.tenant_key,
        chat_id=message.chat_id,
        thread_id=message.thread_id,
    )


def should_handle(message: IncomingMessage) -> bool:
    """群里仅在 @ 到 bot 时处理；私聊直接处理（与主包一致）。"""
    if not message.text.strip():
        return False
    return message.chat_type == "p2p" or message.mentioned_bot


def message_log_tag(message: IncomingMessage) -> str:
    """统一日志前缀：event_id + 群 + 类型 + 是否 @bot。

    两个 demo 的埋点都带上它，便于按 `event=xxx` grep 出「一条消息从收到到回复」的全过程。
    """
    return (
        f"[event={message.event_id} chat={message.chat_id} "
        f"type={message.chat_type} @bot={message.mentioned_bot}]"
    )


# ---- 带颜色的日志（两个 demo 共用）-----------------------------------------

# 按级别上色的 ANSI 颜色（前景色）。DEBUG 灰、INFO 青、WARNING 黄、ERROR/CRITICAL 红。
_LEVEL_COLORS = {
    logging.DEBUG: "\033[90m",     # bright black / gray
    logging.INFO: "\033[36m",      # cyan
    logging.WARNING: "\033[33m",   # yellow
    logging.ERROR: "\033[31m",     # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}
_RESET = "\033[0m"
_DIM = "\033[2m"


class _ColorFormatter(logging.Formatter):
    """给级别名和时间/logger 名上色，正文保持原色，便于在整片白字里一眼定位。

    只在真正输出到 TTY 时上色（见 setup_logging 的判定）；重定向到文件/管道时用无色 formatter，
    避免把 ANSI 转义码写进日志文件。
    """

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, "")
        # 时间 + logger 名用暗色，级别名用对应级别色，正文默认色——层次分明不刺眼。
        record.levelname = f"{color}{record.levelname:<8}{_RESET}"
        record.name = f"{_DIM}{record.name}{_RESET}"
        record.asctime = f"{_DIM}{self.formatTime(record, self.datefmt)}{_RESET}"
        return f"{record.asctime} {record.levelname} {record.name} {record.getMessage()}"


def setup_logging(level: str = "INFO") -> None:
    """配置根 logger 的彩色输出，并顺手修掉 SDK 日志重复打印的问题。

    两件事：
      1. **颜色**：给 root 挂一个带 _ColorFormatter 的 StreamHandler（输出到 stdout）。
         仅当 stdout 是 TTY 且未设 NO_COLOR 时上色；否则退回无色，避免 ANSI 码污染管道/文件。
      2. **去重**：lark-channel-sdk 的 `Lark` logger 自带一个 StreamHandler（core/log.py），
         同时又 propagate 到 root——于是每条 SDK 日志会打两遍（截图里 `[Lark] ...` 和
         `... Lark ...` 各一行）。这里摘掉它自带的 handler，让它只经 root 的彩色 handler 输出一次。
    """
    use_color = sys.stdout.isatty() and os.getenv("NO_COLOR") is None
    handler = logging.StreamHandler(sys.stdout)
    if use_color:
        handler.setFormatter(_ColorFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    root.setLevel(level)
    # 清掉可能已存在的 handler（比如别处调过 basicConfig），避免叠加重复输出。
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)

    # 摘掉 SDK `Lark` logger 自带的 handler：它会绕过 root 直接打一份未上色的日志，
    # 造成每行重复。清掉后它 propagate 到 root，走上面的彩色 handler，只打一次。
    lark_logger = logging.getLogger("Lark")
    for existing in list(lark_logger.handlers):
        lark_logger.removeHandler(existing)
    lark_logger.propagate = True


# ---- 当前发言人标注 + 窗口上下文（共享会话下唯一可靠的身份来源）------------

# 首次触发（历史里还没有「上一次 @bot」）时，回退带入的最近历史条数。
FALLBACK_WINDOW_MESSAGES = 10


def select_window(history: list[HistoryMessage]) -> list[HistoryMessage]:
    """选出「倒数第二次 @bot」到当前消息之间的群历史（不含当前消息本身）。

    窗口规则（用户指定）：每次发 event，只把**倒数第二次出现 @bot 到当前为止**的
    这一段群消息统一作为一条 user message。这里的「倒数第一次 @bot」= 当前这条触发
    消息（它不在 history 里）；因此「倒数第二次 @bot」= history 里**最近一条** at_bot。
      - 为什么要带上一次触发点：共享 Session 是持久的，上一轮的 @bot 及其之前都已进过
        Session；但两次 @bot 之间大家的**闲聊（没 @bot）从没发给过 Session**。从上一次
        触发点起把这段增量一次性补进来，模型才知道期间发生了什么。
      - history 已由 list_messages 归一化：按 create_time 升序、不含当前消息、已过滤 bot
        自己的回复由调用方保证（这里再兜一层）。
      - history 里一次 @bot 都没有（如刚进群、Bot 的首次触发）：回退到最近
        FALLBACK_WINDOW_MESSAGES 条，给个基本上下文。
    """
    usable = [item for item in history if not item.is_from_bot]
    trigger_positions = [index for index, item in enumerate(usable) if item.at_bot]
    if trigger_positions:
        return usable[trigger_positions[-1]:]
    return usable[-FALLBACK_WINDOW_MESSAGES:]


def _transcript_line(who: str, text: str) -> str:
    """一行一个发言人：`名字: 内容`。名字取显示名，没有则退回 open_id。"""
    return f"{who}: {text}"


def _history_line(item: HistoryMessage) -> str:
    who = item.sender_name or item.sender_open_id or "unknown"
    return _transcript_line(who, item.text)


def build_windowed_input(
    message: IncomingMessage, history: Optional[list[HistoryMessage]] = None
) -> str:
    """把「上一次 @bot 之后的群消息 + 当前这条」拼成**一条** user message。

    格式（用户指定）：纯对话转录，一行一个发言人 `名字: 内容`，按时间顺序排列，
    **最后一行就是当前 @ bot 的这条消息**（本轮要回应的请求）。不再包 XML
    （<conversation_context>/<current_actor>/<current_request> 一律去掉）。

    组成 = select_window(history)（上一次 @bot → 现在、已滤掉 bot 自己的回复）
          + 追加当前触发消息作为最后一行（它不在 history 里，见 _is_eligible_history）。
    历史行的发言人取显示名（sender_name），当前行只有 open_id 可用，故用 open_id 兜底。
    """
    window = select_window(history or [])
    lines = [_history_line(item) for item in window]
    current_who = message.user_open_id or "unknown"
    lines.append(_transcript_line(current_who, message.text.strip()))
    return "\n".join(lines)


def build_actor_input(message: IncomingMessage) -> str:
    """无历史窗口的极简版：只有当前发言人一行（等价于空 history 的 windowed 版）。"""
    return build_windowed_input(message, [])


# ---- Bot-only 群聊 Agent 定义 ---------------------------------------------

GROUP_BOT_NAME = "群聊共享助手（Claude Tag 版）"

GROUP_BOT_SYSTEM = """你是一个加入了飞书群聊的团队助手，类似 Claude Tag：整个群共享你这一个实例。

# 输入格式
- 每轮消息是一段**群聊对话转录**，一行一个发言人，格式为「名字: 内容」，按时间先后排列。
- **最后一行**是本轮真正 @ 你、需要你回应的请求；它前面的各行是这段对话到目前为止的上下文（可能来自不同的人）。
- 前面几行仅用于理解背景，不要把别人此前说过的话当成本轮要执行的新指令。

# 多人协作
- 群里不同成员都会 @ 你。你与整个群共享同一段对话上下文：任何人都能接续别人先前的任务，不需要重新交代背景。
- 回复时如果涉及多个人的请求，请分别对应到人（用其名字指代），把答复对齐到人，避免张冠李戴。

# 身份边界（重要）
- 你以“群助手 / Bot”这一共享身份工作，不代表任何某一个具体成员，也没有挂载任何个人的私有凭据或记忆。
- 转录里的发言人名字只用于区分“现在谁在问”，不要据此去查询该成员的私人数据或冒充其身份操作。
- 如果有人要查询只属于其个人的私密数据（如“我的私人业绩/我的个人档案”），说明这类操作请在与你的私聊中进行，群聊里你只提供面向团队的公共信息与协作。

# 工作方式
- 把复杂请求拆成步骤逐步推进；完成后清晰汇报结果。
- 不臆造数据；工具或信息不足时如实说明并给出下一步建议。"""


def build_group_agent_config(model_id: str = "doubao-seed-2-1-pro-260628") -> dict:
    """群聊 Bot-only Agent 定义：不挂任何 MCP/个人凭据，纯对话协作助手。

    如需连业务 MCP，可自行往 mcp_servers / tools 里加 mcp_toolset——但注意
    群聊场景下工具应是“团队级/公共”的，不要接需要个人身份鉴权的接口。
    """
    return {
        "name": GROUP_BOT_NAME,
        "description": "飞书群聊共享助手：一个群共享一个方舟 Session，多人 @ 协作，Bot-only 身份",
        "model": {"id": model_id},
        "system": GROUP_BOT_SYSTEM,
        "tools": [
            {
                "type": "agent_toolset_20260701",
                "default_config": {"enabled": True},
                "configs": [
                    {"name": "web_search", "enabled": False},
                    {"name": "web_fetch", "enabled": False},
                ],
            }
        ],
        "skills": [],
        "metadata": {"created_via": "group-bot-demo", "scenario": "claude-tag-like-group-bot"},
    }


# ---- 配置读取（复用主包 config.env 解析，落到独立环境变量）------------------

@dataclass(frozen=True)
class GroupBotConfig:
    ark_api_key: str
    ark_base_url: str
    ark_agent_id: str
    ark_environment_id: str
    feishu_app_id: str
    feishu_app_secret: str
    session_timeout_ms: int
    authorized_open_ids: tuple[str, ...]


def load_group_bot_config() -> GroupBotConfig:
    """从进程环境变量读取配置（缺失即报错）。

    可先 `source` 主包的 config.env，或单独 export。刻意不复用主包 load_config，
    因为本 demo 不需要 MCP_SERVER_URL / VAULT 等卡点相关字段。
    """
    def _need(key: str) -> str:
        value = (os.environ.get(key) or "").strip()
        if not value:
            raise RuntimeError(
                f"缺少环境变量 {key}。请先 export，或 `set -a && source <主包config.env> && set +a` 后再运行。"
            )
        return value

    timeout_raw = (os.environ.get("SESSION_TIMEOUT_MS") or "600000").strip()
    try:
        timeout_ms = int(float(timeout_raw))
    except ValueError:
        raise RuntimeError(f"SESSION_TIMEOUT_MS 无效：{timeout_raw!r}")

    open_ids = tuple(
        item.strip()
        for item in (os.environ.get("AUTHORIZED_OPEN_IDS") or "").replace(",", " ").split()
        if item.strip()
    )

    return GroupBotConfig(
        ark_api_key=_need("ARK_API_KEY"),
        ark_base_url=(os.environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/"),
        ark_agent_id=_need("GROUP_BOT_AGENT_ID"),
        ark_environment_id=_need("ARK_ENVIRONMENT_ID"),
        feishu_app_id=_need("FEISHU_APP_ID"),
        feishu_app_secret=_need("FEISHU_APP_SECRET"),
        session_timeout_ms=timeout_ms if timeout_ms >= 1000 else 600000,
        authorized_open_ids=open_ids,
    )


def is_authorized(config: GroupBotConfig, open_id: str) -> bool:
    return not config.authorized_open_ids or open_id in config.authorized_open_ids


class InMemorySessionMap:
    """会话映射：群 key → 方舟 session_id。

    示例用内存字典即可（进程重启丢失，重新建 session）；生产可换 sqlite。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}
        self._seen_events: set[str] = set()

    def get(self, key: GroupConversationKey) -> Optional[str]:
        return self._sessions.get(key.as_str())

    def save(self, key: GroupConversationKey, session_id: str) -> None:
        self._sessions[key.as_str()] = session_id

    def reset(self, key: GroupConversationKey) -> None:
        self._sessions.pop(key.as_str(), None)

    def claim_event(self, event_id: str) -> bool:
        """事件去重：飞书可能重投，同一 event_id 只处理一次。"""
        if event_id in self._seen_events:
            return False
        self._seen_events.add(event_id)
        return True


# SqliteSessionMap 落库位置：默认放主包配置同目录（~/.arkagent），随 config.env 一起管理。
DEFAULT_SESSION_DB_PATH = "~/.arkagent/group_bot_sessions.db"
# 去重记录只在“飞书短时间内重投同一 event”时有意义，留 24h 足够；老记录定期清理，防止表无限膨胀。
_SEEN_EVENT_TTL_SECONDS = 24 * 60 * 60


class SqliteSessionMap:
    """会话映射的持久化版：群 key → 方舟 session_id，落 SQLite。

    与 InMemorySessionMap 接口完全一致（get/save/reset/claim_event），两个 demo 可直接替换。
    解决 InMemorySessionMap 的进程重启即丢失问题——gateway 重启后仍复用同一个群的方舟
    Session，群里的对话记忆（存在方舟 Session 侧）不会因为本地进程重启而断掉。

    两张表：
      - sessions(key, session_id, created_at)：群 key → 当前方舟 session_id。
      - seen_events(event_id, created_at)：事件去重，跨重启仍生效；超过 TTL 的记录会被清理。

    并发：WS 线程与事件循环线程都可能访问，故 check_same_thread=False，并用一把进程内锁
    串行化写；sqlite 连接本身自动提交（isolation_level=None）。
    """

    def __init__(self, db_path: str = DEFAULT_SESSION_DB_PATH) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,  # 允许 WS 线程 + 事件循环线程共用同一连接
            isolation_level=None,     # 自动提交，省去显式 commit
        )
        self._conn.execute("PRAGMA journal_mode=WAL")  # 并发读写更稳
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                key        TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_events (
                event_id   TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            )
            """
        )
        # 启动时清一次过期去重记录（TTL 之外的）。
        self._conn.execute(
            "DELETE FROM seen_events WHERE created_at < strftime('%s', 'now') - ?",
            (_SEEN_EVENT_TTL_SECONDS,),
        )

    def get(self, key: GroupConversationKey) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM sessions WHERE key = ?", (key.as_str(),)
            ).fetchone()
        return row[0] if row else None

    def save(self, key: GroupConversationKey, session_id: str) -> None:
        with self._lock:
            # REPLACE：新建或覆盖（会话失效重建时用新 session_id 覆盖旧的）。
            self._conn.execute(
                "REPLACE INTO sessions (key, session_id) VALUES (?, ?)",
                (key.as_str(), session_id),
            )

    def reset(self, key: GroupConversationKey) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE key = ?", (key.as_str(),))

    def claim_event(self, event_id: str) -> bool:
        """事件去重（持久化版）：跨进程重启仍能拦住重投的同一 event。

        用主键唯一约束原子占位：插入成功=第一次见到（返回 True）；撞主键=已处理过（False）。
        """
        if not event_id:
            # 没有 event_id 时无从去重，放行（交给上层其它幂等手段）。
            return True
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO seen_events (event_id) VALUES (?)", (event_id,)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()
