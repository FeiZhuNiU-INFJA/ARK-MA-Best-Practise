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
import os
import time
from dataclasses import dataclass
from typing import Optional, Protocol

# 引用链最多回溯几层：用户 @bot 那条 → 它引用的 → 再上一层……封顶避免有人恶意/无意
# 串成长链时把回查次数放大（每层一次 im.v1.message.get）。第 1 层是直接被引用的消息。
MAX_QUOTE_DEPTH = 5

# 群成员名册（name → open_id）的缓存有效期（秒）：出站把 Agent 回复里的 @名字 重写成可点击
# <at> 要用它。名册变动不频繁（进退群），60s 内复用同一份，避免每条回复都拉一次成员列表。
CHAT_ROSTER_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class ResourceRef:
    """一条消息里的一个可下载附件（图片 / 文件）的引用。

    对齐 Channel SDK 的 `ResourceDescriptor`（type/file_key/file_name）：只保留下载与挂载
    需要的字段。下载靠 `FeishuSender.download_resource(message_id, file_key, type)`
    走 `GET /im/v1/messages/{message_id}/resources/{file_key}?type=...`；拿到 bytes 后由上层
    上传方舟并挂到 /mnt/session/uploads/。仅收 image/file 两类（sticker/audio/video 不挂载）。

    message_id：附件所属消息的 id——下载资源必须带上它（file_key 只在其所属消息里可取）。
    触发消息的附件填当前消息 id；**历史消息**里的附件填那条历史消息的 id（关键：文件常是
    单独一条消息发的，之后才 @bot「说说这个文件」，得按各自的 message_id 去下载）。空表示
    「用调用方的当前消息 id 兜底」，兼容旧调用。
    """

    file_key: str
    file_name: str
    type: str  # "image" | "file"
    message_id: str = ""


