from __future__ import annotations

from typing import Any

from knowledge.memory.models import Memory
from knowledge.schemas.documents import KnowledgeChunk


class MemoryIndex:
    """Separate Chroma collection adapter for confirmed memories only."""

    def __init__(self, vector_repository: Any):
        self.repository = vector_repository

    def upsert(self, memory: Memory) -> None:
        if memory.status != "confirmed":
            return
        chunk = KnowledgeChunk(
            chunk_id=memory.id,
            heading=memory.subject,
            content=memory.summary,
            metadata={
                "chunk_id": memory.id,
                "source_type": "memory",
                "scope_type": memory.scope_type,
                "owner_id": memory.owner_id,
                "space_id": memory.space_id,
                "domain_id": memory.domain_id or "",
                "memory_type": memory.memory_type,
                "confidence": memory.confidence,
            },
        )
        self.repository.upsert([chunk])

    def delete(self, memory_id: str) -> None:
        self.repository.delete([memory_id])

    def search(
        self,
        query: str,
        *,
        scope_type: str,
        owner_id: str,
        space_id: str,
        domain_id: str | None = None,
        limit: int = 5,
    ) -> list[str]:
        clauses: list[dict[str, Any]] = [
            {"source_type": "memory"},
            {"scope_type": scope_type},
            {"owner_id": owner_id},
            {"space_id": space_id},
        ]
        if domain_id is not None:
            clauses.append({"domain_id": domain_id})
        results = self.repository.search(
            query,
            k=max(1, min(limit, 20)),
            where={"$and": clauses},
        )
        return [str(item.chunk_id) for item in results if getattr(item, "chunk_id", "")]
