from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Any

from knowledge.quality.models import (
    CitationSnapshot,
    QualityAnnotationCreate,
    QualitySpanCreate,
    QualitySpanSnapshot,
    ToolRunSnapshot,
    TurnCompletion,
    TurnStart,
)
from knowledge.quality.repository import QualityRepository


logger = logging.getLogger(__name__)


class QualityCaptureService:
    def __init__(self, repository: QualityRepository, memory_repository: Any | None = None):
        self.repository = repository
        self.memory_repository = memory_repository
        self._memory_tasks: set[asyncio.Task] = set()

    async def start(self, value: TurnStart):
        turn = await self.repository.start_turn(value)
        previous = await self.repository.get_previous_conversation_turn(
            turn.conversation_id, turn.id
        )
        if previous is not None:
            normalized = value.question.strip().casefold()
            code = None
            if any(marker in normalized for marker in ("不对", "还是不行", "你理解错", "回答错")):
                code = "user_correction"
            elif any(marker in normalized for marker in ("为什么又问", "已经告诉", "重复问", "又让我")):
                code = "reasked"
            if code:
                await self.repository.create_annotation(QualityAnnotationCreate(
                    turn_id=turn.id,
                    source="rule",
                    code=code,
                    severity="warning",
                    confidence=0.9,
                    details={"previous_turn_id": previous.id},
                ))
        return turn

    async def complete(self, run_id: str, value: TurnCompletion):
        completed = await self.repository.complete_turn(run_id, value)
        await self._record_completion_spans(completed, value)
        await self._annotate_completion(completed, value)
        self._enqueue_memory_candidate(completed, value)
        return completed

    def _enqueue_memory_candidate(self, turn: Any, value: TurnCompletion) -> None:
        if (
            self.memory_repository is None
            or value.status != "completed"
            or not getattr(turn, "user_id", None)
            or not (value.answer or "").strip()
        ):
            return
        task = asyncio.create_task(
            self._enqueue_memory_candidate_async(turn, value),
            name=f"memory-enqueue-{turn.id}",
        )
        self._memory_tasks.add(task)
        task.add_done_callback(self._memory_tasks.discard)

    async def _enqueue_memory_candidate_async(self, turn: Any, value: TurnCompletion) -> None:
        try:
            await self.memory_repository.enqueue_extraction(
                user_id=str(turn.user_id),
                conversation_id=str(turn.conversation_id),
                space_id=str(turn.knowledge_space_id or "middle-platform"),
                domain_id=turn.domain_id,
                channel=str(turn.channel or "api"),
                question=str(turn.question or ""),
                answer=value.answer,
                source_turn_id=str(turn.id),
                source_citations=tuple(item.source_id for item in value.citations),
            )
        except Exception as exc:
            logger.info(
                "Memory extraction enqueue skipped turn_id=%s error_type=%s",
                turn.id,
                type(exc).__name__,
            )

    async def complete_response(
        self,
        run_id: str,
        response: Any,
        *,
        status: str,
        duration_ms: float,
        error_type: str | None = None,
    ):
        return await self.complete(
            run_id,
            TurnCompletion(
                status=status,
                answer=getattr(response, "answer", None) if response is not None else None,
                last_agent=str(getattr(response, "last_agent", "") or ""),
                domain_id=self._single_routed_domain(response),
                duration_ms=duration_ms,
                error_type=error_type,
                routed_domains=[
                    str(item)
                    for item in list(getattr(response, "routed_domains", None) or [])
                    if str(item).strip()
                ],
                specialists_used=self._specialists_used(response),
                response_mode=str(getattr(response, "response_mode", "answer") or "answer"),
                spans=[
                    QualitySpanSnapshot(
                        kind=str(getattr(item, "kind", "") or "agent"),
                        name=str(getattr(item, "name", "") or "unknown"),
                        status=str(getattr(item, "status", "") or "unknown"),
                        duration_ms=getattr(item, "duration_ms", None),
                        input_tokens=int(getattr(item, "input_tokens", 0) or 0),
                        output_tokens=int(getattr(item, "output_tokens", 0) or 0),
                        total_tokens=int(getattr(item, "total_tokens", 0) or 0),
                    )
                    for item in list(getattr(response, "quality_spans", None) or [])
                ],
                tools=[
                    ToolRunSnapshot(
                        tool_call_id=str(getattr(item, "tool_call_id", "") or ""),
                        tool_name=str(getattr(item, "tool_name", "unknown") or "unknown"),
                        agent_name=str(getattr(item, "agent_name", "") or ""),
                        status=str(getattr(item, "status", "unknown") or "unknown"),
                        duration_ms=getattr(item, "duration_ms", None),
                        arguments=dict(getattr(item, "arguments", None) or {}),
                    )
                    for item in list(getattr(response, "tool_runs", None) or [])
                ],
                citations=[
                    CitationSnapshot(
                        source_type=str(getattr(item, "source_type", "") or ""),
                        source_id=str(getattr(item, "source_id", "") or ""),
                        title=str(getattr(item, "title", "") or ""),
                        domain=str(getattr(item, "domain", "") or ""),
                        metadata=dict(getattr(item, "metadata", None) or {}),
                    )
                    for item in list(getattr(response, "citations", None) or [])
                ],
            ),
        )

    async def _record_completion_spans(
        self, turn: Any, value: TurnCompletion
    ) -> None:
        spans = [QualitySpanCreate(
            turn_id=turn.id, run_id=turn.run_id, kind=item.kind, name=item.name,
            status=item.status, duration_ms=item.duration_ms,
            input_tokens=item.input_tokens, output_tokens=item.output_tokens,
            total_tokens=item.total_tokens, metadata=item.metadata,
        ) for item in value.spans]
        if not any(item.kind == "agent" for item in spans):
            spans.append(QualitySpanCreate(
                turn_id=turn.id,
                run_id=turn.run_id,
                kind="agent",
                name=value.last_agent or "agent_run",
                status=value.status,
                duration_ms=value.duration_ms,
            ))
        for item in value.tools:
            spans.append(QualitySpanCreate(
                turn_id=turn.id,
                run_id=turn.run_id,
                kind="tool",
                name=item.tool_name or "unknown",
                status=item.status or "unknown",
                duration_ms=item.duration_ms,
            ))
        if not spans:
            return
        try:
            await self.repository.record_spans(spans)
        except Exception as exc:
            logger.warning(
                "Quality span batch failed turn_id=%s count=%s error_type=%s",
                turn.id,
                len(spans),
                type(exc).__name__,
            )

    async def _annotate_completion(self, turn: Any, value: TurnCompletion) -> None:
        annotations: list[QualityAnnotationCreate] = []
        citations = value.citations
        routed = value.routed_domains
        tools = [item.tool_name for item in value.tools]
        answer = value.answer or ""
        if value.status == "timeout":
            annotations.append(QualityAnnotationCreate(
                turn_id=turn.id, source="rule", code="timeout", severity="error"
            ))
        if value.status == "completed" and routed and not citations:
            annotations.append(QualityAnnotationCreate(
                turn_id=turn.id, source="rule", code="zero_citation", severity="error"
            ))
        duplicated = {name: count for name, count in Counter(tools).items() if name and count > 1}
        if duplicated:
            annotations.append(QualityAnnotationCreate(
                turn_id=turn.id,
                source="rule",
                code="duplicate_tool",
                severity="warning",
                confidence=1.0,
                details={"tools": duplicated},
            ))
        if (
            value.status == "completed"
            and routed
            and value.response_mode != "clarification"
            and any(marker in answer for marker in ("请问", "请提供", "请补充", "需要你确认"))
        ):
            annotations.append(QualityAnnotationCreate(
                turn_id=turn.id,
                source="rule",
                code="unexpected_clarification",
                severity="warning",
                confidence=0.85,
            ))
        for annotation in annotations:
            try:
                await self.repository.create_annotation(annotation)
            except Exception as exc:
                logger.warning(
                    "Quality annotation failed turn_id=%s code=%s error_type=%s",
                    turn.id,
                    annotation.code,
                    type(exc).__name__,
                )

    @staticmethod
    def _single_routed_domain(response: Any) -> str | None:
        routed = [
            str(item)
            for item in list(getattr(response, "routed_domains", None) or [])
            if str(item).strip()
        ]
        return routed[0] if len(routed) == 1 else None

    @staticmethod
    def _specialists_used(response: Any) -> list[str]:
        explicit = [
            str(item)
            for item in list(getattr(response, "specialists_used", None) or [])
            if str(item).strip()
        ]
        if explicit:
            return list(dict.fromkeys(explicit))
        specialist_names = {
            "approval_flow_expert",
            "workflow_expert",
            "metric_platform_expert",
            "bug_diagnosis_expert",
        }
        return list(
            dict.fromkeys(
                str(getattr(item, "tool_name", "") or "")
                for item in list(getattr(response, "tool_runs", None) or [])
                if str(getattr(item, "tool_name", "") or "") in specialist_names
            )
        )

    async def bind_reply(self, run_id: str, message_id: str) -> None:
        await self.repository.bind_channel_reply(run_id, message_id)
