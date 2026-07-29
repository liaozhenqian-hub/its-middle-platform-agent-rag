import pytest
from datetime import datetime, timedelta, timezone

from knowledge.migrations.langgraph_checkpoints import migrate_checkpoints


class _Tuple:
    def __init__(self, checkpoint_id, *, parent=None, writes=None):
        self.config = {"configurable": {"thread_id": "conversation-1", "checkpoint_id": checkpoint_id}}
        self.checkpoint = {"id": checkpoint_id, "channel_versions": {"state": checkpoint_id}}
        self.metadata = {"step": int(checkpoint_id)}
        self.parent_config = parent
        self.pending_writes = writes or []


@pytest.mark.asyncio
async def test_checkpoint_migration_uses_official_saver_apis_in_parent_order():
    parent = {"configurable": {"thread_id": "conversation-1", "checkpoint_id": "1"}}
    source_rows = [_Tuple("2", parent=parent, writes=[("task-1", "channel", "value")]), _Tuple("1")]

    class Source:
        async def alist(self, _config):
            for row in source_rows:
                yield row

    class Target:
        def __init__(self): self.puts = []; self.writes = []
        async def aput(self, config, checkpoint, metadata, new_versions):
            assert config["configurable"]["checkpoint_ns"] == ""
            self.puts.append((config, checkpoint, metadata, new_versions))
            return {"configurable": {"thread_id": "conversation-1", "checkpoint_id": checkpoint["id"]}}
        async def aput_writes(self, config, writes, task_id, task_path=""):
            self.writes.append((config, writes, task_id, task_path))

    target = Target()
    result = await migrate_checkpoints(Source(), target)

    assert result.migrated_count == 2
    assert [item[1]["id"] for item in target.puts] == ["1", "2"]
    assert target.writes[0][1] == [("channel", "value")]
    assert target.writes[0][2] == "task-1"
    assert "value" not in repr(result)


@pytest.mark.asyncio
async def test_checkpoint_migration_skips_expired_threads_but_keeps_active_parents():
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    old_parent = _Tuple("1")
    old_parent.checkpoint["ts"] = (now - timedelta(days=2)).isoformat()
    active_child = _Tuple("2")
    active_child.checkpoint["ts"] = (now - timedelta(hours=1)).isoformat()
    expired = _Tuple("3")
    expired.config["configurable"]["thread_id"] = "expired-thread"
    expired.checkpoint["ts"] = (now - timedelta(days=2)).isoformat()

    class Source:
        async def alist(self, _config):
            for row in (expired, active_child, old_parent):
                yield row

    class Target:
        def __init__(self): self.puts = []
        async def aput(self, config, checkpoint, metadata, new_versions):
            self.puts.append(checkpoint["id"])
            return {"configurable": {"thread_id": "conversation-1", "checkpoint_id": checkpoint["id"]}}
        async def aput_writes(self, *_args): pass

    target = Target()
    result = await migrate_checkpoints(
        Source(), target, active_after=now - timedelta(hours=24)
    )

    assert target.puts == ["1", "2"]
    assert result.thread_count == 1
    assert result.skipped_thread_count == 1
