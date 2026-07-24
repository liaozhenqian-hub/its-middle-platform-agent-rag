import pytest

from knowledge.agent_runtime.sessions import AgentSessionFactory


@pytest.mark.asyncio
async def test_agent_session_loads_recent_items_but_retains_full_history(tmp_path):
    factory = AgentSessionFactory(tmp_path / "sessions.db", history_limit=50)
    session = factory.create("conversation-1")
    items = [
        {"role": "user", "content": f"message-{index}"}
        for index in range(60)
    ]

    await session.add_items(items)
    recent = await session.get_items()
    all_items = await session.get_items(limit=100)

    assert len(recent) == 50
    assert recent[0]["content"] == "message-10"
    assert len(all_items) == 60

    session.close()


@pytest.mark.asyncio
async def test_agent_session_can_be_reopened_and_deleted(tmp_path):
    factory = AgentSessionFactory(tmp_path / "sessions.db", history_limit=50)
    first = factory.create("conversation-1")
    await first.add_items([{"role": "user", "content": "persisted"}])
    first.close()

    reopened = factory.create("conversation-1")
    assert await reopened.get_items() == [
        {"role": "user", "content": "persisted"}
    ]
    await reopened.clear_session()
    assert await reopened.get_items() == []
    reopened.close()
