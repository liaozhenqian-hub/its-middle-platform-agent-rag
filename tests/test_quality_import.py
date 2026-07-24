import json

import pytest

from knowledge.quality.import_cases import import_benchmark_cases
from knowledge.quality.repository import QualityRepository


@pytest.mark.asyncio
async def test_benchmark_import_is_dry_run_capable_and_idempotent(tmp_path):
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "workflow-retry-colloquial",
                        "variant": "colloquial",
                        "question": "HTTP 节点超时会重试几次？",
                        "category": "workflow",
                        "expected_tools": ["workflow_expert"],
                        "expected_citation_types": ["code"],
                        "expected_behavior": "answer",
                        "latency_budget_ms": 45000,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repository = QualityRepository(tmp_path / "quality.db")
    await repository.initialize()

    dry_run = await import_benchmark_cases(repository, benchmark_path, dry_run=True)
    assert dry_run.validated == 1
    assert dry_run.written == 0
    assert await repository.list_eval_cases() == []

    first = await import_benchmark_cases(repository, benchmark_path)
    second = await import_benchmark_cases(repository, benchmark_path)

    assert first.written == 1
    assert second.written == 1
    cases = await repository.list_eval_cases()
    assert len(cases) == 1
    assert cases[0].id == "public-intent-100:workflow-retry-colloquial"
    assert cases[0].domain_id == "workflow"
    assert cases[0].tags == ["public-intent-100", "workflow", "colloquial"]
    assert cases[0].max_latency_ms == 45000
    assert cases[0].max_tool_calls == 6
    assert cases[0].max_citations == 10
