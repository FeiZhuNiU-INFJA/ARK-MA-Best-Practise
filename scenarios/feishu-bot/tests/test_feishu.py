import json
from types import SimpleNamespace

import pytest

from arkagent.feishu import (
    ResourceRef,
    _build_roster,
    _extract_resources,
    _extract_history_resources,
    _inbound_to_incoming,
    _quoted_from_item,
    _split_markdown_blocks,
    _text_to_post_content,
    markdown_render_enabled,
    normalize_feishu_message,
    normalize_history_item,
    resolve_quote_chain,
)


def _inbound(**overrides) -> SimpleNamespace:
    """构造一个鸭子类型的 Channel SDK InboundMessage（只填 _inbound_to_incoming 读的字段）。"""
    base = dict(
        id="om-in-1",
        raw_content_type="text",
        content_text="@小助手 帮我总结",
        create_time=1700000009999,
        mentioned_bot=True,
        conversation=SimpleNamespace(chat_id="oc-1", chat_type="group", thread_id="th-1"),
        sender=SimpleNamespace(open_id="ou-user"),
        mentions=[SimpleNamespace(open_id="ou-bot", tenant_key="tenant-1", is_bot=True)],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _history_item(**overrides) -> dict:
    base = {
        "message_id": "om-h1",
        "msg_type": "text",
        "create_time": "1700000000000",
        "deleted": False,
        "sender": {"id": "ou-alice", "sender_type": "user", "sender_name": "Alice"},
        "body": {"content": json.dumps({"text": "hello"})},
        "mentions": [],
    }
    base.update(overrides)
    return base


def test_normalize_extracts_text_and_removes_mention_tokens():
    result = normalize_feishu_message({
        "event_id": "evt-1",
        "tenant_key": "tenant-1",
        "sender": {"sender_id": {"open_id": "ou-user"}},
        "message": {
            "message_id": "om-1",
            "chat_id": "oc-1",
            "chat_type": "group",
            "message_type": "text",
            "create_time": "1700000001234",
            "content": json.dumps({"text": "@_user_1 帮我总结"}),
            "mentions": [{"key": "@_user_1", "id": {"open_id": "ou-bot"}}],
        },
    })
    assert result is not None
    assert result.text == "帮我总结"  # mention 无 name，token 删掉
    assert result.mentioned_bot is True
    assert result.user_open_id == "ou-user"
    assert result.chat_type == "group"
    assert result.create_time == 1700000001234


def test_normalize_keeps_mention_name_when_present():
    # mention 带 name 时，@token 换成可读的 @名字（含对 bot 的提及），转录里保留「@谁」。
    result = normalize_feishu_message({
        "event_id": "evt-2",
        "sender": {"sender_id": {"open_id": "ou-user"}},
        "message": {
            "message_id": "om-2",
            "chat_id": "oc-1",
            "chat_type": "group",
            "message_type": "text",
            "content": json.dumps({"text": "@_user_1 帮我总结"}),
            "mentions": [{"key": "@_user_1", "id": {"open_id": "ou-bot"}, "name": "小助手"}],
        },
    })
    assert result is not None
    assert result.text == "@小助手 帮我总结"


def test_normalize_ignores_non_text_messages():
    assert normalize_feishu_message({
        "message": {"message_id": "om-1", "chat_id": "oc-1", "chat_type": "p2p", "message_type": "image"}
    }) is None


def test_normalize_defaults_thread_and_tenant():
    result = normalize_feishu_message({
        "sender": {"sender_id": {"open_id": "ou-user"}},
        "message": {
            "message_id": "om-2",
            "chat_id": "oc-2",
            "chat_type": "p2p",
            "message_type": "text",
            "root_id": "root-1",
            "content": json.dumps({"text": "你好"}),
        },
    })
    assert result is not None
    assert result.event_id == "om-2"  # 无 event_id 回退到 message_id
    assert result.thread_id == "root-1"
    assert result.tenant_key == "default"
    assert result.mentioned_bot is False


def test_history_item_detects_at_bot_and_replaces_mention_name():
    item = normalize_history_item(
        _history_item(
            body={"content": json.dumps({"text": "@_user_1 在吗"})},
            mentions=[{"key": "@_user_1", "id": "ou-bot", "name": "小助手"}],
        ),
        bot_open_id="ou-bot",
    )
    assert item is not None
    assert item.text == "@小助手 在吗"
    assert item.at_bot is True
    assert item.is_from_bot is False
    assert item.sender_open_id == "ou-alice"


def test_history_item_marks_bot_own_reply_by_sender_type():
    item = normalize_history_item(
        _history_item(
            sender={"id": "ou-bot", "sender_type": "app", "sender_name": "小助手"},
            body={"content": json.dumps({"text": "已完成"})},
        ),
        bot_open_id="ou-bot",
    )
    assert item is not None
    assert item.is_from_bot is True
    assert item.at_bot is False


def test_history_item_at_bot_false_for_other_mention():
    item = normalize_history_item(
        _history_item(
            body={"content": json.dumps({"text": "@_user_1 你看"})},
            mentions=[{"key": "@_user_1", "id": "ou-carol", "name": "Carol"}],
        ),
        bot_open_id="ou-bot",
    )
    assert item is not None
    assert item.at_bot is False


def test_history_item_deleted_message_becomes_placeholder():
    item = normalize_history_item(_history_item(deleted=True, body={"content": None}), bot_open_id="ou-bot")
    assert item is not None
    assert "撤回" in item.text


def test_history_item_post_collects_nested_text():
    content = json.dumps({"title": "t", "content": [[{"tag": "text", "text": "第一段"}, {"tag": "text", "text": "第二段"}]]})
    item = normalize_history_item(_history_item(msg_type="post", body={"content": content}), bot_open_id="ou-bot")
    assert item is not None
    assert "第一段" in item.text and "第二段" in item.text


# ---- 历史消息附件抽取 _extract_history_resources / HistoryMessage.resources ----

def test_extract_history_resources_file_message():
    content = json.dumps({"file_key": "fk-pdf", "file_name": "report.pdf"})
    refs = _extract_history_resources("file", content, "om-h9")
    assert refs == (
        ResourceRef(file_key="fk-pdf", file_name="report.pdf", type="file", message_id="om-h9"),
    )


def test_extract_history_resources_file_without_name_falls_back_to_key():
    content = json.dumps({"file_key": "fk-noname"})
    refs = _extract_history_resources("file", content, "om-h9")
    assert refs == (
        ResourceRef(file_key="fk-noname", file_name="fk-noname", type="file", message_id="om-h9"),
    )


def test_extract_history_resources_image_message():
    content = json.dumps({"image_key": "img-9"})
    refs = _extract_history_resources("image", content, "om-h9")
    assert refs == (
        ResourceRef(file_key="img-9", file_name="img-9.jpg", type="image", message_id="om-h9"),
    )


def test_extract_history_resources_skips_text_and_unkeyed_and_bad_json():
    assert _extract_history_resources("text", json.dumps({"text": "hi"}), "om-h9") == ()
    assert _extract_history_resources("file", json.dumps({}), "om-h9") == ()       # 无 file_key
    assert _extract_history_resources("image", json.dumps({}), "om-h9") == ()      # 无 image_key
    assert _extract_history_resources("file", "not-json", "om-h9") == ()           # 坏 JSON
    assert _extract_history_resources("file", None, "om-h9") == ()                 # 空 content


def test_history_item_file_message_carries_resource_with_own_message_id():
    content = json.dumps({"file_key": "fk-pdf", "file_name": "office-requirements.pdf"})
    item = normalize_history_item(
        _history_item(message_id="om-file", msg_type="file", body={"content": content}),
        bot_open_id="ou-bot",
    )
    assert item is not None
    assert item.resources == (
        ResourceRef(
            file_key="fk-pdf", file_name="office-requirements.pdf", type="file", message_id="om-file"
        ),
    )


def test_history_item_text_message_has_no_resources():
    item = normalize_history_item(_history_item(), bot_open_id="ou-bot")
    assert item is not None
    assert item.resources == ()


def test_history_item_deleted_message_drops_resources():
    # 撤回消息只留占位文本，不该再带附件（file_key 已失效）。
    item = normalize_history_item(
        _history_item(msg_type="file", deleted=True, body={"content": None}),
        bot_open_id="ou-bot",
    )
    assert item is not None
    assert item.resources == ()


def test_inbound_maps_channel_message_to_incoming():
    result = _inbound_to_incoming(_inbound())
    assert result is not None
    assert result.message_id == "om-in-1"
    assert result.event_id == "om-in-1"
    assert result.chat_id == "oc-1"
    assert result.chat_type == "group"
    assert result.thread_id == "th-1"
    assert result.user_open_id == "ou-user"
    assert result.text == "@小助手 帮我总结"  # SDK 已剥离 mention token，content_text 直接用
    assert result.mentioned_bot is True
    assert result.tenant_key == "tenant-1"  # 从 mentions 兜底取到
    assert result.create_time == 1700000009999


def test_inbound_ignores_non_text():
    assert _inbound_to_incoming(_inbound(raw_content_type="image")) is None


def test_inbound_defaults_tenant_when_missing():
    result = _inbound_to_incoming(_inbound(mentions=[], mentioned_bot=False))
    assert result is not None
    assert result.tenant_key == "default"
    assert result.mentioned_bot is False


def test_inbound_maps_reply_to_message_id_from_reply():
    # SDK 把用户显式引用归一化到 msg.reply；_inbound_to_incoming 应取出其 message_id。
    result = _inbound_to_incoming(
        _inbound(reply=SimpleNamespace(message_id="om-quoted-1"))
    )
    assert result is not None
    assert result.reply_to_message_id == "om-quoted-1"


def test_inbound_reply_to_empty_when_no_reply():
    # 没有引用（reply 为 None、无便捷属性）时应为空串，resolve_quote_chain 直接返回 []。
    result = _inbound_to_incoming(_inbound())
    assert result is not None
    assert result.reply_to_message_id == ""


# ---- 引用链回溯 resolve_quote_chain / _quoted_from_item -----------------------

def _quote_item(mid: str, *, parent_id: str = "", text: str = "", **overrides) -> dict:
    base = {
        "message_id": mid,
        "msg_type": "text",
        "create_time": "1700000000000",
        "deleted": False,
        "parent_id": parent_id,
        "sender": {"id": f"ou-{mid}", "sender_type": "user", "sender_name": mid},
        "body": {"content": json.dumps({"text": text or mid})},
        "mentions": [],
    }
    base.update(overrides)
    return base


def test_resolve_quote_chain_empty_when_no_parent():
    assert resolve_quote_chain("", lambda _mid: None) == []


def test_resolve_quote_chain_single_direct_quote():
    store = {"om-q1": _quote_item("om-q1", text="被引用的话")}
    chain = resolve_quote_chain("om-q1", store.get)
    assert [(q.message_id, q.depth, q.text) for q in chain] == [("om-q1", 1, "被引用的话")]


def test_resolve_quote_chain_follows_parent_ids_with_depth():
    # om-q1 引用 om-q2，om-q2 引用 om-q3：depth 依次 1/2/3。
    store = {
        "om-q1": _quote_item("om-q1", parent_id="om-q2", text="第一层"),
        "om-q2": _quote_item("om-q2", parent_id="om-q3", text="第二层"),
        "om-q3": _quote_item("om-q3", text="第三层"),
    }
    chain = resolve_quote_chain("om-q1", store.get)
    assert [(q.message_id, q.depth) for q in chain] == [("om-q1", 1), ("om-q2", 2), ("om-q3", 3)]


def test_resolve_quote_chain_truncates_at_max_depth():
    # 造一条 8 层长链，默认封顶 MAX_QUOTE_DEPTH=5。
    store = {
        f"om-q{i}": _quote_item(f"om-q{i}", parent_id=f"om-q{i + 1}")
        for i in range(1, 9)
    }
    chain = resolve_quote_chain("om-q1", store.get)
    assert len(chain) == 5
    assert [q.depth for q in chain] == [1, 2, 3, 4, 5]


def test_resolve_quote_chain_respects_custom_max_depth():
    store = {
        "om-q1": _quote_item("om-q1", parent_id="om-q2"),
        "om-q2": _quote_item("om-q2", parent_id="om-q3"),
        "om-q3": _quote_item("om-q3"),
    }
    chain = resolve_quote_chain("om-q1", store.get, max_depth=2)
    assert [q.message_id for q in chain] == ["om-q1", "om-q2"]


def test_resolve_quote_chain_breaks_on_cycle():
    # A 引 B、B 又引 A：seen 集合应在回到 A 时截断，不无限循环。
    store = {
        "om-a": _quote_item("om-a", parent_id="om-b"),
        "om-b": _quote_item("om-b", parent_id="om-a"),
    }
    chain = resolve_quote_chain("om-a", store.get)
    assert [q.message_id for q in chain] == ["om-a", "om-b"]


def test_resolve_quote_chain_stops_when_fetch_returns_none():
    # 中途某条读不到（撤回/无权限）：停在能读到的部分。
    store = {"om-q1": _quote_item("om-q1", parent_id="om-missing")}
    chain = resolve_quote_chain("om-q1", store.get)
    assert [q.message_id for q in chain] == ["om-q1"]


def test_quoted_from_item_deleted_becomes_placeholder():
    quoted = _quoted_from_item(_quote_item("om-q1", deleted=True, body={"content": None}), depth=1)
    assert quoted is not None
    assert "撤回" in quoted.text


def test_quoted_from_item_image_placeholder():
    quoted = _quoted_from_item(
        _quote_item("om-q1", msg_type="image", body={"content": json.dumps({"image_key": "img-1"})}),
        depth=2,
    )
    assert quoted is not None
    assert quoted.text == "[图片]"
    assert quoted.depth == 2


# ---- 附件抽取 _extract_resources / 带附件消息映射 -----------------------------

def _descriptor(**overrides) -> SimpleNamespace:
    base = dict(type="file", file_key="fk-1", file_name="doc.pdf")
    base.update(overrides)
    return SimpleNamespace(**base)


def test_extract_resources_collects_image_and_file():
    msg = SimpleNamespace(resources=[
        _descriptor(type="image", file_key="img-1", file_name=None),
        _descriptor(type="file", file_key="fk-2", file_name="报告.pdf"),
    ])
    refs = _extract_resources(msg)
    assert refs == (
        ResourceRef(file_key="img-1", file_name="img-1.jpg", type="image"),  # 图片无名 → 兜底 .jpg
        ResourceRef(file_key="fk-2", file_name="报告.pdf", type="file"),
    )


def test_extract_resources_skips_unsupported_and_keyless():
    msg = SimpleNamespace(resources=[
        _descriptor(type="audio", file_key="au-1"),   # 非 image/file，跳过
        _descriptor(type="video", file_key="vd-1"),   # 跳过
        _descriptor(type="file", file_key=""),         # 无 file_key，跳过
        _descriptor(type="image", file_key="img-ok", file_name=None),
    ])
    refs = _extract_resources(msg)
    assert [r.file_key for r in refs] == ["img-ok"]


def test_extract_resources_empty_when_no_resources():
    assert _extract_resources(SimpleNamespace()) == ()
    assert _extract_resources(SimpleNamespace(resources=None)) == ()


def test_inbound_maps_image_message_with_resources_and_clears_placeholder():
    # 图片消息：raw_content_type=image，content_text 是媒体占位，应被清空；resources 抽出附件。
    msg = _inbound(
        raw_content_type="image",
        content_text="![image](img-1)",
        resources=[_descriptor(type="image", file_key="img-1", file_name=None)],
    )
    result = _inbound_to_incoming(msg)
    assert result is not None
    assert result.text == ""  # 媒体占位被清掉
    assert result.resources == (
        ResourceRef(file_key="img-1", file_name="img-1.jpg", type="image", message_id="om-in-1"),
    )


def test_inbound_keeps_text_and_resources_for_file_message():
    # 文件消息带 caption 之外，raw_content_type=file 也应清占位、保留 resources。
    msg = _inbound(
        raw_content_type="file",
        content_text="<file key=fk-1/>",
        resources=[_descriptor(type="file", file_key="fk-1", file_name="doc.pdf")],
    )
    result = _inbound_to_incoming(msg)
    assert result is not None
    assert result.text == ""
    assert result.resources[0].file_name == "doc.pdf"
    assert result.resources[0].message_id == "om-in-1"  # 触发消息的附件带当前消息 id


def test_inbound_text_message_has_no_resources():
    result = _inbound_to_incoming(_inbound())
    assert result is not None
    assert result.resources == ()


# ---- 出站富文本渲染（Markdown → 飞书 post）--------------------------------

def test_markdown_render_enabled_default_on(monkeypatch):
    monkeypatch.delenv("GROUP_BOT_MARKDOWN", raising=False)
    assert markdown_render_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", "False"])
def test_markdown_render_disabled_by_env(monkeypatch, value):
    monkeypatch.setenv("GROUP_BOT_MARKDOWN", value)
    assert markdown_render_enabled() is False


def test_text_to_post_content_is_unwrapped_locale_map():
    # 飞书 post 的 content 应是 {zh_cn:{title,content}} 直接序列化，不带外层 {"post":...}。
    content = _text_to_post_content("## 标题\n\n**加粗** 和 `代码`")
    post = json.loads(content)
    assert "post" not in post              # 不是 {"post": ...} 包裹
    assert "zh_cn" in post
    zh = post["zh_cn"]
    assert set(zh.keys()) == {"title", "content"}
    assert isinstance(zh["content"], list)


def test_text_to_post_content_preserves_markdown_markers():
    # 原始 Markdown 记号进入 md 节点，交给飞书端渲染（不是我们自己转 HTML/纯文本）。
    content = _text_to_post_content("# H1\n\n- 一\n- 二")
    text_nodes = [
        node["text"]
        for para in json.loads(content)["zh_cn"]["content"]
        for node in para
        if node.get("tag") == "md"
    ]
    joined = "\n".join(text_nodes)
    assert "# H1" in joined and "- 一" in joined


def test_text_to_post_content_handles_empty():
    # 空文本也要给出结构合法的 post，不抛异常。
    post = json.loads(_text_to_post_content(""))
    assert post["zh_cn"]["title"] == ""
    assert isinstance(post["zh_cn"]["content"], list)


# ---- reply / send_to_chat 的载体选择与降级 --------------------------------

def _bare_sender():
    """不走 __init__（避免真的建 lark client），只拿一个空壳 FeishuSender 测分支逻辑。"""
    from arkagent.feishu import FeishuSender

    return FeishuSender.__new__(FeishuSender)


def _record_low_level(sender, *, post_fails=False):
    """桩掉底层 _reply_with / _create_in_chat，记录 (msg_type, content)；post 可选强制失败。"""
    calls = []

    def _reply_with(message_id, msg_type, content):
        if post_fails and msg_type == "post":
            raise RuntimeError("post rejected")
        calls.append((msg_type, content))

    def _create_in_chat(chat_id, msg_type, content):
        if post_fails and msg_type == "post":
            raise RuntimeError("post rejected")
        calls.append((msg_type, content))

    sender._reply_with = _reply_with
    sender._create_in_chat = _create_in_chat
    return calls


def test_reply_uses_post_when_markdown_enabled(monkeypatch):
    monkeypatch.delenv("GROUP_BOT_MARKDOWN", raising=False)
    sender = _bare_sender()
    calls = _record_low_level(sender)
    sender.reply("om-1", "## 标题\n**粗**")
    assert len(calls) == 1
    assert calls[0][0] == "post"
    assert "zh_cn" in json.loads(calls[0][1])


def test_reply_falls_back_to_text_when_post_fails(monkeypatch):
    monkeypatch.delenv("GROUP_BOT_MARKDOWN", raising=False)
    sender = _bare_sender()
    calls = _record_low_level(sender, post_fails=True)
    sender.reply("om-1", "hello **world**")
    # 只留降级后的那条纯文本（post 抛异常不记录）。
    assert calls == [("text", json.dumps({"text": "hello **world**"}, ensure_ascii=False))]


def test_reply_sends_plain_text_when_markdown_disabled(monkeypatch):
    monkeypatch.setenv("GROUP_BOT_MARKDOWN", "0")
    sender = _bare_sender()
    calls = _record_low_level(sender)
    sender.reply("om-1", "## 不该被渲染")
    assert calls == [("text", json.dumps({"text": "## 不该被渲染"}, ensure_ascii=False))]


def test_send_to_chat_uses_post_then_falls_back(monkeypatch):
    monkeypatch.delenv("GROUP_BOT_MARKDOWN", raising=False)
    # 正常：post
    sender = _bare_sender()
    calls = _record_low_level(sender)
    sender.send_to_chat("oc-1", "**hi**")
    assert calls[0][0] == "post"
    # post 失败：降级 text
    sender2 = _bare_sender()
    calls2 = _record_low_level(sender2, post_fails=True)
    sender2.send_to_chat("oc-1", "**hi**")
    assert calls2 == [("text", json.dumps({"text": "**hi**"}, ensure_ascii=False))]


# ---- 群成员名册归一 + 同名消歧 --------------------------------------------

def test_build_roster_maps_name_to_open_id():
    roster = _build_roster([("张三", "ou-1"), ("李四", "ou-2")])
    assert roster == {"张三": "ou-1", "李四": "ou-2"}


def test_build_roster_dedupes_repeated_pairs():
    # 翻页/重复项：同名同 open_id 只算一个人，仍保留在名册里。
    roster = _build_roster([("张三", "ou-1"), ("张三", "ou-1")])
    assert roster == {"张三": "ou-1"}


def test_build_roster_drops_ambiguous_name():
    # 群里真有两个「张三」→ 名字整体剔除：宁可不 @ 也不 @ 错人。
    roster = _build_roster([("张三", "ou-1"), ("张三", "ou-2"), ("李四", "ou-3")])
    assert roster == {"李四": "ou-3"}


def test_build_roster_skips_blank_name_or_id():
    roster = _build_roster([("", "ou-1"), ("  ", "ou-2"), ("王五", ""), ("赵六", "ou-6")])
    assert roster == {"赵六": "ou-6"}


# ---- Markdown 分块（保围栏代码块完整）-------------------------------------

def test_split_markdown_blocks_splits_on_blank_lines():
    assert _split_markdown_blocks("第一段\n\n第二段") == ["第一段", "第二段"]


def test_split_markdown_blocks_keeps_fenced_code_intact():
    # 围栏代码块内部的空行不该把它切断。
    src = "说明\n\n```py\na = 1\n\nb = 2\n```"
    blocks = _split_markdown_blocks(src)
    assert blocks == ["说明", "```py\na = 1\n\nb = 2\n```"]


# ---- 可点击 @：名册重写 + 混合渲染 ----------------------------------------

def test_text_to_post_content_rewrites_mention_with_roster():
    # 名字命中名册 → 重写成 <at user_id=...>，含 <at> 的块走 structured（会出 tag:a/at 节点）。
    content = _text_to_post_content("请 @张三 跟进", roster={"张三": "ou_zhangsan"})
    dumped = json.dumps(json.loads(content), ensure_ascii=False)
    assert "ou_zhangsan" in dumped
    tags = {node.get("tag") for para in json.loads(content)["zh_cn"]["content"] for node in para}
    assert "at" in tags   # structured 渲染出可点击 @ 节点


def test_text_to_post_content_unknown_name_stays_verbatim():
    # 名字不在名册里 → 不重写，退回原生 md 渲染（没有 <at>）。
    content = _text_to_post_content("请 @路人甲 跟进", roster={"张三": "ou_zhangsan"})
    dumped = json.dumps(json.loads(content), ensure_ascii=False)
    assert "at" not in {
        node.get("tag") for para in json.loads(content)["zh_cn"]["content"] for node in para
    }
    assert "路人甲" in dumped


def test_text_to_post_content_no_roster_is_pure_native():
    # 没有名册：与不带 @ 的老路径完全一致（纯 md 节点）。
    content = _text_to_post_content("请 @张三 跟进")
    tags = {node.get("tag") for para in json.loads(content)["zh_cn"]["content"] for node in para}
    assert tags == {"md"}


def test_text_to_post_content_mixed_mention_and_markdown_block():
    # 含 @ 的段走 structured，其余 md 段仍 native：两种节点都在。
    content = _text_to_post_content(
        "@张三 请看下面\n\n- 一\n- 二", roster={"张三": "ou_zhangsan"}
    )
    tags = {node.get("tag") for para in json.loads(content)["zh_cn"]["content"] for node in para}
    assert "at" in tags   # @ 段 structured
    assert "md" in tags   # 列表段 native


def test_reply_passes_roster_into_post(monkeypatch):
    # reply 带名册时，正文里的 @人名 应被重写进 post content。
    monkeypatch.delenv("GROUP_BOT_MARKDOWN", raising=False)
    sender = _bare_sender()
    sender._roster_cache = {}
    calls = _record_low_level(sender)
    sender.reply("om-1", "请 @张三 跟进", roster={"张三": "ou_zhangsan"})
    assert calls[0][0] == "post"
    assert "ou_zhangsan" in calls[0][1]

