import logging
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

from knowledge.config.settings import Settings, get_settings
from knowledge.schemas.documents import KeywordIndexRecord, KnowledgeChunk, SearchResult


logger = logging.getLogger(__name__)
_METADATA_PAGE_SIZE = 2000


class VectorStoreRepository:
    """Chroma 向量库访问层。

    这个类把 langchain_chroma.Chroma 包成项目自己的仓储接口：
    - upsert(): 入库 chunk 正文和 metadata
    - search(): 向量相似度检索
    - get_keyword_index_records(): 给 BM25 读取轻量索引字段
    - get_chunks()/get_chunk_ids(): 给过滤、展示和正文回填使用
    """

    def __init__(
        self,
        persist_directory: str | Path,
        collection_name: str,
        embedding: Any | None = None,
    ):
        self.persist_directory = Path(persist_directory)
        self.collection_name = collection_name
        self.embedding = embedding
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.vector_store = Chroma(
            collection_name=self.collection_name,
            persist_directory=str(self.persist_directory),
            embedding_function=self.embedding,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        require_embedding: bool = True,
        collection_name: str | None = None,
    ) -> "VectorStoreRepository":
        resolved = settings or get_settings()
        embedding = None
        if require_embedding:
            # 向量入库和向量检索都需要 embedding。
            # 但 stats、BM25 索引读取这类只读 metadata 操作可以 require_embedding=False。
            if not resolved.resolved_embedding_api_key:
                raise ValueError(
                    "EMBEDDING_API_KEY is required for embedding operations. "
                    "DeepSeek chat models are configured separately with DEEPSEEK_API_KEY."
                )
            embedding = OpenAIEmbeddings(
                model=resolved.embedding_model,
                api_key=resolved.resolved_embedding_api_key,
                base_url=resolved.resolved_embedding_base_url,
                dimensions=resolved.embedding_dimensions,
                chunk_size=resolved.embedding_batch_size,
                check_embedding_ctx_length=False,
            )
        return cls(
            persist_directory=resolved.vector_store_path,
            collection_name=collection_name or resolved.chroma_collection_name,
            embedding=embedding,
        )

    def reset(self) -> None:
        self.vector_store.reset_collection()

    def upsert(self, chunks: list[KnowledgeChunk]) -> list[str]:
        if not chunks:
            return []
        stored_ids: list[str] = []
        batch_size = self._max_batch_size(len(chunks))
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            ids = [chunk.chunk_id for chunk in batch]
            try:
                # 删除和写入都必须遵守 Chroma 的单批数量上限。
                self.vector_store.delete(ids=ids)
            except Exception:
                logger.warning(
                    "Failed to delete existing vectors before upsert collection=%s id_count=%d",
                    self.collection_name,
                    len(ids),
                    exc_info=True,
                )
            stored_ids.extend(
                self.vector_store.add_texts(
                    texts=[chunk.content for chunk in batch],
                    metadatas=[chunk.metadata for chunk in batch],
                    ids=ids,
                )
            )
        return stored_ids

    def update_metadata(self, chunks: list[KnowledgeChunk]) -> list[str]:
        """Update commit/permalink metadata without recomputing embeddings."""
        if not chunks:
            return []
        batch_size = self._max_batch_size(len(chunks))
        updated: list[str] = []
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            ids = [chunk.chunk_id for chunk in batch]
            self.vector_store._collection.update(
                ids=ids,
                metadatas=[chunk.metadata for chunk in batch],
            )
            updated.extend(ids)
        return updated

    def delete(self, chunk_ids: list[str]) -> int:
        ids = list(dict.fromkeys(chunk_id for chunk_id in chunk_ids if chunk_id))
        if not ids:
            return 0
        batch_size = self._max_batch_size(len(ids))
        for offset in range(0, len(ids), batch_size):
            self.vector_store.delete(ids=ids[offset : offset + batch_size])
        return len(ids)

    def _max_batch_size(self, item_count: int) -> int:
        client = getattr(self.vector_store, "_client", None)
        getter = getattr(client, "get_max_batch_size", None)
        if callable(getter):
            max_batch_size = int(getter())
            if max_batch_size > 0:
                return min(item_count, max_batch_size)
        return item_count

    def search(
        self,
        query: str,
        k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        # similarity_search_with_score 会先对 query 做 embedding，再在 Chroma 里找相似 chunk。
        # 这里的 score 在当前项目中作为 distance 使用：数值越小表示越相似。
        results = self.vector_store.similarity_search_with_score(
            query,
            k=k,
            filter=self._normalize_where(where),
        )
        search_results: list[SearchResult] = []
        for document, score in results:
            metadata = dict(document.metadata)
            # chunk_id 理论上在 metadata 里；如果缺失，就退回使用 Chroma document id。
            chunk_id = str(metadata.get("chunk_id") or document.id or "")
            search_results.append(
                SearchResult(
                    chunk_id=chunk_id,
                    content=document.page_content,
                    metadata=metadata,
                    score=float(score) if score is not None else None,
                )
            )
        return search_results

    def get_chunks(
        self,
        where: dict[str, Any] | None = None,
        ids: list[str] | None = None,
    ) -> list[KnowledgeChunk]:
        # 读取完整 chunk 正文和 metadata。
        # BM25 search 在选出 top_k 后会调用这个方法回填正文。
        get_kwargs: dict[str, Any] = {
            "include": ["documents", "metadatas"],
        }
        normalized_where = self._normalize_where(where)
        if normalized_where is not None:
            get_kwargs["where"] = normalized_where
        if ids is not None:
            get_kwargs["ids"] = ids
        records = self.vector_store._collection.get(**get_kwargs)
        documents = records.get("documents") or []
        metadatas = records.get("metadatas") or []
        chunks: list[KnowledgeChunk] = []
        for record_id, content, raw_metadata in zip(
            records.get("ids") or [],
            documents,
            metadatas,
        ):
            metadata = dict(raw_metadata or {})
            chunk_id = str(metadata.get("chunk_id") or record_id)
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    heading=str(metadata.get("heading", "")),
                    content=str(content or ""),
                    metadata=metadata,
                )
            )
        return chunks

    def get_keyword_index_records(
        self,
        where: dict[str, Any] | None = None,
    ) -> list[KeywordIndexRecord]:
        # 给 BM25 构建内存索引用：只读取 metadata，不读取长正文。
        # heading 和 bm25_keywords 都存放在 metadata 中。
        get_kwargs: dict[str, Any] = {"include": ["metadatas"]}
        normalized_where = self._normalize_where(where)
        if normalized_where is not None:
            get_kwargs["where"] = normalized_where
        page_size = getattr(self, "_metadata_page_size", _METADATA_PAGE_SIZE)
        offset = 0
        seen_record_ids: set[str] = set()
        index_records: list[KeywordIndexRecord] = []
        while True:
            records = self.vector_store._collection.get(
                **get_kwargs,
                limit=page_size,
                offset=offset,
            )
            record_ids = records.get("ids") or []
            for record_id, raw_metadata in zip(
                record_ids,
                records.get("metadatas") or [],
            ):
                normalized_id = str(record_id)
                if normalized_id in seen_record_ids:
                    continue
                seen_record_ids.add(normalized_id)
                metadata = dict(raw_metadata or {})
                index_records.append(
                    KeywordIndexRecord(
                        chunk_id=str(metadata.get("chunk_id") or normalized_id),
                        heading=str(metadata.get("heading", "")),
                        keywords=str(metadata.get("bm25_keywords", "")),
                        metadata=metadata,
                    )
                )
            if len(record_ids) < page_size:
                break
            offset += len(record_ids)
        return index_records

    def get_chunk_ids(
        self,
        where: dict[str, Any] | None = None,
    ) -> set[str]:
        # 只取 ID，用于 metadata filter 后确定哪些 chunk 有资格参与 BM25 排序。
        get_kwargs: dict[str, Any] = {"include": []}
        normalized_where = self._normalize_where(where)
        if normalized_where is not None:
            get_kwargs["where"] = normalized_where
        records = self.vector_store._collection.get(**get_kwargs)
        return {str(record_id) for record_id in records.get("ids") or []}

    @staticmethod
    def _normalize_where(
        where: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        # Chroma 的 where 对多个普通字段通常需要写成 {"$and": [{"a": 1}, {"b": 2}]}。
        # 项目里为了调用方便允许传 {"a": 1, "b": 2}，这里统一转换。
        #
        # 如果调用方已经传了 $and/$or 等操作符，就原样交给 Chroma。
        if not where or len(where) <= 1 or any(key.startswith("$") for key in where):
            return where
        return {"$and": [{key: value} for key, value in where.items()]}

    def count(self) -> int:
        return int(self.vector_store._collection.count())
