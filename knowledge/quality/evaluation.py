from __future__ import annotations

import asyncio
from dataclasses import asdict
from time import perf_counter
from typing import Any
from uuid import uuid4

from knowledge.quality.behavior import BehaviorChecker
from knowledge.quality.models import EvalCase, EvalRun
from knowledge.quality.judge import SemanticJudgeError
from knowledge.quality.repository import QualityNotFoundError, QualityRepository


class QualityEvaluationService:
    def __init__(
        self,
        *,
        repository: QualityRepository,
        agent_service: Any,
        application_version: str,
        provider: str,
        model_name: str,
        semantic_judge: Any | None = None,
        case_timeout_seconds: float = 120,
        judge_timeout_seconds: float = 30,
        run_config_snapshot: dict[str, Any] | None = None,
    ):
        self.repository = repository
        self.agent_service = agent_service
        self.application_version = application_version
        self.provider = provider
        self.model_name = model_name
        self.semantic_judge = semantic_judge
        self.case_timeout_seconds = case_timeout_seconds
        self.judge_timeout_seconds = judge_timeout_seconds
        self.run_config_snapshot = run_config_snapshot or {}

    async def _select_cases(self, case_ids: list[str] | None) -> list[EvalCase]:
        available = await self.repository.list_eval_cases(enabled=True)
        if case_ids is None:
            cases = available
        else:
            selected = set(case_ids)
            cases = [case for case in available if case.id in selected]
            if len(cases) != len(selected):
                raise QualityNotFoundError("one or more evaluation cases were not found")
        if not cases:
            raise ValueError("at least one enabled evaluation case is required")
        return cases

    async def queue_cases(self, case_ids: list[str] | None = None) -> EvalRun:
        cases = await self._select_cases(case_ids)
        return await self.repository.create_eval_run(
            total_cases=len(cases),
            application_version=self.application_version,
            provider=self.provider,
            model_name=self.model_name,
            status="queued",
            case_ids=[case.id for case in cases],
            config_snapshot={
                **self.run_config_snapshot,
                "case_timeout_seconds": self.case_timeout_seconds,
                "judge_timeout_seconds": self.judge_timeout_seconds,
            },
        )

    async def run_cases(self, case_ids: list[str] | None = None) -> EvalRun:
        cases = await self._select_cases(case_ids)
        run = await self.repository.create_eval_run(
            total_cases=len(cases),
            application_version=self.application_version,
            provider=self.provider,
            model_name=self.model_name,
            status="running",
            case_ids=[case.id for case in cases],
            config_snapshot={
                **self.run_config_snapshot,
                "case_timeout_seconds": self.case_timeout_seconds,
                "judge_timeout_seconds": self.judge_timeout_seconds,
            },
        )
        return await self._run_selected(run, cases)

    async def run_existing(self, run_id: str) -> EvalRun:
        run = await self.repository.get_eval_run(run_id)
        if run is None:
            raise QualityNotFoundError(run_id)
        cases = await self._select_cases(run.case_ids)
        return await self._run_selected(run, cases)

    async def _run_selected(self, run: EvalRun, cases: list[EvalCase]) -> EvalRun:
        for index, case in enumerate(cases, 1):
            latest = await self.repository.get_eval_run(run.id)
            if latest is None:
                raise QualityNotFoundError(run.id)
            if latest.cancel_requested:
                return await self.repository.mark_eval_run_cancelled(run.id)
            await self.repository.update_eval_progress(run.id, index)
            await self._run_case(run.id, case)
        return await self.repository.complete_eval_run(run.id)

    async def cancel(self, run_id: str) -> EvalRun:
        return await self.repository.request_eval_run_cancel(run_id)

    async def retry_failed(self, run_id: str) -> EvalRun:
        if await self.repository.get_eval_run(run_id) is None:
            raise QualityNotFoundError(run_id)
        failed_ids = [
            result.case_id
            for result in await self.repository.list_eval_results(run_id)
            if not result.passed
        ]
        if not failed_ids:
            raise ValueError("evaluation run has no failed cases")
        return await self.queue_cases(failed_ids)

    async def _run_case(self, run_id: str, case: EvalCase) -> None:
        started_at = perf_counter()
        try:
            response = await asyncio.wait_for(
                self._execute_turns(case), timeout=self.case_timeout_seconds
            )
            duration_ms = (perf_counter() - started_at) * 1000
            answer = str(getattr(response, "answer", None) or "")
            tool_runs = list(getattr(response, "tool_runs", None) or [])
            tool_names = [
                str(getattr(item, "tool_name", "") or "")
                for item in tool_runs
                if str(getattr(item, "status", "") or "") != "skipped"
            ]
            citations = list(getattr(response, "citations", None) or [])
            citation_types = [
                str(getattr(item, "source_type", "") or "") for item in citations
            ]
            normalized_answer = answer.casefold()
            checks = {
                "status": str(getattr(response, "status", "")) == "completed",
                "required_tools": set(case.required_tools).issubset(tool_names),
                "required_citations": set(case.required_citation_types).issubset(citation_types),
                # Critical-v2 facts are claims for the semantic judge.  Older
                # exploratory cases retain the deterministic fallback so the
                # existing eval contract remains backwards compatible.
                "required_facts": (
                    True
                    if case.suite == "critical-v2" and self.semantic_judge is not None
                    else all(fact.casefold() in normalized_answer for fact in case.required_facts)
                ),
                "forbidden_facts": all(fact.casefold() not in normalized_answer for fact in case.forbidden_facts),
                "behavior": BehaviorChecker.matches(case.expected_behavior, answer, tool_names),
                "tool_count": len(tool_names) <= case.max_tool_calls,
                "citation_count": len(citation_types) <= case.max_citations,
                "latency": duration_ms <= case.max_latency_ms,
                "deployment_wording": not (
                    any(item == "code" for item in citation_types)
                    and answer.startswith("本次检索暂未找到该能力的明确实现")
                ),
            }
            failure_codes = [name for name, passed in checks.items() if not passed]
            judge: dict[str, Any] = {}
            judge_score: float | None = None
            review_state = "not_required"
            passed = not failure_codes
            # Refusal and clarification cases are deterministic behavior/safety
            # gates.  Sending them to a factual evidence judge adds cost and can
            # turn a correct no-citation refusal into judge_error.
            if (
                passed
                and self.semantic_judge is not None
                and case.expected_behavior == "answer"
            ):
                try:
                    judge = await asyncio.wait_for(
                        self.semantic_judge.judge(
                            question=case.question,
                            answer=answer,
                            evidence=self._evidence_summaries(citations),
                            required_facts=case.required_facts,
                            forbidden_facts=case.forbidden_facts,
                        ),
                        timeout=self.judge_timeout_seconds,
                    )
                except SemanticJudgeError:
                    passed = False
                    failure_codes.append("judge_error")
                    review_state = "review_required"
                except asyncio.TimeoutError:
                    passed = False
                    failure_codes.append("judge_timeout")
                    review_state = "review_required"
                else:
                    judge_score = float(judge.get("score", 0))
                    facts_supported = judge.get("facts_supported") is True
                    no_contradiction = judge.get("critical_contradiction") is False
                    passed = judge_score >= 80 and facts_supported and no_contradiction
                    if not facts_supported:
                        failure_codes.append("evidence_support")
                    if not no_contradiction:
                        failure_codes.append("critical_contradiction")
                    if judge_score < 80:
                        failure_codes.append("semantic_score")
                    if 70 <= judge_score <= 84:
                        review_state = "review_required"
                    if case.suite == "critical-v2":
                        checks["required_facts_supported"] = facts_supported
            await self.repository.save_eval_result(
                run_id=run_id,
                case_id=case.id,
                status=str(getattr(response, "status", "unknown")),
                answer=answer,
                last_agent=str(getattr(response, "last_agent", "") or ""),
                tool_names=tool_names,
                citation_types=citation_types,
                duration_ms=duration_ms,
                checks=checks,
                passed=passed,
                judge_score=judge_score,
                judge=judge,
                failure_codes=failure_codes,
                review_state=review_state,
                case_snapshot=asdict(case),
            )
        except Exception as exc:
            error_type = "TimeoutError" if isinstance(exc, asyncio.TimeoutError) else type(exc).__name__
            await self.repository.save_eval_result(
                run_id=run_id,
                case_id=case.id,
                status="error",
                answer=None,
                last_agent="",
                tool_names=[],
                citation_types=[],
                duration_ms=(perf_counter() - started_at) * 1000,
                checks={
                    "status": False, "required_tools": False,
                    "required_citations": False, "required_facts": False,
                    "forbidden_facts": True, "behavior": False,
                    "tool_count": False, "citation_count": False,
                    "latency": False, "deployment_wording": True,
                },
                passed=False,
                error_type=error_type,
                failure_codes=[error_type],
                case_snapshot=asdict(case),
            )

    async def _execute_turns(self, case: EvalCase) -> Any:
        conversation_id = f"eval:{uuid4()}"
        response = None
        for message in case.turns or [case.question]:
            response = await self.agent_service.chat(
                message,
                conversation_id,
                knowledge_space_id=case.knowledge_space_id,
                domain_id=case.domain_id,
                scope_provided=True,
            )
        return response

    @staticmethod
    def _evidence_summaries(citations: list[Any]) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for citation in citations[:10]:
            metadata = dict(getattr(citation, "metadata", None) or {})
            summaries.append(
                {
                    "source_type": str(getattr(citation, "source_type", "") or ""),
                    "source_id": str(getattr(citation, "source_id", "") or "")[:300],
                    "title": str(getattr(citation, "title", "") or "")[:500],
                    "metadata": {
                        key: value
                        for key, value in metadata.items()
                        if key not in {"content", "body", "token", "authorization", "password"}
                    },
                }
            )
        return summaries
