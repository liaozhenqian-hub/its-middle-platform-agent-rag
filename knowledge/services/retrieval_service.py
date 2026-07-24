from typing import Any

from knowledge.repositories.vector_store_repository import VectorStoreRepository
from knowledge.schemas.documents import SearchResult


class RetrievalService:
    def __init__(self, repository: VectorStoreRepository):
        self.repository = repository

    def search(
        self,
        query: str,
        k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        return self.repository.search(query=query, k=k, where=where)

