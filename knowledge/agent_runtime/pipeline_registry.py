from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

from knowledge.config.settings import Settings
from knowledge.repositories.vector_store_factory import create_vector_store_repository
from knowledge.services.hybrid_rerank_service import HybridRerankService
from knowledge.services.keyword_retrieval_service import KeywordRetrievalService
from knowledge.services.multi_route_retrieval_service import MultiRouteRetrievalService
from knowledge.services.retrieval_pipeline_factory import (
    create_query_rewriter,
    create_reranker,
)


class RetrievalPipelineRegistry:
    """Cache one BM25-backed retrieval pipeline for each trusted domain scope."""

    def __init__(
        self,
        settings: Settings | None = None,
        repository: Any | None = None,
        pipeline_builder: Callable[[str, str | None], Any] | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.query_rewriter = None
        self._pipelines: dict[tuple[str, str], Any] = {}
        self._lock = Lock()

        if pipeline_builder is not None:
            self._pipeline_builder = pipeline_builder
            return
        if settings is None:
            raise ValueError("settings or pipeline_builder is required")

        self.repository = repository or create_vector_store_repository(settings)
        query_rewriter = create_query_rewriter(settings)
        self.query_rewriter = query_rewriter
        reranker = create_reranker(settings)

        def build(app_id: str, domain: str | None) -> MultiRouteRetrievalService:
            keyword_service = KeywordRetrievalService(
                self.repository,
                app_id=app_id,
                domain=domain,
                title_weight=settings.bm25_title_weight,
                keywords_weight=settings.bm25_keywords_weight,
            )
            return MultiRouteRetrievalService(
                self.repository,
                keyword_service,
                query_rewriter=query_rewriter,
                hybrid_ranker=HybridRerankService(reranker=reranker),
            )

        self._pipeline_builder = build

    def get(self, app_id: str, domain: str | None) -> Any:
        normalized_app_id = app_id.strip()
        normalized_domain = domain.strip() if domain and domain.strip() else None
        if not normalized_app_id:
            raise ValueError("app_id is required")
        key = (normalized_app_id, normalized_domain or "")
        with self._lock:
            pipeline = self._pipelines.get(key)
            if pipeline is None:
                pipeline = self._pipeline_builder(normalized_app_id, normalized_domain)
                self._pipelines[key] = pipeline
            return pipeline

    def warm(self, scopes: list[tuple[str, str]]) -> None:
        for app_id, domain in scopes:
            self.get(app_id, domain)

    def invalidate(
        self,
        *,
        app_id: str | None = None,
        domain: str | None = None,
    ) -> int:
        """Atomically evict cached BM25 pipelines after an index update."""
        normalized_app_id = app_id.strip() if app_id and app_id.strip() else None
        normalized_domain = domain.strip() if domain and domain.strip() else None
        with self._lock:
            if normalized_app_id is None:
                removed = len(self._pipelines)
                self._pipelines.clear()
                return removed
            keys = [
                key
                for key in self._pipelines
                if key[0] == normalized_app_id
                and (domain is None or key[1] == (normalized_domain or ""))
            ]
            for key in keys:
                del self._pipelines[key]
            return len(keys)

    def close(self) -> None:
        close = getattr(self.repository, "close", None)
        if callable(close):
            close()
