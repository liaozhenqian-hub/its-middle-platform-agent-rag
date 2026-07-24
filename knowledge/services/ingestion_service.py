from dataclasses import replace
from pathlib import Path

from knowledge.loaders.markdown_loader import MarkdownKnowledgeLoader
from knowledge.repositories.vector_store_repository import VectorStoreRepository
from knowledge.schemas.documents import IngestionSummary


class IngestionService:
    def __init__(
        self,
        repository: VectorStoreRepository | None,
        source_path: str | Path,
        max_chunk_chars: int,
        overlap_chars: int,
        app_id: str,
        domain: str | None = None,
        name: str | None = None,
    ):
        normalized_app_id = app_id.strip()
        if not normalized_app_id:
            raise ValueError("app_id is required")
        self.repository = repository
        self.source_path = Path(source_path)
        self.scope_metadata = {
            key: value
            for key, value in {
                "app_id": normalized_app_id,
                "domain": domain.strip() if domain and domain.strip() else None,
                "name": name.strip() if name and name.strip() else None,
            }.items()
            if value is not None
        }
        self.loader = MarkdownKnowledgeLoader(
            max_chunk_chars=max_chunk_chars,
            overlap_chars=overlap_chars,
        )

    def dry_run(self) -> IngestionSummary:
        result = self.loader.load(self.source_path)
        return IngestionSummary(
            source_path=str(self.source_path),
            chunk_count=len(result.chunks),
            parent_chunk_count=len({chunk.metadata["parent_chunk_id"] for chunk in result.chunks}),
            stored_count=0,
        )

    def ingest(self, reset: bool = False) -> IngestionSummary:
        if self.repository is None:
            raise ValueError("repository is required for ingest")
        result = self.loader.load(self.source_path)
        # Reset is intentionally disabled after the initial successful ingest.
        # Re-running reset would delete existing vectors and call the paid
        # embedding API again. Re-enable only when the knowledge source changes.
        # if reset:
        #     self.repository.reset()
        scoped_chunks = [
            replace(
                chunk,
                metadata={**chunk.metadata, **self.scope_metadata},
            )
            for chunk in result.chunks
        ]
        self.repository.upsert(scoped_chunks)
        return IngestionSummary(
            source_path=str(self.source_path),
            chunk_count=len(result.chunks),
            parent_chunk_count=len({chunk.metadata["parent_chunk_id"] for chunk in result.chunks}),
            stored_count=self.repository.count(),
        )
