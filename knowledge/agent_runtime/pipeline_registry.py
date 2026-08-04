from __future__ import annotations

from collections.abc import Callable
import logging
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


logger = logging.getLogger(__name__)


class RetrievalPipelineRegistry:
    """Cache one BM25-backed retrieval pipeline for each trusted domain scope."""

    def __init__(
        self,
        settings: Settings | None = None,
        repository: Any | None = None,
        pipeline_builder: Callable[[str, str | None], Any] | None = None,
        stale_while_refresh_enabled: bool | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.query_rewriter = None
        self._pipelines: dict[tuple[str, str], Any] = {}
        self._build_locks: dict[tuple[str, str], Lock] = {}
        self._invalidation_generation = 0
        self._lock = Lock()
        self._warm_state = "warming"
        self.stale_while_refresh_enabled = (
            settings.bm25_stale_while_refresh_enabled
            if stale_while_refresh_enabled is None and settings is not None
            else (
                True
                if stale_while_refresh_enabled is None
                else stale_while_refresh_enabled
            )
        )

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
                memory_filter_enabled=settings.bm25_memory_filter_enabled,
            )
            return MultiRouteRetrievalService(
                self.repository,
                keyword_service,
                query_rewriter=query_rewriter,
                hybrid_ranker=HybridRerankService(reranker=reranker),
                parallel_routes_enabled=settings.retrieval_parallel_routes_enabled,
            )

        self._pipeline_builder = build

    def get(self, app_id: str, domain: str | None) -> Any:
        normalized_app_id = app_id.strip()
        normalized_domain = domain.strip() if domain and domain.strip() else None
        if not normalized_app_id:
            raise ValueError("app_id is required")
        key = (normalized_app_id, normalized_domain or "")
        while True:
            with self._lock:
                pipeline = self._pipelines.get(key)
                if pipeline is not None:
                    return pipeline
                build_lock = self._build_locks.setdefault(key, Lock())

            with build_lock:
                with self._lock:
                    pipeline = self._pipelines.get(key)
                    if pipeline is not None:
                        return pipeline
                    generation = self._invalidation_generation

                pipeline = self._pipeline_builder(
                    normalized_app_id,
                    normalized_domain,
                )

                with self._lock:
                    existing = self._pipelines.get(key)
                    if existing is not None:
                        return existing
                    if generation != self._invalidation_generation:
                        continue
                    self._pipelines[key] = pipeline
                    return pipeline

    def warm(self, scopes: list[tuple[str, str]]) -> None:
        with self._lock:
            self._warm_state = "warming"
        try:
            for app_id, domain in scopes:
                self.get(app_id, domain)
        except Exception:
            with self._lock:
                self._warm_state = "unavailable"
            raise
        with self._lock:
            self._warm_state = "available"

    def warm_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self._warm_state,
                "cached_pipelines": len(self._pipelines),
            }

    def refresh(
        self,
        *,
        app_id: str | None = None,
        domain: str | None = None,
    ) -> int:
        """Build replacements without blocking reads and atomically publish them."""
        if not self.stale_while_refresh_enabled:
            return self.invalidate(app_id=app_id, domain=domain)

        normalized_app_id = app_id.strip() if app_id and app_id.strip() else None
        normalized_domain = domain.strip() if domain and domain.strip() else None
        with self._lock:
            snapshots = {
                key: pipeline
                for key, pipeline in self._pipelines.items()
                if normalized_app_id is None
                or (
                    key[0] == normalized_app_id
                    and (domain is None or key[1] == (normalized_domain or ""))
                )
            }
        if not snapshots:
            return 0

        try:
            replacements = {
                key: self._pipeline_builder(key[0], key[1] or None)
                for key in snapshots
            }
        except Exception as exc:
            with self._lock:
                self._warm_state = "unavailable"
            logger.warning(
                "Retrieval pipeline refresh failed error_type=%s",
                type(exc).__name__,
            )
            return 0

        with self._lock:
            replaced = 0
            for key, replacement in replacements.items():
                if self._pipelines.get(key) is snapshots[key]:
                    self._pipelines[key] = replacement
                    replaced += 1
            self._warm_state = "available"
            return replaced

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
            self._invalidation_generation += 1
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
