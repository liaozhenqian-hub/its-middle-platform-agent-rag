import asyncio

import pytest

from knowledge.quality import EvalCaseCreate, QualityRepository
from knowledge.quality.worker import QualityEvalWorker


@pytest.mark.asyncio
async def test_eval_worker_claims_one_queued_run_and_recovers_stale_jobs(tmp_path):
    repository = QualityRepository(tmp_path / "quality.db")
    await repository.initialize()
    case = await repository.create_eval_case(
        EvalCaseCreate(name="critical", question="审批流怎么对接", enabled=True)
    )
    stale = await repository.create_eval_run(
        total_cases=1,
        application_version="0.2.0",
        provider="deepseek",
        model_name="deepseek-v4-flash",
        status="running",
        case_ids=[case.id],
    )

    class Evaluator:
        def __init__(self):
            self.calls = []

        async def run_existing(self, run_id):
            await repository.complete_eval_run(run_id)
            self.calls.append(run_id)

        async def queue_cases(self, case_ids):
            raise AssertionError("scheduler is not due in this test")

    evaluator = Evaluator()
    worker = QualityEvalWorker(
        repository=repository,
        evaluator=evaluator,
        poll_seconds=0.01,
        stale_seconds=0,
        scheduled=False,
    )
    await worker.start()
    for _ in range(100):
        if evaluator.calls:
            break
        await asyncio.sleep(0.01)
    await worker.close()

    assert evaluator.calls == [stale.id]
    assert (await repository.get_eval_run(stale.id)).status == "completed"
