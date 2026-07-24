from types import SimpleNamespace

import pytest

from knowledge.feishu.gateway import (
    FeishuGatewayError,
    LarkOapiGateway,
    normalize_sdk_event,
    normalize_sdk_reaction_event,
)


def test_normalize_sdk_event_keeps_only_required_fields():
    data = SimpleNamespace(
        header=SimpleNamespace(event_id="event-1"),
        event=SimpleNamespace(
            sender=SimpleNamespace(
                sender_type="user",
                sender_id=SimpleNamespace(open_id="ou-user-1"),
            ),
            message=SimpleNamespace(
                message_id="message-1",
                chat_id="chat-1",
                chat_type="group",
                message_type="text",
                content='{"text":"@_user_1 hello"}',
                mentions=[
                    SimpleNamespace(
                        key="@_user_1",
                        name="Bot",
                        mentioned_type="bot",
                        id=SimpleNamespace(open_id="ou_bot"),
                    )
                ],
            ),
        ),
    )

    assert normalize_sdk_event(data) == {
        "event_id": "event-1",
        "sender_type": "user",
        "sender_id": "ou-user-1",
        "sender_name": "",
        "message_id": "message-1",
        "thread_id": "",
        "root_id": "",
        "parent_id": "",
        "chat_id": "chat-1",
        "chat_type": "group",
        "message_type": "text",
        "content": '{"text":"@_user_1 hello"}',
        "mentions": [
            {
                "key": "@_user_1",
                "open_id": "ou_bot",
                "mentioned_type": "bot",
            }
        ],
    }


def test_normalize_sdk_reaction_event_keeps_only_feedback_identity():
    data = SimpleNamespace(
        header=SimpleNamespace(event_id="reaction-event-1"),
        event=SimpleNamespace(
            message_id="bot-message-1",
            reaction_type=SimpleNamespace(emoji_type="THUMBSUP"),
            user_id=SimpleNamespace(open_id="ou-user-1"),
            action_time="1234",
        ),
    )

    assert normalize_sdk_reaction_event(data, action="created") == {
        "event_type": "reaction",
        "event_id": "reaction-event-1",
        "action": "created",
        "message_id": "bot-message-1",
        "emoji_type": "THUMBSUP",
        "user_id": "ou-user-1",
        "action_time": "1234",
    }


def test_gateway_reply_failure_exposes_only_status_code(monkeypatch):
    gateway = LarkOapiGateway("cli_test", "rotated-secret")
    reply = gateway._api_client.im.v1.message.reply
    monkeypatch.setattr(
        gateway._api_client.im.v1.message,
        "reply",
        lambda request: SimpleNamespace(success=lambda: False, code=999, msg="secret body"),
    )

    with pytest.raises(FeishuGatewayError) as captured:
        gateway.reply_text("message-1", "safe reply")

    assert "999" in str(captured.value)
    assert "secret body" not in str(captured.value)


def test_gateway_replies_with_markdown_card(monkeypatch):
    gateway = LarkOapiGateway("cli_test", "rotated-secret")
    captured = {}

    def reply(request):
        captured["request"] = request
        return SimpleNamespace(success=lambda: True)

    monkeypatch.setattr(gateway._api_client.im.v1.message, "reply", reply)

    gateway.reply_markdown(
        "message-1",
        "## 问题摘要\n\n- 第一项\n- 第二项",
        title="中台助手 (1/2)",
    )

    body = captured["request"].request_body
    card = __import__("json").loads(body.content)
    assert body.msg_type == "interactive"
    assert card["header"]["title"]["content"] == "中台助手 (1/2)"
    assert card["elements"][0] == {
        "tag": "markdown",
        "content": "## 问题摘要\n\n- 第一项\n- 第二项",
    }


def test_gateway_falls_back_to_text_when_card_is_rejected(monkeypatch):
    gateway = LarkOapiGateway("cli_test", "rotated-secret")
    message_types = []

    def reply(request):
        message_types.append(request.request_body.msg_type)
        if request.request_body.msg_type == "interactive":
            return SimpleNamespace(success=lambda: False, code=999)
        return SimpleNamespace(success=lambda: True)

    monkeypatch.setattr(gateway._api_client.im.v1.message, "reply", reply)

    gateway.reply_markdown("message-1", "**回答**")

    assert message_types == ["interactive", "text"]
