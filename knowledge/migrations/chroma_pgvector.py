from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from knowledge.schemas.documents import KnowledgeChunk


@dataclass(frozen=True)
class VectorMigrationResult:
    collection_name: str
    processed_count: int
    last_offset: int


class ChromaPgvectorMigrator:
    def __init__(
        self,
        source_collection: Any,
        target_repository: Any,
        *,
        collection_name: str,
        batch_size: int = 500,
        expected_dimensions: int = 1024,
        checkpoint: Callable[[int, int], None] | None = None,
    ) -> None:
        self.source_collection = source_collection
        self.target_repository = target_repository
        self.collection_name = collection_name
        self.batch_size = batch_size
        self.expected_dimensions = expected_dimensions
        self.checkpoint = checkpoint

    def run(self, *, start_offset: int = 0) -> VectorMigrationResult:
        bulk_import = getattr(self.target_repository, "bulk_import", None)
        if callable(bulk_import):
            with bulk_import() as target:
                return self._run(target, start_offset=start_offset)
        return self._run(self.target_repository, start_offset=start_offset)

    def _run(self, target_repository: Any, *, start_offset: int) -> VectorMigrationResult:
        offset = start_offset
        processed = 0
        while True:
            records = self.source_collection.get(
                include=["documents", "metadatas", "embeddings"],
                limit=self.batch_size,
                offset=offset,
            )
            ids = list(records.get("ids") or [])
            if not ids:
                break
            documents = list(records.get("documents") or [])
            metadatas = list(records.get("metadatas") or [])
            raw_embeddings = records.get("embeddings")
            embeddings = [] if raw_embeddings is None else list(raw_embeddings)
            if not (
                len(ids) == len(documents) == len(metadatas) == len(embeddings)
            ):
                raise ValueError("Chroma migration page fields have different lengths")
            chunks: list[KnowledgeChunk] = []
            normalized_embeddings: list[list[float]] = []
            for record_id, content, metadata, embedding in zip(
                ids, documents, metadatas, embeddings
            ):
                vector = list(embedding)
                if len(vector) != self.expected_dimensions:
                    raise ValueError("embedding dimension mismatch in migration batch")
                raw_metadata = dict(metadata or {})
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=str(raw_metadata.get("chunk_id") or record_id),
                        heading=str(raw_metadata.get("heading") or ""),
                        content=str(content or ""),
                        metadata=raw_metadata,
                    )
                )
                normalized_embeddings.append(vector)
            try:
                target_repository.upsert_with_embeddings(
                    chunks,
                    normalized_embeddings,
                )
            except Exception:
                raise RuntimeError("vector batch write failed") from None
            page_count = len(ids)
            offset += page_count
            processed += page_count
            if self.checkpoint is not None:
                self.checkpoint(offset, processed)
        return VectorMigrationResult(
            collection_name=self.collection_name,
            processed_count=processed,
            last_offset=offset,
        )
