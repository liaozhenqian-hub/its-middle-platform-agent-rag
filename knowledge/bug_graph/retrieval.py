from __future__ import annotations

import asyncio
from typing import Any

from knowledge.bug_graph.models import BugDiagnosisState
from knowledge.logs.grafana import TraceLogResult


class PipelineBugCodeRetriever:
    def __init__(
        self,
        registry: Any,
        *,
        app_id: str = "middle-platform",
        top_k: int = 5,
        min_rerank_score: float = 0.35,
        keyword_k: int = 20,
        vector_k: int = 20,
    ) -> None:
        self.registry = registry
        self.app_id = app_id
        self.top_k = top_k
        self.min_rerank_score = min_rerank_score
        self.keyword_k = keyword_k
        self.vector_k = vector_k

    async def search(
        self,
        state: BugDiagnosisState,
        log_result: TraceLogResult,
    ) -> list[dict[str, Any]]:
        branch = log_result.code_branch
        for symbol in self._symbol_candidates(state):
            chunks = await asyncio.to_thread(
                self.registry.repository.get_chunks,
                {
                    "$and": [
                        {"source_type": "code"},
                        {"branch": branch},
                        {"symbol_name": symbol},
                    ]
                },
                None,
            )
            if chunks:
                return [
                    {
                        "chunk_id": chunk.chunk_id,
                        "heading": chunk.heading,
                        "content": chunk.content,
                        "domain": str(chunk.metadata.get("domain_id") or "中台"),
                        "metadata": chunk.metadata,
                        "match_type": "exact_symbol",
                    }
                    for chunk in chunks[: self.top_k]
                ]

        pipeline = self.registry.get(self.app_id, None)
        where = {
            "$and": [
                {"source_type": "code"},
                {"branch": branch},
            ]
        }
        query = self._retrieval_query(state)
        result = await asyncio.to_thread(
            pipeline.search,
            query,
            self.keyword_k,
            self.vector_k,
            self.top_k,
            where,
        )
        if not result.rerank_applied:
            return []
        accepted = [
            item
            for item in result.final_results
            if item.rerank_score is not None
            and item.rerank_score >= self.min_rerank_score
        ]
        return [
            {
                "chunk_id": item.chunk_id,
                "heading": item.heading,
                "content": item.content,
                "domain": str(item.metadata.get("domain_id") or "中台"),
                "metadata": item.metadata,
                "match_type": "hybrid_rerank",
                "rerank_score": item.rerank_score,
            }
            for item in accepted[: self.top_k]
        ]

    async def enrich(
        self,
        state: BugDiagnosisState,
        matches: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for match in matches:
            item = dict(match)
            metadata = dict(item.get("metadata") or {})
            relative_path = str(metadata.get("relative_path") or "").strip()
            branch = str(metadata.get("branch") or "").strip()
            symbol_name = str(metadata.get("symbol_name") or item.get("heading") or "")
            container_name = symbol_name.rsplit(".", 1)[0] if "." in symbol_name else symbol_name
            context_chunks: list[dict[str, Any]] = []
            if relative_path and branch and container_name:
                chunks = await asyncio.to_thread(
                    self.registry.repository.get_chunks,
                    {
                        "$and": [
                            {"source_type": "code"},
                            {"branch": branch},
                            {"relative_path": relative_path},
                        ]
                    },
                    None,
                )
                for chunk in chunks:
                    chunk_metadata = dict(chunk.metadata or {})
                    if (
                        str(chunk_metadata.get("symbol_name") or "") == container_name
                        and str(chunk_metadata.get("symbol_type") or "")
                        in {"class", "interface", "enum", "annotation"}
                    ):
                        context_chunks.append(
                            {
                                "chunk_id": chunk.chunk_id,
                                "heading": chunk.heading,
                                "content": chunk.content,
                                "metadata": chunk_metadata,
                            }
                        )
                        if len(context_chunks) >= 2:
                            break
            context_metadata = context_chunks[0]["metadata"] if context_chunks else metadata
            item["context_chunks"] = context_chunks
            item["structural_context"] = {
                key: context_metadata[key]
                for key in ("imports", "extends", "implements", "annotations", "calls")
                if context_metadata.get(key)
            }
            enriched.append(item)
        return enriched

    @staticmethod
    def _symbol_candidates(state: BugDiagnosisState) -> list[str]:
        candidates: list[str] = []
        for frame in state.get("stack_frames", []):
            symbol = str(frame.get("symbol") or "").strip()
            if not symbol:
                continue
            parts = symbol.split(".")
            candidates.append(symbol)
            if len(parts) >= 2:
                candidates.append(".".join(parts[-2:]))
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _retrieval_query(state: BugDiagnosisState) -> str:
        parts = [str(state.get("normalized_problem") or "")]
        parts.extend(str(item) for item in state.get("exception_types", []))
        for frame in state.get("stack_frames", []):
            parts.extend(
                str(frame.get(key) or "")
                for key in ("symbol", "file", "line")
            )
        parts.extend(str(item) for item in state.get("entity_hints", []))
        return " ".join(part for part in parts if part).strip()
