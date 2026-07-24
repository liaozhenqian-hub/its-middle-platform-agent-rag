from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from knowledge.agent_runtime.context import redact_mapping
from knowledge.parsers.documents import DocumentParser


class CitationDetailNotFound(LookupError):
    pass


@dataclass(frozen=True)
class CitationDetail:
    source_type: str
    source_id: str
    title: str
    domain: str
    excerpt: str
    language: str | None
    truncated: bool
    metadata: dict[str, Any]
    content_scope: str = "excerpt"
    full_text_available: bool = False
    document_url: str | None = None


class CitationDetailService:
    _CONTENT_TYPES = {"code", "product_document", "knowledge_chunk"}
    _PUBLIC_METADATA = {
        "branch",
        "commit_sha",
        "relative_path",
        "symbol_name",
        "start_line",
        "end_line",
        "gitlab_url",
        "page_number",
        "source_version",
        "heading",
        "language",
        "method",
        "path",
        "operation_id",
        "refreshed_at",
        "stale",
    }

    def __init__(
        self,
        *,
        catalog: Any,
        vector_repository: Any,
        max_chars: int,
        storage_root: str | Path | None = None,
        document_parser: DocumentParser | None = None,
    ):
        self.catalog = catalog
        self.vector_repository = vector_repository
        self.max_chars = max_chars
        self.storage_root = Path(storage_root).resolve() if storage_root else None
        self.document_parser = document_parser or DocumentParser()

    async def get(
        self, source_type: str, source_id: str, *, view: str = "section"
    ) -> CitationDetail:
        if view not in {"section", "full"}:
            raise ValueError("unsupported citation detail view")
        if source_type == "product_document":
            return await self._product_document_detail(source_id, view=view)
        if source_type in self._CONTENT_TYPES:
            return await self._content_detail(source_type, source_id)
        if source_type == "swagger":
            return await self._swagger_detail(source_id)
        raise CitationDetailNotFound(source_id)

    async def _content_detail(self, source_type: str, source_id: str) -> CitationDetail:
        entries = await self.catalog.list_chunks(chunk_ids=[source_id])
        if not entries:
            raise CitationDetailNotFound(source_id)
        source = await self.catalog.get_source(entries[0].source_id)
        if source is None or not bool(source.enabled):
            raise CitationDetailNotFound(source_id)
        chunks = await asyncio.to_thread(
            self.vector_repository.get_chunks,
            None,
            [source_id],
        )
        if not chunks:
            raise CitationDetailNotFound(source_id)
        chunk = chunks[0]
        actual_type = str(chunk.metadata.get("source_type") or "knowledge_chunk")
        if source_type != actual_type:
            raise CitationDetailNotFound(source_id)
        content = str(chunk.content or "")
        metadata = self._public_metadata(chunk.metadata)
        return CitationDetail(
            source_type=source_type,
            source_id=source_id,
            title=chunk.heading or source_id,
            domain=str(chunk.metadata.get("domain_id") or chunk.metadata.get("domain") or ""),
            excerpt=content[: self.max_chars],
            language=(
                str(chunk.metadata.get("language"))
                if chunk.metadata.get("language")
                else None
            ),
            truncated=len(content) > self.max_chars,
            metadata=metadata,
        )

    async def _product_document_detail(
        self, source_id: str, *, view: str
    ) -> CitationDetail:
        entry, source = await self._registered_entry(source_id)
        hit_chunks = await asyncio.to_thread(
            self.vector_repository.get_chunks, None, [source_id]
        )
        if not hit_chunks:
            raise CitationDetailNotFound(source_id)
        hit = hit_chunks[0]
        if str(hit.metadata.get("source_type") or "") != "product_document":
            raise CitationDetailNotFound(source_id)

        original_path = await self._document_path_for(entry, source)
        document_url = (
            f"/api/v1/citations/document?source_id={quote(source_id, safe='')}"
            if original_path is not None
            else None
        )
        if view == "full" and original_path is not None:
            content = await asyncio.to_thread(
                self._read_full_document,
                original_path,
                entry,
                source,
            )
            scope = "full"
        else:
            content = await self._section_content(entry, hit)
            scope = "section"

        metadata = self._public_metadata(hit.metadata)
        return CitationDetail(
            source_type="product_document",
            source_id=source_id,
            title=hit.heading or source_id,
            domain=str(hit.metadata.get("domain_id") or getattr(source, "domain_id", "") or ""),
            excerpt=content,
            language=(
                str(hit.metadata.get("language"))
                if hit.metadata.get("language")
                else self._document_language(original_path)
            ),
            truncated=False,
            metadata=metadata,
            content_scope=scope,
            full_text_available=original_path is not None,
            document_url=document_url,
        )

    async def document_path(self, source_id: str) -> Path:
        entry, source = await self._registered_entry(source_id)
        path = await self._document_path_for(entry, source)
        if path is None:
            raise CitationDetailNotFound(source_id)
        return path

    async def _registered_entry(self, source_id: str) -> tuple[Any, Any]:
        entries = await self.catalog.list_chunks(chunk_ids=[source_id])
        if not entries:
            raise CitationDetailNotFound(source_id)
        entry = entries[0]
        entry_type = str(getattr(entry, "source_type", "product_document"))
        if entry_type not in {"product_document", "SourceType.DOCUMENT", "document"}:
            raise CitationDetailNotFound(source_id)
        source = await self.catalog.get_source(entry.source_id)
        if source is None or not bool(source.enabled):
            raise CitationDetailNotFound(source_id)
        return entry, source

    async def _section_content(self, entry: Any, hit: Any) -> str:
        entries = await self.catalog.list_chunks(
            source_id=entry.source_id,
            version_id=getattr(entry, "version_id", None),
        )
        heading = str(hit.metadata.get("heading") or hit.heading or "")
        file_id = getattr(entry, "source_file_id", None)
        section_entries = [
            item
            for item in entries
            if getattr(item, "source_file_id", None) == file_id
            and str((getattr(item, "metadata", {}) or {}).get("heading") or "") == heading
        ]
        if not section_entries:
            return str(hit.content or "")
        section_entries.sort(
            key=lambda item: (
                int((getattr(item, "metadata", {}) or {}).get("section_index") or 0),
                int((getattr(item, "metadata", {}) or {}).get("chunk_part") or 1),
                str(getattr(item, "chunk_id", "")),
            )
        )
        chunks = await asyncio.to_thread(
            self.vector_repository.get_chunks,
            None,
            [item.chunk_id for item in section_entries],
        )
        by_id = {item.chunk_id: item for item in chunks}
        parts = [
            str(by_id[item.chunk_id].content or "")
            for item in section_entries
            if item.chunk_id in by_id
        ]
        return self._merge_parts(parts) or str(hit.content or "")

    async def _document_path_for(self, entry: Any, source: Any) -> Path | None:
        if self.storage_root is None or not getattr(entry, "source_file_id", None):
            return None
        files = await self.catalog.list_files(
            entry.source_id,
            getattr(entry, "version_id", None),
            file_id=entry.source_file_id,
        )
        if not files:
            return None
        version = str(
            (getattr(source, "config", {}) or {}).get("last_synced_version")
            or (getattr(entry, "metadata", {}) or {}).get("source_version")
            or ""
        ).strip()
        if not version:
            return None
        upload_root = (self.storage_root / "uploads" / entry.source_id / version).resolve()
        candidate = (upload_root / Path(files[0].relative_path)).resolve()
        if candidate != upload_root and upload_root not in candidate.parents:
            return None
        return candidate if candidate.is_file() else None

    def _read_full_document(self, path: Path, entry: Any, source: Any) -> str:
        suffix = path.suffix.lower()
        if suffix in {".md", ".txt"}:
            return path.read_text(encoding="utf-8")
        chunks = self.document_parser.parse(
            path,
            entry.source_id,
            str((getattr(entry, "metadata", {}) or {}).get("source_version") or "current"),
            str(getattr(source, "domain_id", "") or "shared"),
            relative_path=str((getattr(entry, "metadata", {}) or {}).get("relative_path") or path.name),
        )
        sections: list[str] = []
        current_key: tuple[int, str] | None = None
        parts: list[str] = []
        for chunk in chunks:
            key = (
                int(chunk.metadata.get("section_index") or len(sections)),
                str(chunk.metadata.get("heading") or chunk.heading or "Document"),
            )
            if current_key is not None and key != current_key:
                sections.append(f"## {current_key[1]}\n\n{self._merge_parts(parts)}")
                parts = []
            current_key = key
            parts.append(str(chunk.content or ""))
        if current_key is not None:
            sections.append(f"## {current_key[1]}\n\n{self._merge_parts(parts)}")
        return "\n\n".join(sections)

    @staticmethod
    def _merge_parts(parts: list[str]) -> str:
        merged = ""
        for raw in parts:
            part = raw.strip()
            if not part:
                continue
            if not merged:
                merged = part
                continue
            overlap = 0
            limit = min(1000, len(merged), len(part))
            for size in range(limit, 19, -1):
                if merged.endswith(part[:size]):
                    overlap = size
                    break
            merged = f"{merged}\n\n{part[overlap:].lstrip()}"
        return merged

    @staticmethod
    def _document_language(path: Path | None) -> str | None:
        if path is None:
            return None
        return {".md": "markdown", ".txt": "text", ".docx": "markdown", ".pdf": "markdown"}.get(path.suffix.lower())

    async def _swagger_detail(self, source_id: str) -> CitationDetail:
        swagger_source_id, separator, operation_identity = source_id.partition(":")
        if not separator or not operation_identity:
            raise CitationDetailNotFound(source_id)
        source = await self.catalog.get_source(swagger_source_id)
        if source is None or not bool(source.enabled):
            raise CitationDetailNotFound(source_id)
        cache = await self.catalog.get_swagger_cache(swagger_source_id)
        if cache is None:
            raise CitationDetailNotFound(source_id)
        operation = self._find_operation(cache["specification"], operation_identity)
        if operation is None:
            raise CitationDetailNotFound(source_id)
        method, path, value = operation
        operation_id = str(value.get("operationId") or f"{method}:{path}")
        summary = str(value.get("summary") or value.get("description") or "")
        excerpt = "\n".join(
            part
            for part in (
                f"{method} {path}",
                summary,
                "参数：" + json.dumps(value.get("parameters") or [], ensure_ascii=False),
                "响应：" + json.dumps(value.get("responses") or {}, ensure_ascii=False),
            )
            if part
        )
        refreshed_at = cache.get("refreshed_at")
        metadata = {
            "operation_id": operation_id,
            "method": method,
            "path": path,
            "refreshed_at": (
                refreshed_at.isoformat()
                if hasattr(refreshed_at, "isoformat")
                else str(refreshed_at or "")
            ),
        }
        return CitationDetail(
            source_type="swagger",
            source_id=source_id,
            title=operation_id,
            domain=str(getattr(source, "domain_id", "") or ""),
            excerpt=excerpt[: self.max_chars],
            language="json",
            truncated=len(excerpt) > self.max_chars,
            metadata=metadata,
        )

    @staticmethod
    def _find_operation(
        specification: dict[str, Any],
        identity: str,
    ) -> tuple[str, str, dict[str, Any]] | None:
        for path, path_item in (specification.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(operation, dict):
                    continue
                normalized_method = str(method).upper()
                operation_id = str(operation.get("operationId") or "")
                fallback = f"{normalized_method}:{path}"
                if identity in {operation_id, fallback}:
                    return normalized_method, str(path), operation
        return None

    def _public_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        redacted = redact_mapping(metadata)
        return {
            key: value
            for key, value in redacted.items()
            if key in self._PUBLIC_METADATA
        }
