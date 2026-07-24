from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from knowledge.memory.extractor import ExtractedMemory, MemoryExtractor
from knowledge.memory.models import Memory, MemoryCandidate
from knowledge.memory.models import MemoryCandidateCreate
from knowledge.memory.promotions import DomainPromotionValidator
from knowledge.memory.repository import MemoryRepository


class MemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        *,
        extractor: MemoryExtractor | None = None,
        max_recall: int = 5,
        candidate_ttl_seconds: int = 7 * 24 * 3600,
        index: Any | None = None,
        default_retention_days: int = 180,
        auto_confirm_seconds: int = 24 * 3600,
    ):
        self.repository = repository
        self.extractor = extractor
        self.max_recall = max(1, min(max_recall, 20))
        self.candidate_ttl_seconds = candidate_ttl_seconds
        self.index = index
        self.default_retention_days = max(1, default_retention_days)
        self.auto_confirm_seconds = max(1, auto_confirm_seconds)

    async def initialize(self) -> None:
        await self.repository.initialize()
        await self.repository.expire_memories()

    async def recall(
        self,
        query: str,
        *,
        user_id: str | None,
        space_id: str,
        domain_id: str | None,
    ) -> list[Memory]:
        records: list[Memory] = []
        if user_id:
            records.extend(
                await self.repository.list_memories(
                    scope_type="user", owner_id=user_id, space_id=space_id
                )
            )
        if domain_id:
            records.extend(
                await self.repository.list_memories(
                    scope_type="domain", owner_id=domain_id,
                    space_id=space_id, domain_id=domain_id,
                )
            )
        terms = self._terms(query)
        indexed_ids: set[str] = set()
        if self.index is not None:
            try:
                if user_id:
                    indexed_ids.update(await asyncio.to_thread(
                        self.index.search,
                        query,
                        scope_type="user",
                        owner_id=user_id,
                        space_id=space_id,
                        domain_id=domain_id,
                        limit=self.max_recall,
                    ))
                if domain_id:
                    indexed_ids.update(await asyncio.to_thread(
                        self.index.search,
                        query,
                        scope_type="domain",
                        owner_id=domain_id,
                        space_id=space_id,
                        domain_id=domain_id,
                        limit=self.max_recall,
                    ))
            except Exception:
                indexed_ids.clear()
        ranked: list[tuple[int, Memory]] = []
        seen: set[str] = set()
        for item in records:
            if item.id in seen:
                continue
            seen.add(item.id)
            haystack = f"{item.subject} {item.normalized_fact} {item.summary}".casefold()
            score = sum(1 for term in terms if term.casefold() in haystack)
            if item.id in indexed_ids:
                score += 2
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (-pair[0], -pair[1].confidence, pair[1].updated_at),)
        return [item for _, item in ranked[: self.max_recall]]

    async def augment_message(
        self,
        message: str,
        *,
        user_id: str | None,
        conversation_id: str | None,
        space_id: str,
        domain_id: str | None,
    ) -> str:
        blocks: list[str] = []
        if conversation_id:
            summary = await self.repository.get_conversation_summary(conversation_id)
            if (
                summary is not None
                and summary.user_id == user_id
                and summary.space_id == space_id
            ):
                blocks.append(f"历史会话摘要（仅作上下文，不是知识库证据）：\n{summary.summary[:2000]}")
        memories = await self.recall(
            message,
            user_id=user_id,
            space_id=space_id,
            domain_id=domain_id,
        )
        memory_lines = [
            f"- [{item.memory_type}] {' '.join(str(item.summary).split())[:500]}"
            for item in memories[:5]
        ]
        if memory_lines:
            blocks.append(
                "已确认的长期记忆（只能作为用户背景，不能替代知识库证据）：\n"
                + "\n".join(memory_lines)
            )
        if not blocks:
            return message
        return "\n\n".join(blocks) + f"\n\n当前问题：\n{message}"

    async def delete_conversation_summary(self, conversation_id: str) -> bool:
        return await self.repository.delete_conversation_summary(conversation_id)

    async def extract_candidates(
        self,
        *,
        question: str,
        answer: str | None,
        user_id: str,
        space_id: str,
        domain_id: str | None,
        source_turn_id: str | None,
        source_citations: tuple[str, ...] = (),
    ) -> list[MemoryCandidate]:
        if self.extractor is None or not user_id.strip():
            return []
        extracted = await self.extractor.extract(question, answer, domain_id)
        output: list[MemoryCandidate] = []
        expires = datetime.now(UTC) + timedelta(seconds=self.candidate_ttl_seconds)
        for item in extracted:
            scope_type = item.scope_type
            if scope_type not in {"user", "domain"}:
                continue
            owner_id = user_id if scope_type == "user" else (domain_id or user_id)
            if scope_type == "domain" and not domain_id:
                continue
            candidate = await self.repository.create_candidate(
                self._candidate_create(
                    item, owner_id=owner_id, space_id=space_id,
                    domain_id=domain_id, source_turn_id=source_turn_id,
                    source_citations=source_citations, expires_at=expires,
                )
            )
            output.append(candidate)
        return output

    async def approve_candidate(self, candidate_id: str, *, actor: str = "admin") -> Memory:
        memory = await self.repository.approve_candidate(
            candidate_id,
            actor=actor,
            valid_until=datetime.now(UTC) + timedelta(days=self.default_retention_days),
        )
        if self.index is not None:
            try:
                await asyncio.to_thread(self.index.upsert, memory)
            except Exception as exc:
                await self.repository.enqueue_index_repair(
                    memory.id, "upsert", type(exc).__name__
                )
        return memory

    async def auto_confirm_due_candidates(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(seconds=self.auto_confirm_seconds)
        candidates = await self.repository.list_due_user_candidates(cutoff, limit=limit)
        confirmed: list[Memory] = []
        for candidate in candidates:
            try:
                confirmed.append(await self.approve_candidate(
                    candidate.id,
                    actor="system:auto-confirm",
                ))
            except (KeyError, ValueError):
                # A concurrent user/admin action may resolve the candidate first.
                continue
        return confirmed

    async def reject_candidate(self, candidate_id: str, *, actor: str = "admin") -> MemoryCandidate:
        return await self.repository.reject_candidate(candidate_id, actor=actor)

    async def recall_procedures(
        self, *, user_id: str, domain_id: str | None, task_type: str,
        environment: str, branch: str, limit: int = 3,
    ):
        return await self.repository.list_matching_procedures(
            owner_id=user_id, domain_id=domain_id, task_type=task_type,
            environment=environment, branch=branch, limit=limit,
        )

    async def request_domain_promotion(
        self, *, source_memory_id: str, target_domain_id: str,
        public_summary: str, requested_by: str, valid_until: datetime | None,
    ):
        source = await self.repository.get_memory(source_memory_id)
        if source is None:
            raise KeyError(source_memory_id)
        summary = DomainPromotionValidator().validate(
            source, target_domain_id, public_summary
        )
        candidate = await self.repository.create_candidate(MemoryCandidateCreate(
            scope_type="domain", owner_id=target_domain_id,
            space_id=source.space_id, domain_id=target_domain_id,
            memory_type=source.memory_type, subject=source.subject,
            normalized_fact=summary, summary=summary,
            source_turn_id=source.source_turn_id,
            source_citations=source.source_citations,
            confidence=source.confidence, expires_at=valid_until,
        ))
        spec = await self.repository.get_procedural_spec(source.id)
        if spec is not None:
            await self.repository.upsert_procedural_spec(candidate.id, spec)
        return await self.repository.create_domain_promotion(
            source_memory_id=source.id, target_candidate_id=candidate.id,
            target_domain_id=target_domain_id, public_summary=summary,
            requested_by=requested_by, valid_until=valid_until,
        )

    async def approve_domain_promotion(self, promotion_id: str, *, actor: str):
        promotion = await self.repository.get_domain_promotion(promotion_id)
        if promotion.state != "pending" or not promotion.target_candidate_id:
            raise ValueError("domain promotion is no longer pending")
        memory = await self.repository.approve_candidate(
            promotion.target_candidate_id, actor=actor,
            valid_until=promotion.valid_until,
        )
        if self.index is not None:
            await asyncio.to_thread(self.index.upsert, memory)
        await self.repository.review_domain_promotion(
            promotion_id, state="approved", reviewed_by=actor,
        )
        return memory

    async def reject_domain_promotion(self, promotion_id: str, *, actor: str):
        promotion = await self.repository.get_domain_promotion(promotion_id)
        if promotion.target_candidate_id:
            try:
                await self.repository.reject_candidate(
                    promotion.target_candidate_id, actor=actor
                )
            except ValueError:
                pass
        return await self.repository.review_domain_promotion(
            promotion_id, state="rejected", reviewed_by=actor,
        )

    async def record_memory_conflict(
        self, memory_id: str, reason_code: str, *, threshold: int = 2
    ) -> int:
        return await self.repository.record_memory_conflict(
            memory_id, reason_code, threshold=threshold
        )

    async def repair_memory_index(self, limit: int = 100) -> int:
        if self.index is None:
            return 0
        completed = 0
        for memory_id, operation in await self.repository.list_index_repairs(limit):
            try:
                if operation == "delete":
                    await asyncio.to_thread(self.index.delete, memory_id)
                else:
                    memory = await self.repository.get_memory(memory_id)
                    if memory is None:
                        await asyncio.to_thread(self.index.delete, memory_id)
                    else:
                        await asyncio.to_thread(self.index.upsert, memory)
                await self.repository.complete_index_repair(memory_id)
                completed += 1
            except Exception:
                continue
        return completed

    async def forget(self, memory_id: str, *, actor: str = "user") -> bool:
        deleted = await self.repository.soft_delete_memory(memory_id, actor=actor)
        if deleted and self.index is not None:
            await asyncio.to_thread(self.index.delete, memory_id)
        return deleted

    @staticmethod
    def _terms(query: str) -> tuple[str, ...]:
        compact = "".join(query.casefold().split())
        if len(compact) <= 2:
            return (compact,)
        return tuple(dict.fromkeys(
            [compact] + [compact[index:index + 2] for index in range(len(compact) - 1)]
        ))

    @staticmethod
    def _candidate_create(
        item: ExtractedMemory, *, owner_id: str, space_id: str,
        domain_id: str | None, source_turn_id: str | None,
        source_citations: tuple[str, ...], expires_at,
    ):
        from knowledge.memory.models import MemoryCandidateCreate

        return MemoryCandidateCreate(
            scope_type=item.scope_type, owner_id=owner_id, space_id=space_id,
            domain_id=domain_id, memory_type=item.memory_type,
            subject=item.subject, normalized_fact=item.normalized_fact,
            summary=item.summary, source_turn_id=source_turn_id,
            source_citations=source_citations, confidence=item.confidence,
            expires_at=expires_at,
        )
