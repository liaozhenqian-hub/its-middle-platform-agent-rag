from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from knowledge.memory.models import MemoryCandidate, MemoryCandidateCreate
from knowledge.memory.repository import MemoryRepository
from knowledge.memory.entities import EntityMemoryRepository
from knowledge.memory.procedures import build_bug_diagnosis_spec


class BugIncidentMemoryRecorder:
    _DOMAIN_ALIASES = {
        "approval-flow": "approval-flow",
        "审批流": "approval-flow",
        "workflow": "workflow",
        "工作流": "workflow",
        "metric-platform": "metric-platform",
        "指标平台": "metric-platform",
    }
    _TRACE_PATTERN = re.compile(
        r"(?:trace\s*id\s*[:=]?\s*)?[A-Za-z0-9_-]{12,}", re.IGNORECASE
    )

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        candidate_ttl_seconds: int,
        entity_repository: EntityMemoryRepository | None = None,
        procedural_enabled: bool = True,
    ):
        self.repository = repository
        self.candidate_ttl_seconds = max(60, candidate_ttl_seconds)
        self.entity_repository = entity_repository
        self.procedural_enabled = procedural_enabled

    async def record(
        self, user_id: str | None, state: dict[str, Any], result: Any
    ) -> MemoryCandidate | None:
        if (
            not user_id
            or result.status != "completed"
            or result.evidence_grade not in {"correlated", "contract_supported"}
        ):
            return None
        environment = str(state.get("environment") or "unknown")
        branch = "master" if environment == "prod" else "develop"
        service = self._bounded(state.get("service"), 120) or "中台服务"
        endpoint = self._bounded(state.get("endpoint"), 300)
        exceptions = [
            self._bounded(item, 120)
            for item in list(state.get("exception_types") or [])[:5]
            if self._bounded(item, 120)
        ]
        citation_ids = tuple(
            dict.fromkeys(
                str(item.get("source_id") or "")
                for item in result.citations
                if item.get("source_type") in {"code", "product_document", "swagger"}
                and item.get("source_id")
            )
        )[:20]
        if not citation_ids:
            return None
        evidence = tuple(
            (str(item.get("source_type") or ""), str(item.get("source_id") or ""))
            for item in result.citations
            if item.get("source_type") in {"code", "product_document", "swagger"}
            and item.get("source_id")
        )[:20]
        domain_id = self._domain_id(state, result.citations)
        if self.entity_repository is not None and endpoint:
            await self._record_entities(
                user_id=user_id,
                domain_id=domain_id,
                environment=environment,
                branch=branch,
                service=service,
                endpoint=endpoint,
                evidence=evidence,
            )
        if self.procedural_enabled:
            await self._record_procedure(
                user_id=user_id,
                domain_id=domain_id,
                environment=environment,
                branch=branch,
                service=service,
                citation_ids=citation_ids,
                run_id=self._bounded(state.get("run_id"), 200) or None,
            )
        fact = {
            "environment": environment,
            "branch": branch,
            "service": service,
            "endpoint": endpoint or None,
            "exception_types": exceptions,
            "evidence_grade": result.evidence_grade,
        }
        identity = json.dumps(fact, ensure_ascii=False, sort_keys=True)
        candidate_id = "incident-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        try:
            existing = await self.repository.get_candidate(candidate_id)
            return existing if existing.status == "candidate" else None
        except KeyError:
            pass
        target = f"{service}{' ' + endpoint if endpoint else ''}"
        exception_text = "、".join(exceptions) if exceptions else "异常"
        summary = (
            f"{environment} 环境（{branch} 分支）{target} 出现 {exception_text}，"
            f"已有 {result.evidence_grade} 级代码/契约证据。确认修复后可用于相似问题排查。"
        )
        return await self.repository.create_candidate(MemoryCandidateCreate(
            id=candidate_id,
            scope_type="user",
            owner_id=user_id,
            space_id="middle-platform",
            domain_id=domain_id,
            memory_type="episodic_memory",
            subject=f"bug:{target}"[:100],
            normalized_fact=identity[:1000],
            summary=summary[:1000],
            source_turn_id=self._bounded(state.get("run_id"), 200) or None,
            source_citations=citation_ids,
            confidence=0.9 if result.evidence_grade == "contract_supported" else 0.8,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.candidate_ttl_seconds),
        ))

    async def _record_procedure(
        self,
        *,
        user_id: str,
        domain_id: str | None,
        environment: str,
        branch: str,
        service: str,
        citation_ids: tuple[str, ...],
        run_id: str | None,
    ) -> None:
        steps = [
            "确认用户环境并映射到固定代码分支",
            "按 trace ID 查询脱敏日志（最近 24 小时）",
            "从异常类型、堆栈符号和接口路径提取检索线索",
            "先精确检索符号，再执行混合代码检索",
            "结合代码和接口契约证据形成诊断并验证修复",
        ]
        procedure_key = f"{domain_id or service}|{environment}|{branch}"
        candidate_id = "procedure-" + hashlib.sha256(
            procedure_key.encode("utf-8")
        ).hexdigest()[:24]
        try:
            await self.repository.get_candidate(candidate_id)
            return
        except KeyError:
            pass
        candidate = await self.repository.create_candidate(MemoryCandidateCreate(
            id=candidate_id,
            scope_type="user",
            owner_id=user_id,
            space_id="middle-platform",
            domain_id=domain_id,
            memory_type="procedural_memory",
            subject=f"bug-diagnosis:{domain_id or service}"[:100],
            normalized_fact=json.dumps(
                {
                    "environment": environment,
                    "branch": branch,
                    "steps": steps,
                },
                ensure_ascii=False,
            )[:1000],
            summary=(f"{environment} 环境 Bug 标准排障流程：" + "；".join(steps))[:1000],
            source_turn_id=run_id,
            source_citations=citation_ids,
            confidence=0.85,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.candidate_ttl_seconds),
        ))
        await self.repository.upsert_procedural_spec(
            candidate.id,
            build_bug_diagnosis_spec(environment=environment, branch=branch),
        )

    async def _record_entities(
        self,
        *,
        user_id: str,
        domain_id: str | None,
        environment: str,
        branch: str,
        service: str,
        endpoint: str,
        evidence: tuple[tuple[str, str], ...],
    ) -> None:
        source = await self.entity_repository.upsert_entity(
            scope_type="user",
            owner_id=user_id,
            space_id="middle-platform",
            domain_id=domain_id,
            entity_type="service",
            canonical_name=service,
            branch=branch,
            environment=environment,
        )
        target = await self.entity_repository.upsert_entity(
            scope_type="user",
            owner_id=user_id,
            space_id="middle-platform",
            domain_id=domain_id,
            entity_type="endpoint",
            canonical_name=endpoint,
            branch=branch,
            environment=environment,
        )
        await self.entity_repository.upsert_relation(
            source_entity_id=source.id,
            target_entity_id=target.id,
            relation_type="serves_endpoint",
            summary=f"{service} 提供 {endpoint} 接口",
            evidence=evidence,
            confidence=0.9,
        )

    @classmethod
    def _domain_id(cls, state: dict[str, Any], citations: list[dict[str, Any]]) -> str | None:
        candidates = list(state.get("domain_hints") or []) + [
            item.get("domain") for item in citations
        ]
        for value in candidates:
            normalized = cls._DOMAIN_ALIASES.get(str(value or "").strip())
            if normalized:
                return normalized
        return None

    @staticmethod
    def _bounded(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]