@dataclass(frozen=True)
class IncomingMessage:
    event_id: str
    message_id: str
    chat_id: str
    chat_type: str  # "p2p" | "group"
    thread_id: str
    user_open_id: str
    # 发言人显示名（SDK 已从群名册/联系人解析好，见 normalize/pipeline.py resolve_names）。
    # 拿来在转录里把「当前这条触发消息」的发言人显示成真名而非 open_id，与历史行口径一致；
    # 也让 Agent 回复时能 @ 到人的真名。SDK 没解析出来（私聊/解析失败）时为空，由下游退回 open_id。
    user_name: str
    tenant_key: str
    text: str
    mentioned_bot: bool
    # 租户内跨应用稳定的员工身份。需要飞书自建应用开通“获取用户 user ID”权限；
    # 缺失时，上层为兼容历史消息临时回退到 user_open_id。
    user_id: str = ""
    create_time: int = 0  # 消息创建时间戳（毫秒）；窗口排序必需
    # 这条消息「引用/回复」的那条消息 id（飞书 parent_id，且 parent_id != root_id 时才是
    # 用户显式引用——话题根不算，见 Channel SDK normalize/pipeline.py）。为空表示没引用。
    # 有值时用它沿父链回溯出被引用内容（resolve_quote_chain），注入上下文。
    reply_to_message_id: str = ""
    # 话题根消息 id（飞书 root_id）：话题群里「基于某条消息发起话题」的那条由头消息。
    # 它在主时间线、不在 thread 容器里，故读话题历史时读不到；有值时单独把它（及其之前
    # 几条主时间线消息）作为「话题前情」补进上下文（load_thread_context）。非话题为空。
    root_id: str = ""
    # 这条消息携带的图片/文件附件（多模态）。走「下载→上传方舟→挂载到 /mnt/session/uploads/」
    # 的挂载文件系统方案；空表示纯文本消息。图片消息本身没有正文，text 会是占位/空。
    resources: tuple[ResourceRef, ...] = ()
    # 飞书原始消息类型（text/file/image/post/share_* 等）。单聊只允许 text 主动触发 Agent；
    # 群聊仍允许用户显式 @bot 时携带图片/文件。
    content_type: str = "text"

    @property
    def employee_id(self) -> str:
        """员工持久身份键：优先租户级 user_id，兼容旧事件时回退应用级 open_id。"""
        return self.user_id or self.user_open_id


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
    另外带上这条历史消息里的图片/文件附件（resources）：文件常是**单独一条消息**发的，
    用户之后才在别的消息里 @bot「说说这个文件」。这些附件不在触发消息上，得从落进窗口的
    历史消息里收集出来，一并挂进 Session 供 Agent 读取（见 shared.collect_round_resources）。
    """

    message_id: str
    sender_open_id: str
    sender_name: str
    sender_type: str  # "user" | "app" | "anonymous" | ...
    text: str
    create_time: int
    at_bot: bool = False
    is_from_bot: bool = False
    resources: tuple[ResourceRef, ...] = ()


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
        user_id=sender_id.get("user_id") or "",
        # 原始事件体里不含发言人显示名（需另调联系人接口），此路径为兼容旧调用/测试用，留空由下游兜底。
        user_name="",
        tenant_key=event.get("tenant_key") or "default",
        text=text.strip(),
        mentioned_bot=bool(mentions),
        create_time=create_time,
    )


def markdown_render_enabled() -> bool:
    """出站是否把回复渲染成飞书 post 富文本（默认开）。

    Agent 回复本就是 Markdown；飞书纯文本不渲染，直发会带 `**`/`##` 等符号。默认转 post
    渲染。设 GROUP_BOT_MARKDOWN=0/false/no/off 关闭，退回老的纯文本直发（排障或对端不支持时用）。
    """
    return os.getenv("GROUP_BOT_MARKDOWN", "1").strip().lower() not in ("0", "false", "no", "off")


def _build_roster(pairs: "list[tuple[str, str]]") -> dict[str, str]:
    """把 (显示名, open_id) 列表归一成 `显示名 → open_id` 名册，并对同名做消歧。

    - 翻页/重复项去重：同名同 open_id 只算一个人（按 open_id 去重后计数）；
    - **同名消歧**：一个显示名映射到 2 个及以上不同 open_id（群里真有两个「张三」）时，
      整个名字从名册剔除——出站遇到 `@张三` 找不到唯一目标就原样保留，宁可不 @ 也不 @ 错人。
    纯函数，无 IO；被 chat_roster 在缓存前调用。
    """
    ids_by_name: dict[str, set[str]] = {}
    for name, open_id in pairs:
        clean = (name or "").strip()
        if not clean or not open_id:
            continue
        ids_by_name.setdefault(clean, set()).add(open_id)
    return {name: next(iter(ids)) for name, ids in ids_by_name.items() if len(ids) == 1}


def _split_markdown_blocks(text: str) -> list[str]:
    """按空行把 Markdown 切成若干块，且保持围栏代码块（```）完整（不被内部空行切断）。

    出站混合渲染要「逐块」决定用 native 还是 structured：含 `<at>` 的块走 structured（飞书才能把
    `<at>` 渲成可点击 @），其余块走 native（保留标题/列表/代码块的原生渲染）。围栏代码块整体保留，
    避免被其中的空行拆坏。纯字符串处理，无 IO。
    """
    blocks: list[str] = []
    buf: list[str] = []
    in_fence = False
    for line in (text or "").split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            buf.append(line)
            continue
        if not line.strip() and not in_fence:
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf))
    return blocks


def _text_to_post_content(text: str, roster: "Optional[dict[str, str]]" = None) -> str:
    """把一段 Markdown 文本转成飞书 post 消息的 content（已 JSON 序列化的 locale map）。

    复用 SDK 的 `markdown_to_post_ast`：它把 Markdown 归一成飞书 post AST（`{zh_cn:{title,
    content}}`，正文用 `{tag:"md"}` 节点承载，标题/加粗/列表/代码块等由飞书端渲染）。飞书
    `im.v1.message.create` 对 `msg_type=post` 要求 content 直接是这个 locale map 的 JSON
    （不加外层 `{"post":...}`），与 SDK sender 的口径一致。转换纯字符串处理、无 IO。

    可点击 @（roster 非空时）：先用 SDK 的 `resolve_mentions_in_text` 把正文里的 `@显示名`
    重写成 `<at user_id="ou_...">显示名</at>`（名字要在名册里且唯一，否则原样保留）；重写后
    若正文里出现了 `<at>`，改走**混合渲染**——含 `<at>` 的块用 structured（飞书才能把 `<at>`
    渲成可点击提及），其余块仍用 native md。没有 roster / 没匹配到任何 @ 时，行为与之前完全一致。
    """
    from lark_channel.channel.outbound.markdown import markdown_to_post_ast

    src = text or ""
    if roster:
        from lark_channel.channel.outbound.markdown.resolve_mentions import (
            resolve_mentions_in_text,
        )

        src = resolve_mentions_in_text(src, roster.get)

    if roster and "<at" in src:
        content: list = []
        for block in _split_markdown_blocks(src):
            mode = "structured" if "<at" in block else "native"
            ast = markdown_to_post_ast(block, tag_md_mode=mode)
            content.extend(ast["zh_cn"]["content"])
        post = {"zh_cn": {"title": "", "content": content or [[{"tag": "text", "text": ""}]]}}
    else:
        post = markdown_to_post_ast(src)
    return json.dumps(post, ensure_ascii=False)


class FeishuSender:
    """基于 lark-channel-sdk 自带 OpenAPI Client 的消息发送器（reply / send / react / 读历史）。

    SDK 的 `lark_channel.Client` 与 lark-oapi 的 `Client` 接口一一对应（同步阻塞、
    builder 风格、response.success()），因此这里的实现与迁移前几乎一致，只是导入路径
    从 `lark_oapi` 换成 `lark_channel`。同步调用，下游用 run_in_executor 丢线程池。

    出站富文本：Agent 的回复是 Markdown（`## 标题` / `**加粗**` / 列表 / 代码块），飞书
    **纯文本消息不渲染 Markdown**，直发会带着符号原样显示。故 reply / send_to_chat 默认把
    正文经 `_text_to_post_content` 转成飞书 **post 富文本**（`msg_type=post`）再发；转换或发送
    失败则自动降级回纯文本，保证「宁可不渲染也要发出去」。开关见 `markdown_render_enabled`。
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
        # chat_id → (到期时间戳, {显示名: open_id}) 的名册缓存。出站 @名字 重写用；见 chat_roster。
        self._roster_cache: dict[str, tuple[float, dict[str, str]]] = {}

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

    def list_chat_members(self, chat_id: str) -> list[tuple[str, str]]:
        """拉取一个群的全部成员，返回 (显示名, open_id) 列表（翻页拉全）。

        走原生 `GET /open-apis/im/v1/chats/:chat_id/members?member_id_type=open_id`
        （SDK 无对应 typed model，用 BaseRequest 直发，与 bot_open_id 同款）。每页最多 100，
        `has_more`/`page_token` 翻页。成员 `member_id_type=user` 时 `member_id` 即 open_id；
        非 open_id 的成员（机器人等）跳过——名册只服务「把回复里 @人名 变成可点击 <at>」。
        纯 IO，同步调用，下游用 run_in_executor 丢线程池；失败抛异常由上层降级为不 @。
        """
        from lark_channel import AccessTokenType, BaseRequest, HttpMethod

        pairs: list[tuple[str, str]] = []
        page_token: Optional[str] = None
        for _ in range(50):  # 封顶 50 页（5000 人）防异常分页把请求放大
            queries: list[tuple[str, str]] = [("member_id_type", "open_id"), ("page_size", "100")]
            if page_token:
                queries.append(("page_token", page_token))
            request = (
                BaseRequest.builder()
                .http_method(HttpMethod.GET)
                .uri("/open-apis/im/v1/chats/:chat_id/members")
                .paths({"chat_id": chat_id})
                .queries(queries)
                .token_types({AccessTokenType.TENANT})
                .build()
            )
            response = self._client.request(request)
            if not response.success():
                raise RuntimeError(f"读取群成员失败 {response.code}: {response.msg}")
            raw = response.raw.content if response.raw else None
            try:
                payload = json.loads(raw) if raw else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            data = payload.get("data") if isinstance(payload, dict) else None
            data = data if isinstance(data, dict) else {}
            for item in data.get("items") or []:
                if not isinstance(item, dict):
                    continue
                open_id = str(item.get("member_id") or "")
                name = str(item.get("name") or "")
                if open_id and name:
                    pairs.append((name, open_id))
            page_token = str(data.get("page_token") or "")
            if not data.get("has_more") or not page_token:
                break
        return pairs

    def chat_roster(self, chat_id: str) -> dict[str, str]:
        """群成员名册 `显示名 → open_id`（带 TTL 缓存 + 同名消歧）。

        出站渲染用它把 Agent 回复里的 `@张三` 重写成可点击 `<at user_id="ou_...">张三</at>`。
        缓存 CHAT_ROSTER_TTL_SECONDS 秒，避免每条回复都拉一次成员列表。**同名消歧**：若群里有
        两个人重名，该名字整个从名册剔除（lookup 返回 None）——宁可不 @，也不 @ 错人。
        拉取失败时返回空名册（不缓存），出站退回把 @名字 原样保留。
        """
        now = time.monotonic()
        cached = self._roster_cache.get(chat_id)
        if cached is not None and cached[0] > now:
            return cached[1]
        try:
            pairs = self.list_chat_members(chat_id)
        except Exception:  # noqa: BLE001 - 名册拉取失败不该拖垮回复，退回不 @
            return {}
        roster = _build_roster(pairs)
        self._roster_cache[chat_id] = (now + CHAT_ROSTER_TTL_SECONDS, roster)
        return roster

    def reply(self, message_id: str, text: str, roster: "Optional[dict[str, str]]" = None) -> None:
        """回复某条消息。默认转 post 富文本渲染 Markdown；转换或发送失败则降级为纯文本再发一次。

        roster（群成员名册 名字→open_id）非空时，正文里的 `@显示名` 会被重写为可点击 `<at>`
        提及（见 `_text_to_post_content`）；私聊/无名册时不重写。降级纯文本时把重写后的正文原样发，
        让 `<at>` 至少以文字形式保留发言意图。"""
        if markdown_render_enabled():
            try:
                self._reply_with(message_id, "post", _text_to_post_content(text, roster))
                return
            except Exception:  # noqa: BLE001 - post 渲染/发送失败不该让回复彻底丢，降级纯文本重试
                pass
        self._reply_with(message_id, "text", json.dumps({"text": text}, ensure_ascii=False))

    def _reply_with(self, message_id: str, msg_type: str, content: str) -> None:
        from lark_channel.api.im.v1.model.reply_message_request import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        body = (
            ReplyMessageRequestBody.builder()
            .content(content)
            .msg_type(msg_type)
            .build()
        )
        request = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = self._client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(f"飞书回复失败 {response.code}: {response.msg}")

    def reply_in_thread(
        self, message_id: str, text: str, roster: "Optional[dict[str, str]]" = None
    ) -> Optional[str]:
        """在话题内回复并返回 thread_id；若目标在主时间线则由本次回复创建话题。"""
        if markdown_render_enabled():
            try:
                return self._reply_in_thread_with(
                    message_id, "post", _text_to_post_content(text, roster)
                )
            except Exception:  # noqa: BLE001 - 富文本失败时仍需在同一话题内降级发送
                pass
        return self._reply_in_thread_with(
            message_id, "text", json.dumps({"text": text}, ensure_ascii=False)
        )

    def _reply_in_thread_with(
        self, message_id: str, msg_type: str, content: str
    ) -> Optional[str]:
        from lark_channel.api.im.v1.model.reply_message_request import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        body = (
            ReplyMessageRequestBody.builder()
            .content(content)
            .msg_type(msg_type)
            .reply_in_thread(True)
            .build()
        )
        request = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = self._client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(f"飞书话题回复失败 {response.code}: {response.msg}")
        return str(getattr(response.data, "thread_id", "") or "") or None

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

    def send_to_chat(self, chat_id: str, text: str, roster: "Optional[dict[str, str]]" = None) -> None:
        """往群里主动发一条消息。默认转 post 富文本渲染 Markdown；转换或发送失败则降级纯文本重试。

        roster 非空时把正文里的 `@显示名` 重写为可点击 `<at>` 提及（见 `_text_to_post_content`）。"""
        if markdown_render_enabled():
            try:
                self._create_in_chat(chat_id, "post", _text_to_post_content(text, roster))
                return
            except Exception:  # noqa: BLE001 - post 渲染/发送失败降级纯文本，别让消息彻底发不出去
                pass
        self._create_in_chat(chat_id, "text", json.dumps({"text": text}, ensure_ascii=False))

    def send_authorization_card(
        self, chat_id: str, url: str, domain: str = "calendar"
    ) -> None:
        """在单聊中发送按业务域区分的用户只读授权卡片。"""
        is_drive = domain == "drive"
        title = "授权搜索你的文档" if is_drive else "授权查看你的日程"
        detail = (
            "为了搜索你最近创建或编辑的文档，需要你授权当前飞书账号。"
            "文档创建、修改或删除仍使用数字员工的 Bot 身份。"
            if is_drive
            else "为了读取你的个人日历和忙闲信息，需要你授权当前飞书账号。"
            "创建或修改日程仍使用数字员工的 Bot 身份。"
        )
        button_text = "授权搜索文档" if is_drive else "授权查看日程"
        card = {
            "schema": "2.0",
            "config": {"width_mode": "default"},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "subtitle": {
                    "tag": "plain_text",
                    "content": "仅用于当前数字员工协作",
                },
                "template": "blue",
                "icon": {
                    "tag": "standard_icon",
                    "token": "search_outlined" if is_drive else "calendar_outlined",
                },
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": detail,
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": button_text},
                        "type": "primary_filled",
                        "width": "fill",
                        "behaviors": [{"type": "open_url", "default_url": url}],
                    },
                ]
            },
        }
        self._create_in_chat(
            chat_id, "interactive", json.dumps(card, ensure_ascii=False)
        )

    def _create_in_chat(self, chat_id: str, msg_type: str, content: str) -> None:
        self._create_in_chat_raw(chat_id, msg_type, content)

    def _create_in_chat_raw(self, chat_id: str, msg_type: str, content: str) -> Optional[str]:
        """底层发送:返回创建成功后的 message_id。上层如果只需要发出去可以忽略。"""
        from lark_channel.api.im.v1.model.create_message_request import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type(msg_type)
            .content(content)
            .build()
        )
        request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        response = self._client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"飞书发送失败 {response.code}: {response.msg}")
        data = getattr(response, "data", None)
        return getattr(data, "message_id", None) if data is not None else None

    def send_interactive_card(self, chat_id: str, card: dict) -> Optional[str]:
        """在群/单聊中发一张飞书交互卡片(schema 2.0 或旧 v1 dict),返回 message_id。

        topic6 HITL 会用返回的 message_id 回写到 pipeline_hc_events,后续卡片按钮点击回调
        时可以按 message_id 反查究竟对应哪一次 HC 卡点。
        """
        return self._create_in_chat_raw(
            chat_id, "interactive", json.dumps(card, ensure_ascii=False)
        )

    def patch_interactive_card(self, message_id: str, card: dict) -> None:
        """就地覆写一条已发出的 interactive 卡片(topic6 进度卡片用)。

        飞书 `PATCH /open-apis/im/v1/messages/{message_id}` 只允许修改卡片(interactive),
        不能改文本。这里假定 message_id 对应的原消息就是 interactive 卡片;不满足会 400。
        """
        from lark_channel.api.im.v1.model.patch_message_request import (
            PatchMessageRequest,
            PatchMessageRequestBody,
        )

        body = (
            PatchMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        request = (
            PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
        )
        response = self._client.im.v1.message.patch(request)
        if not response.success():
            raise RuntimeError(f"飞书 patch 卡片失败 {response.code}: {response.msg}")

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

    def download_resource(self, message_id: str, file_key: str, resource_type: str) -> bytes:
        """下载一条消息里的图片/文件附件，返回原始 bytes（挂载文件系统方案的第一步）。

        走 `GET /im/v1/messages/{message_id}/resources/{file_key}?type=...`
        （SDK 的 im.v1.message_resource.get）——附件 file_key 属于某条消息，必须带上
        message_id 才能取到二进制；type 取 image/file，与 ResourceRef.type 一致。
        拿到的 bytes 由上层交给 ArkClient.upload_file → add_session_file，挂到
        /mnt/session/uploads/。同步调用，下游用 run_in_executor 丢线程池；读失败抛异常
        由上层降级为文字占位。
        """
        from lark_channel.api.im.v1.model.get_message_resource_request import (
            GetMessageResourceRequest,
        )

        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type(resource_type)
            .build()
        )
        response = self._client.im.v1.message_resource.get(request)
        if not response.success():
            raise RuntimeError(f"下载附件失败 {response.code}: {response.msg}")
        file_obj = getattr(response, "file", None)
        if file_obj is None:
            raise RuntimeError("下载附件成功，但响应中没有文件内容")
        if hasattr(file_obj, "read"):
            return file_obj.read()
        if isinstance(file_obj, (bytes, bytearray)):
            return bytes(file_obj)
        raise RuntimeError("下载附件成功，但文件内容类型无法识别")

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


