from __future__ import annotations

from typing import Any

from langchain_openai import OpenAIEmbeddings

from knowledge.config.settings import Settings, get_settings
from knowledge.repositories.postgres_vector_store_repository import (
    PostgresVectorStoreRepository,
)
from knowledge.repositories.vector_store_repository import VectorStoreRepository
from knowledge.repositories.vector_shadow_repository import (
    PostgresVectorShadowAudit,
    ShadowVectorStoreRepository,
)


def create_vector_store_repository(
    settings: Settings | None = None,
    *,
    require_embedding: bool = True,
    collection_name: str | None = None,
    embedding: Any | None = None,
    shadow_audit_sink: Any | None = None,
) -> Any:
    resolved = settings or get_settings()
    if resolved.vector_store_provider == "chroma":
        primary = VectorStoreRepository.from_settings(
            resolved,
            require_embedding=require_embedding,
            collection_name=collection_name,
        )
        if not resolved.vector_shadow_enabled:
            return primary
        selected_embedding = embedding
        if selected_embedding is None and require_embedding:
            selected_embedding = OpenAIEmbeddings(
                model=resolved.embedding_model,
                api_key=resolved.resolved_embedding_api_key,
                base_url=resolved.resolved_embedding_base_url,
                dimensions=resolved.embedding_dimensions,
                chunk_size=resolved.embedding_batch_size,
                check_embedding_ctx_length=False,
            )
        shadow = PostgresVectorStoreRepository.from_settings(
            resolved,
            collection_name=collection_name,
            embedding=selected_embedding,
        )
        return ShadowVectorStoreRepository(
            primary,
            shadow,
            audit_sink=shadow_audit_sink
            or PostgresVectorShadowAudit(
                shadow.pool,
                schema=resolved.pgvector_schema,
            ),
            sample_rate=resolved.vector_shadow_sample_rate,
        )

    selected_embedding = embedding
    if selected_embedding is None and require_embedding:
        if not resolved.resolved_embedding_api_key:
            raise ValueError("EMBEDDING_API_KEY is required for embedding operations")
        selected_embedding = OpenAIEmbeddings(
            model=resolved.embedding_model,
            api_key=resolved.resolved_embedding_api_key,
            base_url=resolved.resolved_embedding_base_url,
            dimensions=resolved.embedding_dimensions,
            chunk_size=resolved.embedding_batch_size,
            check_embedding_ctx_length=False,
        )
    return PostgresVectorStoreRepository.from_settings(
        resolved,
        collection_name=collection_name,
        embedding=selected_embedding,
    )
