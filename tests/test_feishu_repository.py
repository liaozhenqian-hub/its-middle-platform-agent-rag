import asyncio
import sqlite3

import pytest

from knowledge.feishu.repository import FeishuEventRepository


@pytest.mark.asyncio
async def test_feishu_repository_claim_is_atomic_and_persists_completion(tmp_path):
    database = tmp_path / "feishu.db"
    repository = FeishuEventRepository(database)
    await repository.initialize()

    results = await asyncio.gather(
        *(
            repository.claim("event-1", "message-1", "chat-1")
            for _ in range(5)
        )
    )
    await repository.complete("event-1")
    restarted = FeishuEventRepository(database)
    await restarted.initialize()

    assert results.count(True) == 1
    assert results.count(False) == 4
    assert await restarted.claim("event-1", "message-1", "chat-1") is False


@pytest.mark.asyncio
async def test_feishu_repository_allows_only_one_retry_for_failed_event(tmp_path):
    database = tmp_path / "feishu.db"
    repository = FeishuEventRepository(database)
    await repository.initialize()
    assert await repository.claim("event-2", "message-2", "chat-2") is True
    await repository.fail("event-2", "TimeoutError")

    restarted = FeishuEventRepository(database)
    await restarted.initialize()
    assert await restarted.claim("event-2", "message-2", "chat-2") is True
    await restarted.fail("event-2", "RuntimeError")
    assert await restarted.claim("event-2", "message-2", "chat-2") is False


@pytest.mark.asyncio
async def test_feishu_repository_recovers_processing_event_after_restart(tmp_path):
    database = tmp_path / "feishu.db"
    repository = FeishuEventRepository(database)
    await repository.initialize()
    assert await repository.claim("event-crash", "message-crash", "chat-crash") is True

    restarted = FeishuEventRepository(database)
    await restarted.initialize()

    assert await restarted.claim("event-crash", "message-crash", "chat-crash") is True


@pytest.mark.asyncio
async def test_feishu_repository_schema_cannot_store_message_or_credentials(tmp_path):
    database = tmp_path / "feishu.db"
    repository = FeishuEventRepository(database)
    await repository.initialize()
    await repository.claim("event-private", "message-private", "chat-private")
    await repository.fail("event-private", "SecretFailure")

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(feishu_events)")
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        rows = connection.execute("SELECT * FROM feishu_events").fetchall()

    assert journal_mode.lower() == "wal"
    assert not columns & {
        "message_text",
        "answer",
        "app_secret",
        "token",
        "payload",
        "citations",
    }
    serialized = repr(rows)
    assert "message body" not in serialized
    assert "app secret" not in serialized
