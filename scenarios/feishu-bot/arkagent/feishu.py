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
    for mention in mentions:
        key = mention.get("key")
        if key:
            text = text.replace(key, "")
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


def _inbound_to_incoming(msg: object) -> Optional[IncomingMessage]:
    """把 Channel SDK 的 `InboundMessage` 映射回本项目的 `IncomingMessage`。

    只处理文本；SDK 已把 @提及 从正文里剥掉并归一化，`content_text` 就是纯净文本，
    等价于原 normalize_feishu_message 去掉 mention token 后的结果。tenant_key SDK 未在
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
