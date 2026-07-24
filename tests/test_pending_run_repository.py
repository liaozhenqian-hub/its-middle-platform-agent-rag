import pytest

from knowledge.agent_runtime.pending_runs import (
    PendingRunConflictError,
    PendingRunNotFoundError,
    PendingRunRepository,
)


@pytest.mark.asyncio
async def test_pending_run_repository_persists_and_completes_run(tmp_path):
    db = tmp_path / "agent.db"
    repository = PendingRunRepository(db)
    await repository.initialize()
    state = {"schema_version": "1", "context": {"run_id": "run-1"}}
    approvals = [
        {
            "tool_call_id": "call-1",
            "tool_name": "synthetic_write",
            "status": "pending",
        }
    ]

    await repository.save_pending("run-1", "conversation-1", state, approvals)
    restored = await repository.get_pending("run-1")

    assert restored.run_id == "run-1"
    assert restored.conversation_id == "conversation-1"
    assert restored.state == state
    assert restored.approvals == approvals

    await repository.mark_completed("run-1")
    with pytest.raises(PendingRunConflictError):
        await repository.get_pending("run-1")


@pytest.mark.asyncio
async def test_pending_run_repository_distinguishes_unknown_and_completed(tmp_path):
    repository = PendingRunRepository(tmp_path / "agent.db")
    await repository.initialize()

    with pytest.raises(PendingRunNotFoundError):
        await repository.get_pending("missing")

    await repository.save_pending("run-1", "conversation-1", {}, [])
    await repository.mark_completed("run-1")
    with pytest.raises(PendingRunConflictError):
        await repository.mark_completed("run-1")


@pytest.mark.asyncio
async def test_pending_run_repository_deletes_conversation_runs(tmp_path):
    repository = PendingRunRepository(tmp_path / "agent.db")
    await repository.initialize()
    await repository.save_pending("run-1", "conversation-1", {}, [])

    await repository.delete_conversation("conversation-1")

    with pytest.raises(PendingRunNotFoundError):
        await repository.get_pending("run-1")