def _extract_resources(msg: object) -> tuple[ResourceRef, ...]:
    """从 SDK 的 `InboundMessage.resources` 里挑出可挂载的图片/文件，映射为 ResourceRef。

    SDK 已把各消息类型（image/file/post…）里的媒体归一化成 ResourceDescriptor
    （type/file_key/file_name），这里只收 image 与 file 两类——sticker/audio/video
    不走挂载文件系统方案。图片没有原文件名，用 `{file_key}.jpg` 兜一个可读名。
    message_id 记到每个 ResourceRef 上，供下载时定位所属消息（触发消息=当前 id）。
    """
    descriptors = getattr(msg, "resources", None) or []
    message_id = getattr(msg, "id", "") or ""
    refs: list[ResourceRef] = []
    for desc in descriptors:
        res_type = getattr(desc, "type", "")
        file_key = getattr(desc, "file_key", "") or ""
        if not file_key or res_type not in ("image", "file"):
            continue
        file_name = getattr(desc, "file_name", None) or (
            f"{file_key}.jpg" if res_type == "image" else file_key
        )
        refs.append(ResourceRef(
            file_key=file_key, file_name=file_name, type=res_type, message_id=message_id
        ))
    return tuple(refs)


def _inbound_to_incoming(msg: object) -> Optional[IncomingMessage]:
    """把 Channel SDK 的 `InboundMessage` 映射回本项目的 `IncomingMessage`。

    处理文本与带图片/文件附件的消息。SDK 的 `content_text` **保留了渲染后的 @名字**（含对
    bot 自己的提及，见 lark_channel normalize/pipeline.py：“content_text itself keeps the
    rendered mention”）——所以当前触发消息里 `@小助手` 仍在，转录中「谁 @ 了谁」可见，与历史行
    口径一致。（SDK 另有剥掉 bot 提及的 body_text 视图，本项目不用它。）Channel SDK 未把
    事件 header 的 tenant_key 透出，因此这里固定使用 default。不能从 mentions 猜 tenant_key：
    带 @ 的消息有 mention、不带 @ 的话题续聊没有，会导致同一话题得到两把不同的会话键。
    chat_id 本身已能稳定隔离会话。

    多模态：raw_content_type 为 image/file/post 时，从 SDK 的 resources 抽出图片/文件附件
    （_extract_resources），交由上层「下载→上传方舟→挂载到 /mnt/session/uploads/」。既非文本
    也无可挂载附件的消息（sticker/audio/video 等）返回 None，维持原「只处理文本」的下游契约。
    """
    content_type = str(getattr(msg, "raw_content_type", None) or "")
    resources = _extract_resources(msg)
    if content_type != "text" and not resources:
        return None
    conversation = getattr(msg, "conversation", None)
    sender = getattr(msg, "sender", None)
    text = (getattr(msg, "content_text", "") or "").strip()
    # 图片/文件消息的 content_text 是 SDK 的媒体占位（`![image](key)` / `<file .../>`），
    # 对转录无意义且会混淆模型——附件已由 resources 单独承载、挂到文件系统，故清掉占位文本。
    if resources and content_type in ("image", "file"):
        text = ""
    chat_type = "p2p" if getattr(conversation, "chat_type", "") == "p2p" else "group"
    tenant_key = "default"
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
        user_id=getattr(sender, "user_id", "") or "",
        # SDK 已在归一化时把发言人显示名解析进来（InboundMessage.sender_name = sender.display_name，
        # 见 lark_channel normalize/pipeline.py）。取来让当前触发行显示真名、并供回复 @ 到人。
        user_name=str(getattr(msg, "sender_name", "") or getattr(sender, "display_name", "") or ""),
        tenant_key=tenant_key,
        text=text,
        mentioned_bot=bool(getattr(msg, "mentioned_bot", False)),
        create_time=int(getattr(msg, "create_time", 0) or 0),
        reply_to_message_id=reply_to or "",
        root_id=root_id,
        resources=resources,
        content_type=content_type or "unknown",
    )


