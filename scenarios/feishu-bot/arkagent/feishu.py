"""飞书接入层：Channel SDK 收消息 + im.v1 发消息 + 消息归一化。

对齐源项目 src/lark-channel.ts 的最新实现：入站改用官方 **lark-channel-sdk**
（`FeishuChannel`）——它在底层 WSClient/EventDispatcher/Client 之上把长连接、断线
重连、事件归一化（`InboundMessage`）、去重、策略过滤都打包好了，等于官方版的
`normalize_feishu_message` + 去重 + should_handle。我们只做两件事：
  1. 把 SDK 的 `InboundMessage` 映射回本项目既有的 `IncomingMessage`（保持下游
     gateway/demo 的接口不变）；
  2. 出站沿用 SDK 自带的同步 OpenAPI `Client`（API 与 lark-oapi 完全一致），
     `FeishuSender` 仍是同步调用，下游照旧用 run_in_executor 丢线程池。

保留下面的纯函数（normalize_feishu_message / normalize_history_item / ...）不变：
它们是 raw 事件/历史 dict 的映射逻辑，被单测覆盖，也用于读群历史归一化。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Protocol

# 引用链最多回溯几层：用户 @bot 那条 → 它引用的 → 再上一层……封顶避免有人恶意/无意
# 串成长链时把回查次数放大（每层一次 im.v1.message.get）。第 1 层是直接被引用的消息。
MAX_QUOTE_DEPTH = 5


@dataclass(frozen=True)
class IncomingMessage:
    event_id: str
    message_id: str
    chat_id: str
    chat_type: str  # "p2p" | "group"
    thread_id: str
    user_open_id: str
    tenant_key: str
    text: str
    mentioned_bot: bool
    create_time: int = 0  # 消息创建时间戳（毫秒）；窗口排序必需
    # 这条消息「引用/回复」的那条消息 id（飞书 parent_id，且 parent_id != root_id 时才是
    # 用户显式引用——话题根不算，见 Channel SDK normalize/pipeline.py）。为空表示没引用。
    # 有值时用它沿父链回溯出被引用内容（resolve_quote_chain），注入上下文。
    reply_to_message_id: str = ""
    # 话题根消息 id（飞书 root_id）：话题群里「基于某条消息发起话题」的那条由头消息。
    # 它在主时间线、不在 thread 容器里，故读话题历史时读不到；有值时单独把它（及其之前
    # 几条主时间线消息）作为「话题前情」补进上下文（load_thread_context）。非话题为空。
    root_id: str = ""


@dataclass(frozen=True)
class QuotedMessage:
    """引用链上的一条被引用消息（resolve_quote_chain 归一化后的结果）。

    用户 @bot 的那条消息可能引用了另一条消息，被引用的那条又可能引用更早的一条……
    逐层回溯得到一条「由近及远」的引用链。depth=1 是直接被引用的那条，depth 越大越久远。
    文本抽取复用历史归一化那套（_history_item_text），非文本消息给占位（[图片]/[文件…]）。
    """

    message_id: str
    sender_open_id: str
    sender_name: str
    text: str
    depth: int  # 1=直接引用，2=引用的引用，……最多到 MAX_QUOTE_DEPTH


@dataclass(frozen=True)
class HistoryMessage:
    """一条群历史消息（loadLarkRecentHistory 归一化后的结果）。

    对齐源项目 src/lark-channel.ts 的 ChannelHistoryMessage：文本已抽取、
    @提及 token 已替换为可读的 @名字，撤回消息标注为占位文本。
    另外记两个本 demo 窗口规则要用的判定：
      - at_bot：这条历史消息 @ 了当前 Bot（用来切窗口边界）。
      - is_from_bot：这条历史消息是 Bot 自己发的（回复），注入上下文时过滤掉。
    """

    message_id: str
    sender_open_id: str
    sender_name: str
    sender_type: str  # "user" | "app" | "anonymous" | ...
    text: str
    create_time: int
    at_bot: bool = False
    is_from_bot: bool = False


class GatewayLike(Protocol):
    def accept(self, message: IncomingMessage) -> None: ...


def normalize_feishu_message(event: dict) -> Optional[IncomingMessage]:
    """把 im.message.receive_v1 事件体归一化为 IncomingMessage；非文本/缺字段则丢弃。"""
    message = event.get("message") or {}
    if not message.get("message_id") or not message.get("chat_id") or message.get("message_type") != "text":
        return None
    try:
        content = json.loads(message.get("content") or "{}")
        text = str(content.get("text") or "")
    except (json.JSONDecodeError, ValueError):
        return None
    mentions = message.get("mentions") or []
    # @提及 token 换成可读的 @名字（含对 bot 自己的提及）：让转录里「谁在叫谁」保持可见，
    # 与历史归一化 _history_item_text、SDK 主路径 content_text 三处口径一致。无名字则删掉 token。
    for mention in mentions:
        key = mention.get("key")
        if key:
            name = mention.get("name")
            text = text.replace(key, f"@{name}" if name else "")
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    chat_type = "p2p" if message.get("chat_type") == "p2p" else "group"
    try:
        create_time = int(message.get("create_time") or 0)
    except (TypeError, ValueError):
        create_time = 0
    return IncomingMessage(
        event_id=event.get("event_id") or message.get("message_id"),
        message_id=message["message_id"],
        chat_id=message["chat_id"],
        chat_type=chat_type,
        thread_id=message.get("thread_id") or message.get("root_id") or message.get("parent_id") or "",
        user_open_id=sender_id.get("open_id") or "",
        tenant_key=event.get("tenant_key") or "default",
        text=text.strip(),
        mentioned_bot=bool(mentions),
        create_time=create_time,
    )


class FeishuSender:
    """基于 lark-channel-sdk 自带 OpenAPI Client 的消息发送器（reply / send / react / 读历史）。

    SDK 的 `lark_channel.Client` 与 lark-oapi 的 `Client` 接口一一对应（同步阻塞、
    builder 风格、response.success()），因此这里的实现与迁移前几乎一致，只是导入路径
    从 `lark_oapi` 换成 `lark_channel`。同步调用，下游用 run_in_executor 丢线程池。
    """

    def __init__(self, app_id: str, app_secret: str):
        import lark_channel as lark

        self._lark = lark
        self._client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
        self._bot_open_id: Optional[str] = None

    def bot_open_id(self) -> str:
        """当前 Bot 自己的 open_id（缓存）。窗口规则要用它判断历史里哪条是「@ 到 bot」，
        以及哪条是 bot 自己发的回复（注入上下文时要过滤）。走原生 /bot/v3/info。"""
        if self._bot_open_id is not None:
            return self._bot_open_id
        from lark_channel import AccessTokenType, BaseRequest, HttpMethod

        request = (
            BaseRequest.builder()
            .http_method(HttpMethod.GET)
            .uri("/open-apis/bot/v3/info")
            .token_types({AccessTokenType.TENANT})
            .build()
        )
        response = self._client.request(request)
        if not response.success():
            raise RuntimeError(f"读取 Bot 信息失败 {response.code}: {response.msg}")
        raw = response.raw.content if response.raw else None
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        bot = payload.get("bot") if isinstance(payload, dict) else None
        self._bot_open_id = str((bot or {}).get("open_id") or "")
        return self._bot_open_id

    def reply(self, message_id: str, text: str) -> None:
        from lark_channel.api.im.v1.model.reply_message_request import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        body = (
            ReplyMessageRequestBody.builder()
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .msg_type("text")
            .build()
        )
        request = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = self._client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(f"飞书回复失败 {response.code}: {response.msg}")

    def react(self, message_id: str, emoji_type: str) -> Optional[str]:
        """给某条消息加一个表情回应（im.v1.message_reaction.create）。
        群聊里比文字回执更轻量：@ bot 后直接在原消息下贴个「稍等」(OneSecond) 表情，
        表示已收到、正在处理，不刷屏。emoji_type 取值见飞书「表情文案说明」。
        返回本次表情的 reaction_id，供回复完成后 delete_reaction 撤回；拿不到则返回 None。"""
        from lark_channel.api.im.v1.model.create_message_reaction_request import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
        )
        from lark_channel.api.im.v1.model.emoji import Emoji

        body = (
            CreateMessageReactionRequestBody.builder()
            .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
            .build()
        )
        request = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = self._client.im.v1.message_reaction.create(request)
        if not response.success():
            raise RuntimeError(f"飞书表情回应失败 {response.code}: {response.msg}")
        return getattr(response.data, "reaction_id", None) if response.data else None

    def delete_reaction(self, message_id: str, reaction_id: str) -> None:
        """撤回此前贴的表情（im.v1.message_reaction.delete）。
        回复正式发出后，把「稍等」表情清掉——回执只在处理中有意义，处理完就该消失。"""
        from lark_channel.api.im.v1.model.delete_message_reaction_request import (
            DeleteMessageReactionRequest,
        )

        request = (
            DeleteMessageReactionRequest.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        response = self._client.im.v1.message_reaction.delete(request)
        if not response.success():
            raise RuntimeError(f"飞书表情撤回失败 {response.code}: {response.msg}")

    def send_to_chat(self, chat_id: str, text: str) -> None:
        from lark_channel.api.im.v1.model.create_message_request import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        response = self._client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"飞书发送失败 {response.code}: {response.msg}")

    def list_messages(
        self,
        message: "IncomingMessage",
        max_messages: int = 40,
        max_pages: int = 3,
    ) -> list["HistoryMessage"]:
        """读取当前群/话题的近期历史（移植 src/lark-channel.ts 的 loadLarkRecentHistory）。

        - container：话题群按 thread 容器，普通群按 chat 容器；chat 容器用 end_time
          截到当前消息，避免把「未来」的消息带进来。
        - 倒序拉取、按需翻页；归一化后按 create_time 升序、去重、排除触发消息本身。
        - 每条历史都会算出 at_bot / is_from_bot，供窗口规则切边界、过滤 bot 回复。
        窗口规则本身不在这里做（见 shared.py），这里只负责「把历史读全、读干净」。
        """
        from lark_channel.api.im.v1.model.list_message_request import ListMessageRequest

        bot_open_id = self.bot_open_id()
        source = "thread" if message.thread_id else "chat"
        container_id = message.thread_id if source == "thread" else message.chat_id

        collected: list[dict] = []
        page_token: Optional[str] = None
        for _ in range(max(1, max_pages)):
            builder = (
                ListMessageRequest.builder()
                .container_id_type(source)
                .container_id(container_id)
                .sort_type("ByCreateTimeDesc")
                .page_size(50)
            )
            if source == "chat" and message.create_time:
                # end_time 单位是秒；向上取整以包含当前这一秒的消息。
                builder = builder.end_time(str((message.create_time + 999) // 1000))
            if page_token:
                builder = builder.page_token(page_token)
            response = self._client.im.v1.message.list(builder.build())
            if not response.success():
                raise RuntimeError(f"读取群历史失败 {response.code}: {response.msg}")
            items = (response.data.items or []) if response.data else []
            collected.extend(_history_item_to_dict(item) for item in items)
            eligible = sum(1 for it in collected if _is_eligible_history(it, message))
            has_more = bool(response.data and response.data.has_more)
            page_token = response.data.page_token if response.data else None
            if eligible >= max_messages or not has_more or not page_token:
                break

        unique: dict[str, dict] = {}
        for item in collected:
            if _is_eligible_history(item, message):
                unique[item["message_id"]] = item
        normalized = [
            normalized_item
            for item in unique.values()
            if (normalized_item := normalize_history_item(item, bot_open_id)) is not None
        ]
        normalized.sort(key=lambda item: item.create_time)
        return normalized[-max_messages:]

    def get_message(self, message_id: str) -> Optional[dict]:
        """按 message_id 读取单条消息，拍平成与 list_messages 同构的 dict（含 parent_id）。

        用于引用链回溯：InboundMessage 只带「直接被引用的那条」id，要拿「引用的引用」需按
        parent_id 逐层 im.v1.message.get。读不到（已撤回/无权限/跨会话）返回 None，由上层降级。
        """
        from lark_channel.api.im.v1.model.get_message_request import GetMessageRequest

        request = GetMessageRequest.builder().message_id(message_id).build()
        response = self._client.im.v1.message.get(request)
        if not response.success():
            raise RuntimeError(f"读取消息失败 {response.code}: {response.msg}")
        items = (response.data.items or []) if response.data else []
        if not items:
            return None
        return _history_item_to_dict(items[0])

    def load_quote_chain(self, message: "IncomingMessage") -> list["QuotedMessage"]:
        """把 message 引用的那条、及其上溯的引用链读出来（最多 MAX_QUOTE_DEPTH 层）。

        纯 IO 编排：逐层调 get_message 拿 raw，交给纯函数 resolve_quote_chain 归一化 +
        防环 + 截断。get_message 是 lark-oapi 同步调用，由上层丢 executor；这里不吞异常，
        读失败交上层降级为「无引用」。"""
        return resolve_quote_chain(message.reply_to_message_id, self.get_message)

    def load_thread_context(
        self,
        message: "IncomingMessage",
        before_count: int = 3,
    ) -> list["HistoryMessage"]:
        """读话题「前情」：发起话题的根消息 + 它之前最多 before_count 条主时间线消息。

        话题群里读历史用的是 thread 容器，只含话题串内部的楼层——**发起话题的那条根消息
        （root_id）在主时间线、不在 thread 容器里，会被漏掉**；根消息往往正是整段讨论的由头。
        这里补上：
          1. get_message(root_id) 直接读根消息（不受话题边界限制、老消息也读得到）；
          2. chat 容器（普通群里只能取到各话题的根消息，即主时间线）以 end_time 截到根消息
             时间，取根之前最多 before_count 条主时间线消息，给根消息一点铺垫。
        非话题（root_id 空）直接返回 []。任一步读失败交上层降级为「无话题前情」。
        返回 create_time 升序，末尾即根消息。
        """
        if not message.root_id:
            return []
        bot_open_id = self.bot_open_id()
        root_item = self.get_message(message.root_id)
        if not root_item:
            return []
        context_items: dict[str, dict] = {message.root_id: root_item}
        root_time = _to_ms(root_item.get("create_time"))
        if before_count > 0 and root_time > 0 and message.chat_id:
            for item in self._list_chat_before(message.chat_id, root_time, before_count):
                mid = item.get("message_id")
                if mid and mid != message.root_id and _to_ms(item.get("create_time")) < root_time:
                    context_items[mid] = item
        normalized = [
            normalized_item
            for item in context_items.values()
            if (normalized_item := normalize_history_item(item, bot_open_id)) is not None
        ]
        normalized.sort(key=lambda item: item.create_time)
        # 只留「根 + 根之前 before_count 条」，多读的裁掉。
        return normalized[-(before_count + 1):]

    def _list_chat_before(self, chat_id: str, end_time_ms: int, limit: int) -> list[dict]:
        """读 chat 容器里 end_time 之前的一页消息（倒序），拍平成 dict。

        话题群里 chat 容器只回主时间线的话题根消息；配合 end_time 截到指定时刻，用于取
        「发起话题那条之前」的几条主时间线铺垫。limit 很小（默认 3），一页足矣，不翻页。
        """
        from lark_channel.api.im.v1.model.list_message_request import ListMessageRequest

        builder = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .sort_type("ByCreateTimeDesc")
            .page_size(max(limit + 5, 20))
            # end_time 单位是秒；向上取整以包含根消息那一秒（根消息会被调用方按 id 排除）。
            .end_time(str((end_time_ms + 999) // 1000))
        )
        response = self._client.im.v1.message.list(builder.build())
        if not response.success():
            raise RuntimeError(f"读取主时间线历史失败 {response.code}: {response.msg}")
        items = (response.data.items or []) if response.data else []
        return [_history_item_to_dict(item) for item in items]


def resolve_quote_chain(
    first_parent_id: str,
    fetch: "callable",
    max_depth: int = MAX_QUOTE_DEPTH,
) -> list["QuotedMessage"]:
    """沿 parent_id 链回溯出被引用消息列表（纯逻辑，fetch 注入便于测试）。

    - first_parent_id：@bot 那条消息直接引用的消息 id（IncomingMessage.reply_to_message_id）。
    - fetch(message_id) -> Optional[dict]：读单条消息的拍平 dict（含 parent_id），读不到给 None。
    - 逐层：拿到一条就归一化成 QuotedMessage（depth 从 1 递增），再顺着它的 parent_id 上溯。
    - 防环：seen 记录已访问 id，遇到重复即停（避免 A 引 B、B 引 A 之类死循环）。
    - 截断：最多 max_depth 层；到顶或某层读不到（None）就停，返回已拿到的部分。
    返回顺序：depth 升序（[直接引用, 引用的引用, ...]）。first_parent_id 为空直接返回 []。
    """
    if not first_parent_id:
        return []
    chain: list[QuotedMessage] = []
    seen: set[str] = set()
    current_id: Optional[str] = first_parent_id
    for depth in range(1, max_depth + 1):
        if not current_id or current_id in seen:
            break
        seen.add(current_id)
        item = fetch(current_id)
        if not item:
            break
        quoted = _quoted_from_item(item, depth)
        if quoted is not None:
            chain.append(quoted)
        current_id = str(item.get("parent_id") or "") or None
    return chain


def _quoted_from_item(item: dict, depth: int) -> Optional["QuotedMessage"]:
    """把一条飞书消息 dict 归一化为 QuotedMessage；文本抽取复用历史那套。

    撤回消息给占位文本；文本/富文本抽正文，图片/文件给占位。发言人取显示名，
    没有则退回 open_id；bot 自己发的消息也照常纳入（引用链里可能引用了 bot 的回复）。"""
    message_id = str(item.get("message_id") or "")
    if not message_id:
        return None
    mentions = item.get("mentions") or []
    if bool(item.get("deleted")):
        text = "[该消息已撤回，原内容不应继续作为有效依据]"
    else:
        text = _history_item_text(item.get("msg_type"), (item.get("body") or {}).get("content"), mentions)
    if not text:
        return None
    sender = item.get("sender") or {}
    return QuotedMessage(
        message_id=message_id,
        sender_open_id=str(sender.get("id") or "") or "unknown",
        sender_name=str(sender.get("sender_name") or ""),
        text=text,
        depth=depth,
    )


def _inbound_to_incoming(msg: object) -> Optional[IncomingMessage]:
    """把 Channel SDK 的 `InboundMessage` 映射回本项目的 `IncomingMessage`。

    只处理文本。SDK 的 `content_text` **保留了渲染后的 @名字**（含对 bot 自己的提及，
    见 lark_channel normalize/pipeline.py：“content_text itself keeps the rendered
    mention”）——所以当前触发消息里 `@小助手` 仍在，转录中「谁 @ 了谁」可见，与历史行口径一致。
    （SDK 另有剥掉 bot 提及的 body_text 视图，本项目不用它。）tenant_key SDK 未在
    归一化结果里透出（它藏在事件 header），这里从 mentions 里兜底取，取不到给 default
    ——共享会话键里 tenant_key 只是命名空间前缀，同租户内恒定即可。
    """
    if getattr(msg, "raw_content_type", None) != "text":
        return None
    conversation = getattr(msg, "conversation", None)
    sender = getattr(msg, "sender", None)
    text = (getattr(msg, "content_text", "") or "").strip()
    chat_type = "p2p" if getattr(conversation, "chat_type", "") == "p2p" else "group"
    mentions = getattr(msg, "mentions", None) or []
    tenant_key = next(
        (m.tenant_key for m in mentions if getattr(m, "tenant_key", None)),
        None,
    ) or "default"
    # SDK 已把「用户显式引用某条消息」归一化到 msg.reply（reply_to_message_id 便捷属性）；
    # 话题根不会进这里（pipeline 只在 parent_id != root_id 时才设 reply）。取到就带上，
    # 供 resolve_quote_chain 沿父链把被引用内容补进上下文。
    reply_to = getattr(msg, "reply_to_message_id", None) or (
        getattr(getattr(msg, "reply", None), "message_id", None) or ""
    )
    # 话题根 id 不在 InboundMessage 的一等字段里，但保留在 raw（include_raw 默认开）。
    # 话题群里它是「发起话题的那条主时间线消息」；用于 load_thread_context 补话题前情。
    raw = getattr(msg, "raw", None) or {}
    root_id = str(raw.get("root_id") or "") if isinstance(raw, dict) else ""
    return IncomingMessage(
        event_id=getattr(msg, "id", "") or "",
        message_id=getattr(msg, "id", "") or "",
        chat_id=getattr(conversation, "chat_id", "") or "",
        chat_type=chat_type,
        thread_id=getattr(conversation, "thread_id", None) or "",
        user_open_id=getattr(sender, "open_id", "") or "",
        tenant_key=tenant_key,
        text=text,
        mentioned_bot=bool(getattr(msg, "mentioned_bot", False)),
        create_time=int(getattr(msg, "create_time", 0) or 0),
        reply_to_message_id=reply_to or "",
        root_id=root_id,
    )


def start_feishu_gateway(app_id: str, app_secret: str, gateway: GatewayLike) -> None:
    """用 lark-channel-sdk 启动 WS 长连接，阻塞运行。收到消息映射后交给 gateway.accept。

    SDK 已经把「长连接 + 断线重连 + 归一化 + 去重」都做了，等价于原来手写的
    EventDispatcherHandler + normalize_feishu_message + claim_event 那一套。这里的策略：
      - transport=ws：与原实现一致，无需公网回调（另一个可选值是 webhook）。
      - policy.require_mention=False + group_policy=open：**不让 SDK 层拦**，是否处理仍由
        下游 gateway 的 should_handle 判定（群里只在 @bot 时处理），保持与迁移前行为一致。
      - safety.chat_queue.enabled=False：关掉 SDK 侧的排队/合并——本项目的 client_serial_bot 用自己的
        KeyedQueue 串行、ma_native_queue_bot 靠方舟原生队列，SDK 若再合并会破坏「每条消息独立成窗」的
        窗口规则。关掉后 SDK 逐条直投，dedup 仍然生效。

    调试 SDK 内部 stale/dedup/policy：设环境变量 `FEISHU_SDK_DEBUG=1`。它做两件事——
      1. 把 SDK 那个独立的 `Lark` logger（core/log.py 里硬编码成 WARNING、自带 handler）
         调到 DEBUG，于是 safety pipeline 的 "stale drop / dedup drop / self-sent drop /
         policy drop / lock contention drop" 这些 DEBUG 行才会打出来（否则被 WARNING 挡掉，
         注意：传给 FeishuChannel 的 log_level 只喂 OpenAPI client，不管这个 logger）；
      2. 注册 SDK 的 `reject` 事件——被丢弃的消息会回调 `RejectEvent(reason=...)`，reason
         取值 stale/duplicate/self_sent/policy/bot_loop/lock_contention，比 DEBUG 文本更结构化，
         用我们自己的 logger 打成 INFO，方便和 gateway 埋点串起来看。
    正常路径（被放行的消息）SDK 不打 DEBUG，只有 drop 才有——想看放行后的流程仍看 gateway 埋点。
    """
    import logging
    import os

    from lark_channel import Events, FeishuChannel, LogLevel, PolicyConfig, SafetyConfig
    from lark_channel.channel.config import ChatQueueConfig

    # SDK 的 `Lark` logger 在 import 时（core/log.py）给自己挂了个 StreamHandler，同时又
    # propagate 到 root——每条 SDK 日志会打两遍（一遍未上色、一遍走我们的彩色 handler）。
    # 这里在 import 之后摘掉它自带的 handler，让它只经 root 输出一次、且带上颜色。
    # 必须放在这里（import 之后）而不是只在 setup_logging：lark_channel 往往在 setup_logging
    # 之后才首次 import，那时它的 handler 才被加上，早删会被重新加回来。
    lark_logger = logging.getLogger("Lark")
    for existing in list(lark_logger.handlers):
        lark_logger.removeHandler(existing)
    lark_logger.propagate = True

    sdk_debug = os.getenv("FEISHU_SDK_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    if sdk_debug:
        # SDK 的 stale/dedup/policy drop 都打在名为 "Lark" 的 logger 上，且它被硬编码为
        # WARNING。只有直接把它调到 DEBUG 才看得到那些行。
        lark_logger.setLevel(logging.DEBUG)

    channel = FeishuChannel(
        app_id=app_id,
        app_secret=app_secret,
        transport="ws",
        log_level=LogLevel.DEBUG if sdk_debug else LogLevel.INFO,
        policy=PolicyConfig(require_mention=False, group_policy="open", dm_policy="open"),
        safety=SafetyConfig(chat_queue=ChatQueueConfig(enabled=False)),
    )

    def _on_message(msg: object) -> None:
        incoming = _inbound_to_incoming(msg)
        if incoming:
            gateway.accept(incoming)

    channel.on(Events.MESSAGE, _on_message)

    if sdk_debug:
        _sdk_log = logging.getLogger("feishu.sdk")

        def _on_reject(event: object) -> None:
            # RejectEvent: message_id / chat_id / sender_id / reason
            _sdk_log.info(
                "[SDK reject] message=%s chat=%s reason=%s",
                getattr(event, "message_id", "?"),
                getattr(event, "chat_id", "?"),
                getattr(event, "reason", "?"),
            )

        channel.on(Events.REJECT, _on_reject)

    channel.start()  # 阻塞：内部起 WS，等价于原 ws_client.start()


# ---- 群历史归一化（移植 src/lark-channel.ts，纯函数便于单测）------------------

def _history_item_to_dict(item: object) -> dict:
    """把 lark-oapi 的 Message 对象拍平成纯 dict，隔离 SDK 类型、便于测试。"""
    sender = getattr(item, "sender", None)
    body = getattr(item, "body", None)
    mentions = getattr(item, "mentions", None) or []
    return {
        "message_id": str(getattr(item, "message_id", "") or ""),
        "msg_type": getattr(item, "msg_type", None),
        "create_time": getattr(item, "create_time", None),
        "deleted": bool(getattr(item, "deleted", False)),
        # parent_id：这条消息引用/回复的上一条消息 id，供引用链逐层上溯（get_message 用）。
        "parent_id": str(getattr(item, "parent_id", "") or ""),
        "sender": {
            "id": getattr(sender, "id", None) if sender else None,
            "sender_type": getattr(sender, "sender_type", None) if sender else None,
            "sender_name": getattr(sender, "sender_name", None) if sender else None,
        },
        "body": {"content": getattr(body, "content", None) if body else None},
        "mentions": [
            {"key": getattr(m, "key", None), "id": getattr(m, "id", None), "name": getattr(m, "name", None)}
            for m in mentions
        ],
    }


def _to_ms(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_eligible_history(item: dict, trigger: "IncomingMessage") -> bool:
    """排除触发消息本身、以及晚于触发时间的消息（避免把并发到达的「未来」消息带入）。"""
    create_time = _to_ms(item.get("create_time"))
    message_id = item.get("message_id")
    return bool(
        message_id
        and message_id != trigger.message_id
        and create_time > 0
        and (not trigger.create_time or create_time <= trigger.create_time)
    )


def normalize_history_item(item: dict, bot_open_id: str) -> Optional["HistoryMessage"]:
    """把一条飞书历史 dict 归一化为 HistoryMessage；无文本且无内容则丢弃。"""
    message_id = str(item.get("message_id") or "")
    if not message_id:
        return None
    deleted = bool(item.get("deleted"))
    mentions = item.get("mentions") or []
    if deleted:
        text = "[该消息已撤回，原内容不应继续作为有效依据]"
    else:
        text = _history_item_text(item.get("msg_type"), (item.get("body") or {}).get("content"), mentions)
    if not text:
        return None
    sender = item.get("sender") or {}
    sender_open_id = str(sender.get("id") or "")
    sender_type = str(sender.get("sender_type") or "unknown")
    at_bot = bool(bot_open_id) and any((m or {}).get("id") == bot_open_id for m in mentions)
    is_from_bot = sender_type == "app" or (bool(bot_open_id) and sender_open_id == bot_open_id)
    return HistoryMessage(
        message_id=message_id,
        sender_open_id=sender_open_id or "unknown",
        sender_name=str(sender.get("sender_name") or ""),
        sender_type=sender_type,
        text=text,
        create_time=_to_ms(item.get("create_time")),
        at_bot=at_bot,
        is_from_bot=is_from_bot,
    )


def _history_item_text(msg_type: Optional[str], content: Optional[str], mentions: list) -> str:
    """抽取历史消息文本：text/post 取正文，file/image 给占位；@ token 换成 @名字。"""
    raw = content or ""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw.strip()[:2000]
    if msg_type == "text":
        text = str((value or {}).get("text") or "") if isinstance(value, dict) else ""
    elif msg_type == "post":
        text = "\n".join(_collect_post_text(value))
    elif msg_type == "file":
        text = f"[文件：{(value or {}).get('file_name') or '未命名文件'}]" if isinstance(value, dict) else "[文件]"
    elif msg_type == "image":
        text = "[图片]"
    elif msg_type:
        text = f"[{msg_type} 消息]"
    else:
        text = ""
    for mention in mentions:
        key = (mention or {}).get("key")
        if key:
            name = (mention or {}).get("name")
            text = text.replace(key, f"@{name}" if name else "")
    return " ".join(text.split())[:2000]


def _collect_post_text(value) -> list[str]:
    """递归收集富文本 post 里的所有 text 节点（对齐 TS collectText）。"""
    if isinstance(value, str):
        return []
    if isinstance(value, list):
        result: list[str] = []
        for child in value:
            result.extend(_collect_post_text(child))
        return result
    if not isinstance(value, dict):
        return []
    own = [value["text"]] if isinstance(value.get("text"), str) else []
    for key, child in value.items():
        if key != "text":
            own.extend(_collect_post_text(child))
    return own
