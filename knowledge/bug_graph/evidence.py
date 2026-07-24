from __future__ import annotations

import asyncio
from typing import Any


class ContractEvidenceProvider:
    """Fetch supplemental contract evidence within a code-confirmed domain."""

    def __init__(
        self,
        *,
        registry: Any,
        swagger_inspector: Any | None,
        swagger_source_provider: Any | None,
        app_id: str = "middle-platform",
        top_k: int = 3,
    ) -> None:
        self.registry = registry
        self.swagger_inspector = swagger_inspector
        self.swagger_source_provider = swagger_source_provider
        self.app_id = app_id
        self.top_k = top_k

    async def enrich(
        self,
        state: dict[str, Any],
        code_matches: list[dict[str, Any]],
    ) -> dict[str, Any]:
        empty = {"swagger_operations": [], "document_matches": []}
        endpoints = [str(item) for item in state.get("log_endpoints", []) if item]
        domain_id = self._confirmed_domain(code_matches)
        if not endpoints or not domain_id:
            return empty

        query = " ".join(
            part
            for part in (str(state.get("normalized_problem") or ""), *endpoints)
            if part
        )
        return {
            "swagger_operations": await self._swagger(domain_id, query),
            "document_matches": await self._documents(domain_id, query),
        }

    @staticmethod
    def _confirmed_domain(code_matches: list[dict[str, Any]]) -> str | None:
        for item in code_matches:
            domain_id = str((item.get("metadata") or {}).get("domain_id") or "").strip()
            if domain_id and domain_id != "shared":
                return domain_id
        return None

    async def _documents(self, domain_id: str, query: str) -> list[dict[str, Any]]:
        try:
            pipeline = self.registry.get(self.app_id, None)
            result = await asyncio.to_thread(
                pipeline.search,
                query,
                20,
                20,
                self.top_k,
                {
                    "$and": [
                        {"$or": [{"domain_id": domain_id}, {"domain_id": "shared"}]},
                        {"source_type": "product_document"},
                    ]
                },
            )
        except Exception:
            return []
        if not result.rerank_applied:
            return []
        return [
            {
                "chunk_id": item.chunk_id,
                "heading": item.heading,
                "content": item.content,
                "domain": domain_id,
                "metadata": item.metadata,
            }
            for item in result.final_results[: self.top_k]
        ]

    async def _swagger(self, domain_id: str, query: str) -> list[dict[str, Any]]:
        if self.swagger_inspector is None or self.swagger_source_provider is None:
            return []
        try:
            sources = await self.swagger_source_provider.list_for_domain(domain_id)
        except Exception:
            return []
        operations: list[dict[str, Any]] = []
        for source in sources:
            try:
                result = await self.swagger_inspector.inspect(source, query, top_k=self.top_k)
            except Exception:
                continue
            for operation in result.get("operations") or []:
                operations.append(
                    {
                        **operation,
                        "source_id": source.source_id,
                        "domain": domain_id,
                        "refreshed_at": str(result.get("refreshed_at") or ""),
                        "stale": bool(result.get("stale", False)),
                    }
                )
        return operations[: self.top_k]
