import json
from types import SimpleNamespace

from arkagent.feishu import (
    _inbound_to_incoming,
    normalize_feishu_message,
    normalize_history_item,
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
    assert result.text == "帮我总结"
    assert result.mentioned_bot is True
    assert result.user_open_id == "ou-user"
    assert result.chat_type == "group"
    assert result.create_time == 1700000001234


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
