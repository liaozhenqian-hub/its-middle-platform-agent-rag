from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from knowledge.config.settings import PROJECT_ROOT, Settings
from knowledge.quality.models import EvalCaseCreate
from knowledge.quality.repository import QualityRepository


DEFAULT_BENCHMARK_PATH = (
    PROJECT_ROOT
    / "storage"
    / "evaluations"
    / "domain-public-benchmark-cases-100-20260717.json"
)


@dataclass(frozen=True)
class ImportSummary:
    validated: int
    written: int


def _case_value(raw: dict[str, Any]) -> tuple[str, EvalCaseCreate]:
    stable_id = str(raw["id"]).strip()
    category = str(raw["category"]).strip()
    variant = str(raw.get("variant") or "unknown").strip()
    if not stable_id or not category:
        raise ValueError("benchmark case id and category are required")
    domain_id = category if category in {
        "approval-flow",
        "workflow",
        "metric-platform",
    } else None
    return (
        f"public-intent-100:{stable_id}",
        EvalCaseCreate(
            name=stable_id,
            question=str(raw["question"]),
            domain_id=domain_id,
            required_tools=[str(item) for item in raw.get("expected_tools", [])],
            required_citation_types=[
                str(item) for item in raw.get("expected_citation_types", [])
            ],
            tags=["public-intent-100", category, variant],
            expected_behavior=str(raw.get("expected_behavior") or "answer"),
            max_latency_ms=float(raw.get("latency_budget_ms") or 60_000),
            max_tool_calls=int(raw.get("max_tool_calls") or 6),
            max_citations=int(raw.get("max_citations") or 10),
        ),
    )


async def import_benchmark_cases(
    repository: QualityRepository,
    benchmark_path: Path,
    *,
    dry_run: bool = False,
) -> ImportSummary:
    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("benchmark file must contain a cases list")
    cases = [_case_value(raw) for raw in raw_cases]
    if not dry_run:
        for case_id, value in cases:
            await repository.upsert_eval_case(case_id, value)
    return ImportSummary(validated=len(cases), written=0 if dry_run else len(cases))


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Import the public 100-case benchmark")
    parser.add_argument("--path", type=Path, default=DEFAULT_BENCHMARK_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    repository = QualityRepository(settings.resolved_agent_quality_db)
    await repository.initialize()
    summary = await import_benchmark_cases(repository, args.path, dry_run=args.dry_run)
    print(f"{summary.validated} cases validated, {summary.written} written")


if __name__ == "__main__":
    asyncio.run(_main())