def start_feishu_gateway(app_id: str, app_secret: str, gateway: GatewayLike) -> None:
    """用 lark-channel-sdk 启动 WS 长连接，阻塞运行。收到消息映射后交给 gateway.accept。

    SDK 已经把「长连接 + 断线重连 + 归一化 + 去重」都做了，等价于原来手写的
    EventDispatcherHandler + normalize_feishu_message + claim_event 那一套。这里的策略：
      - transport=ws：与原实现一致，无需公网回调（另一个可选值是 webhook）。
      - policy.require_mention=False + group_policy=open：**不让 SDK 层拦**，是否处理仍由
        下游 gateway 的 should_handle 判定（群里只在 @bot 时处理），保持与迁移前行为一致。
      - safety.chat_queue.enabled=False：关掉 SDK 侧的排队/合并——digital_employee 的 serial
        模式用自己的 KeyedQueue，native-queue 模式靠方舟原生队列；SDK 若再合并会破坏
        「每条消息独立成窗」的规则。关掉后 SDK 逐条直投，dedup 仍然生效。

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

    # topic6 HITL：Gateway 若实现了 on_card_action(action) 就注册 CARD_ACTION 回调。
    # 这是可选钩子——digital-employee 单场景不实现该方法时,SDK 不订阅卡片按钮事件,
    # 与原行为完全一致。
    if hasattr(gateway, "on_card_action"):
        card_handler = gateway.on_card_action  # type: ignore[attr-defined]

        def _on_card_action(action: object) -> None:
            card_handler(action)

        channel.on(Events.CARD_ACTION, _on_card_action)

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
        resources: tuple[ResourceRef, ...] = ()
    else:
        content = (item.get("body") or {}).get("content")
        text = _history_item_text(item.get("msg_type"), content, mentions)
        resources = _extract_history_resources(item.get("msg_type"), content, message_id)
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
        resources=resources,
    )


def _extract_history_resources(
    msg_type: Optional[str], content: Optional[str], message_id: str
) -> tuple[ResourceRef, ...]:
    """从一条历史消息的 body.content 里抽出图片/文件附件，映射为 ResourceRef（带 message_id）。

    与入站 `_extract_resources` 对应，但历史读到的是 raw content JSON 而非 SDK 归一化对象：
      - file 消息：`{"file_key": ..., "file_name": ...}` → ResourceRef(type="file")。
      - image 消息：`{"image_key": ...}` → ResourceRef(type="image")，无原名用 `{key}.jpg` 兜底。
    其余类型（text/post/audio/video/sticker…）不产附件。message_id 是这条历史消息自己的 id
    ——下载资源必须按各自所属消息取（file_key 只在其所属消息里有效）。
    """
    raw = content or ""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(value, dict):
        return ()
    if msg_type == "file":
        file_key = str(value.get("file_key") or "")
        if not file_key:
            return ()
        file_name = str(value.get("file_name") or "") or file_key
        return (ResourceRef(file_key=file_key, file_name=file_name, type="file", message_id=message_id),)
    if msg_type == "image":
        image_key = str(value.get("image_key") or "")
        if not image_key:
            return ()
        return (ResourceRef(
            file_key=image_key, file_name=f"{image_key}.jpg", type="image", message_id=message_id
        ),)
    return ()


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
