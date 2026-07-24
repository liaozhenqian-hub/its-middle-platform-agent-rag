import asyncio
from types import SimpleNamespace

import pytest

from knowledge.feishu.bridge import FeishuBotBridge
from knowledge.feishu.repository import FeishuEventRepository
from knowledge.quality import QualityCaptureService, QualityRepository


class FakeGateway:
    def __init__(self):
        self.handler = None
        self.replies = []
        self.markdown_replies = []
        self.closed = False
        self.bot_open_id = "ou_bot"

    def start(self, handler):
        self.handler = handler

    def reply_text(self, message_id, text):
        self.replies.append((message_id, text))
        return f"bot-reply-{len(self.replies)}"

    def reply_markdown(self, message_id, text, *, title="中台助手"):
        self.markdown_replies.append((message_id, text, title))
        return f"bot-markdown-{len(self.markdown_replies)}"

    def get_user_name(self, user_id):
        return "张三" if user_id == "ou-user-1" else ""

    async def close(self):
        self.closed = True


class FakeAgentService:
    def __init__(self, response=None, error=None, delay=0):
        self.response = response or SimpleNamespace(answer="业务回答", citations=[])
        self.error = error
        self.delay = delay
        self.calls = []

    async def chat(self, message, conversation_id=None, **kwargs):
        self.calls.append((message, conversation_id, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.response


class FakeOwnershipService:
    def __init__(self):
        self.claims = []

    async def claim(self, conversation_id, owner_id, *, channel, now=None):
        self.claims.append((conversation_id, owner_id, channel))


def event(event_id="event-1", text="怎么对接工作流"):
    return {
        "event_id": event_id,
        "sender_type": "user",
        "sender_id": "ou-user-1",
        "message_id": f"message-{event_id}",
        "chat_id": "chat-1",
        "chat_type": "p2p",
        "message_type": "text",
        "content": f'{{"text":"{text}"}}',
        "mentions": [],
    }


def reaction(action="created", emoji_type="THUMBSUP"):
    return {
        "event_type": "reaction",
        "event_id": f"reaction-{action}-{emoji_type}",
        "action": action,
        "message_id": "bot-markdown-1",
        "emoji_type": emoji_type,
        "user_id": "ou-user-1",
        "action_time": "1234",
    }


@pytest.mark.asyncio
async def test_bridge_maps_feishu_chat_to_fixed_middle_platform_scope(tmp_path):
    gateway = FakeGateway()
    agent = FakeAgentService()
    ownership = FakeOwnershipService()
    bridge = FeishuBotBridge(
        gateway=gateway,
        agent_service=agent,
        repository=FeishuEventRepository(tmp_path / "events.db"),
        reply_max_chars=3500,
        agent_timeout_seconds=30,
        ownership_service=ownership,
    )
    await bridge.start()

    handled = await bridge.handle_event(event())

    assert handled is True
    assert len(agent.calls) == 1
    message, conversation_id, kwargs = agent.calls[0]
    assert message == "怎么对接工作流"
    assert conversation_id == "feishu:chat-1"
    assert kwargs["knowledge_space_id"] == "middle-platform"
    assert kwargs["domain_id"] is None
    assert kwargs["scope_provided"] is True
    assert kwargs["run_id"]
    assert ownership.claims == [("feishu:chat-1", "ou-user-1", "feishu")]
    assert gateway.markdown_replies == [
        ("message-event-1", "业务回答", "中台助手")
    ]
    assert gateway.replies == []


@pytest.mark.asyncio
async def test_bridge_isolates_group_topics_and_reuses_thread_context(tmp_path):
    gateway = FakeGateway()
    agent = FakeAgentService()
    quality_repository = QualityRepository(tmp_path / "quality.db")
    await quality_repository.initialize()
    bridge = FeishuBotBridge(
        gateway=gateway,
        agent_service=agent,
        repository=FeishuEventRepository(tmp_path / "events.db"),
        quality_capture=QualityCaptureService(quality_repository),
        reply_max_chars=3500,
        agent_timeout_seconds=30,
    )
    await bridge.start()

    first = event("group-topic-1", "审批流管理员转办接口")
    first.update(chat_type="group", mentions=[{"key": "@bot", "open_id": "ou_bot"}])
    second = event("group-topic-2", "指标平台如何对接")
    second.update(chat_type="group", mentions=[{"key": "@bot", "open_id": "ou_bot"}])
    threaded = event("group-thread-1", "继续说明入参")
    threaded.update(
        chat_type="group",
        thread_id="thread-approval",
        root_id="root-approval",
        mentions=[{"key": "@bot", "open_id": "ou_bot"}],
    )
    threaded_again = event("group-thread-2", "再说明出参")
    threaded_again.update(
        chat_type="group",
        thread_id="thread-approval",
        root_id="root-approval",
        mentions=[{"key": "@bot", "open_id": "ou_bot"}],
    )

    for incoming in (first, second, threaded, threaded_again):
        assert await bridge.handle_event(incoming) is True

    conversations = [call[1] for call in agent.calls]
    assert conversations[0] != conversations[1]
    assert conversations[2] == conversations[3]
    assert conversations[2].endswith(":thread:thread-approval")


@pytest.mark.asyncio
async def test_bridge_reply_to_bot_reuses_original_conversation(tmp_path):
    gateway = FakeGateway()
    agent = FakeAgentService()
    quality_repository = QualityRepository(tmp_path / "quality.db")
    await quality_repository.initialize()
    bridge = FeishuBotBridge(
        gateway=gateway,
        agent_service=agent,
        repository=FeishuEventRepository(tmp_path / "events.db"),
        quality_capture=QualityCaptureService(quality_repository),
        reply_max_chars=3500,
        agent_timeout_seconds=30,
    )
    await bridge.start()
    original = event("group-parent", "工作流连接器如何配置")
    original.update(chat_type="group", mentions=[{"key": "@bot", "open_id": "ou_bot"}])
    assert await bridge.handle_event(original) is True
    reply = event("group-child", "继续")
    reply.update(
        chat_type="group",
        parent_id="bot-markdown-1",
        mentions=[{"key": "@bot", "open_id": "ou_bot"}],
    )
    assert await bridge.handle_event(reply) is True
    assert agent.calls[0][1] == agent.calls[1][1]


@pytest.mark.asyncio
async def test_bridge_ignores_group_message_mentioning_another_user(tmp_path):
    gateway = FakeGateway()
    agent = FakeAgentService()
    bridge = FeishuBotBridge(
        gateway=gateway,
        agent_service=agent,
        repository=FeishuEventRepository(tmp_path / "events.db"),
        reply_max_chars=3500,
        agent_timeout_seconds=30,
    )
    await bridge.start()
    incoming = event("event-other-mention", "@_user_1 请处理这个问题")
    incoming.update(
        chat_type="group",
        mentions=[{"key": "@_user_1", "open_id": "ou_other_user"}],
    )

    handled = await bridge.handle_event(incoming)

    assert handled is False
    assert agent.calls == []
    assert gateway.replies == []


@pytest.mark.asyncio
async def test_bridge_suppresses_duplicate_event_and_splits_reply(tmp_path):
    gateway = FakeGateway()
    agent = FakeAgentService(
        SimpleNamespace(answer="第一段\n\n" + "x" * 30, citations=[])
    )
    bridge = FeishuBotBridge(
        gateway=gateway,
        agent_service=agent,
        repository=FeishuEventRepository(tmp_path / "events.db"),
        reply_max_chars=20,
        agent_timeout_seconds=30,
    )
    await bridge.start()

    first = await bridge.handle_event(event())
    duplicate = await bridge.handle_event(event())

    assert first is True
    assert duplicate is False
    assert len(agent.calls) == 1
    assert all(len(text) <= 20 for _, text, _ in gateway.markdown_replies)
    assert [message_id for message_id, _, _ in gateway.markdown_replies] == [
        "message-event-1",
        "message-event-1",
        "message-event-1",
    ]
    assert [title for _, _, title in gateway.markdown_replies] == [
        "中台助手 (1/3)",
        "中台助手 (2/3)",
        "中台助手 (3/3)",
    ]


@pytest.mark.asyncio
async def test_bridge_returns_sanitized_failure_for_timeout_or_agent_error(tmp_path):
    gateway = FakeGateway()
    agent = FakeAgentService(
        error=RuntimeError("private prompt and secret-token must-not-leak")
    )
    bridge = FeishuBotBridge(
        gateway=gateway,
        agent_service=agent,
        repository=FeishuEventRepository(tmp_path / "events.db"),
        reply_max_chars=3500,
        agent_timeout_seconds=30,
    )
    await bridge.start()

    assert await bridge.handle_event(event("event-error")) is False

    output = gateway.replies[0][1]
    assert "暂时无法处理" in output
    assert "private prompt" not in output
    assert "secret-token" not in output


@pytest.mark.asyncio
async def test_bridge_hands_thread_callback_to_event_loop_and_closes(tmp_path):
    gateway = FakeGateway()
    agent = FakeAgentService()
    bridge = FeishuBotBridge(
        gateway=gateway,
        agent_service=agent,
        repository=FeishuEventRepository(tmp_path / "events.db"),
        reply_max_chars=3500,
        agent_timeout_seconds=30,
    )
    await bridge.start()

    await asyncio.to_thread(gateway.handler, event("event-thread"))
    for _ in range(50):
        if agent.calls:
            break
        await asyncio.sleep(0.01)
    await bridge.close()

    assert len(agent.calls) == 1
    assert gateway.closed is True


@pytest.mark.asyncio
async def test_bridge_captures_real_sender_and_maps_reply_reactions_to_feedback(tmp_path):
    gateway = FakeGateway()
    agent = FakeAgentService(
        SimpleNamespace(
            status="completed",
            answer="业务回答",
            citations=[],
            tool_runs=[],
            last_agent="Manager Agent",
        )
    )
    quality_repository = QualityRepository(tmp_path / "quality.db")
    await quality_repository.initialize()
    bridge = FeishuBotBridge(
        gateway=gateway,
        agent_service=agent,
        repository=FeishuEventRepository(tmp_path / "events.db"),
        quality_capture=QualityCaptureService(quality_repository),
        reply_max_chars=3500,
        agent_timeout_seconds=30,
    )
    await bridge.start()

    assert await bridge.handle_event(event("quality-event")) is True
    page = await quality_repository.list_turns(channel="feishu")
    assert page.total == 1
    turn = await quality_repository.get_turn(page.items[0].id)
    assert turn.user_id == "ou-user-1"
    assert turn.user_name == "张三"
    assert turn.channel_message_id == "message-quality-event"
    assert turn.channel_reply_message_id == "bot-markdown-1"
    assert turn.answer == "业务回答"

    assert await bridge.handle_event(reaction()) is True
    turn = await quality_repository.get_turn(turn.id)
    assert turn.feedback[0].rating == "positive"
    assert await bridge.handle_event(reaction("deleted")) is True
    assert (await quality_repository.get_turn(turn.id)).feedback == []


@pytest.mark.asyncio
async def test_bridge_does_not_fail_reply_when_quality_completion_is_unavailable(tmp_path):
    class FailingCapture:
        async def start(self, value):
            return SimpleNamespace(id="turn-1")

        async def bind_reply(self, run_id, message_id):
            raise OSError("private quality database path")

    gateway = FakeGateway()
    bridge = FeishuBotBridge(
        gateway=gateway,
        agent_service=FakeAgentService(),
        repository=FeishuEventRepository(tmp_path / "events.db"),
        quality_capture=FailingCapture(),
        reply_max_chars=3500,
        agent_timeout_seconds=30,
    )
    await bridge.start()

    handled = await bridge.handle_event(event("quality-unavailable"))

    assert handled is True
    assert len(gateway.markdown_replies) == 1
    assert gateway.replies == []
