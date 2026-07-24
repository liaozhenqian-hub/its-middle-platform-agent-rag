from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote

from knowledge.catalog.models import (
    ChunkCatalogCreate,
    CodeSymbolCreate,
    SourceFileCreate,
    SourceType,
    SourceVersionCreate,
)
from knowledge.catalog.repository import CatalogRepository
from knowledge.parsers import (
    CodeParser,
    DirectoryDomainClassifier,
    DocumentParser,
    SourceFilePolicy,
    VueSfcBatchParser,
    VueSfcSource,
)
from knowledge.schemas.documents import KnowledgeChunk
from knowledge.source_sync import GitChangeType, GitSnapshot


@dataclass(frozen=True)
class IndexingSummary:
    source_id: str
    version_ref: str
    version_id: str
    upserted: int
    deleted: int


class SourceIndexCoordinator:
    def __init__(
        self,
        catalog: CatalogRepository,
        vector_repository,
        pipeline_registry,
        *,
        code_parser: CodeParser | None = None,
        document_parser: DocumentParser | None = None,
        file_policy: SourceFilePolicy | None = None,
        vue_parser: VueSfcBatchParser | None = None,
    ):
        self.catalog = catalog
        self.vector_repository = vector_repository
        self.pipeline_registry = pipeline_registry
        self.code_parser = code_parser or CodeParser()
        self.document_parser = document_parser or DocumentParser()
        self.file_policy = file_policy or SourceFilePolicy()
        self.vue_parser = vue_parser or VueSfcBatchParser(code_parser=self.code_parser)

    async def index_git_snapshot(
        self,
        source_id: str,
        snapshot: GitSnapshot,
    ) -> IndexingSummary:
        source = await self._require_source(source_id, SourceType.GIT)
        if not snapshot.worktree_path.is_dir():
            raise ValueError("Git worktree root does not exist")
        old_entries = await self.catalog.list_chunks(source_id=source_id)
        versions_before = await self.catalog.list_versions(source_id)
        baseline_current = next((item for item in versions_before if item.current), None)
        succeeded_version_ids = {
            item.id for item in versions_before if item.status == "succeeded"
        }
        baseline_entries = [
            entry
            for entry in old_entries
            if entry.version_id in succeeded_version_ids
        ]
        discarded_entry_ids = sorted(
            {entry.chunk_id for entry in old_entries}
            - {entry.chunk_id for entry in baseline_entries}
        )
        rules = await self.catalog.list_domain_rules(source_id)
        classifier = DirectoryDomainClassifier(
            [
                (
                    rule.pattern,
                    "shared" if rule.shared else str(rule.target_domain_id),
                    rule.priority,
                )
                for rule in rules
            ]
        )
        paths, affected_paths = self._git_paths(snapshot)
        raw_files = self._read_code_files(snapshot.worktree_path, paths)
        chunks = await self._parse_code_files(
            raw_files,
            source_id=source_id,
            branch=str(source.config.get("branch") or "main"),
            commit_sha=snapshot.commit_sha,
            classifier=classifier,
        )
        chunks = [
            self._with_git_metadata(chunk, source, snapshot.commit_sha)
            for chunk in chunks
        ]
        stale = self._stale_chunk_ids(
            old_entries,
            chunks,
            None if snapshot.full_reconcile else affected_paths,
        )
        chunks_to_embed, metadata_only_chunks = self._diff_chunks(
            baseline_entries, chunks
        )
        version, already_current = await self._start_version(
            source_id,
            snapshot.commit_sha,
            {"commit_sha": snapshot.commit_sha},
        )
        if already_current:
            await self._delete_stale(stale)
            await self.catalog.update_source(
                source_id,
                config={**source.config, "last_synced_commit": snapshot.commit_sha},
            )
            self.pipeline_registry.invalidate(app_id="middle-platform")
            return IndexingSummary(
                source_id, snapshot.commit_sha, version.id, 0, len(stale)
            )

        backups = await self._backup_vectors(baseline_entries)
        mutation_started = False
        try:
            mutation_started = True
            if chunks_to_embed:
                await asyncio.to_thread(
                    self.vector_repository.upsert, chunks_to_embed
                )
            if metadata_only_chunks:
                update_metadata = getattr(
                    self.vector_repository, "update_metadata", None
                )
                if update_metadata is not None:
                    await asyncio.to_thread(
                        update_metadata, metadata_only_chunks
                    )
                else:
                    # Test doubles and legacy repositories without metadata-only
                    # updates retain correctness at the cost of re-embedding.
                    await asyncio.to_thread(
                        self.vector_repository.upsert, metadata_only_chunks
                    )
            await self._persist_chunks(
                source_id,
                version.id,
                SourceType.GIT,
                raw_files,
                chunks,
            )
            await self.catalog.update_version(version.id, status="succeeded", current=True)
            await self._delete_stale(stale)
            config = {**source.config, "last_synced_commit": snapshot.commit_sha}
            await self.catalog.update_source(source_id, config=config)
            self.pipeline_registry.invalidate(app_id="middle-platform")
            return IndexingSummary(
                source_id,
                snapshot.commit_sha,
                version.id,
                len(chunks),
                len(stale),
            )
        except Exception:
            if mutation_started:
                await self._rollback_index(
                    source,
                    version,
                    baseline_current,
                    baseline_entries,
                    backups,
                    chunks,
                    discarded_entry_ids,
                )
            else:
                await self.catalog.update_version(
                    version.id, status="failed", current=False
                )
            raise

    async def index_document_version(
        self,
        source_id: str,
        version_ref: str,
        root_path: str | Path,
    ) -> IndexingSummary:
        source = await self._require_source(source_id, SourceType.DOCUMENT)
        if not source.domain_id:
            raise ValueError("document sources require a domain")
        root = Path(root_path).resolve()
        if not root.is_dir():
            raise ValueError("document root does not exist or is not a directory")
        old_entries = await self.catalog.list_chunks(source_id=source_id)
        versions_before = await self.catalog.list_versions(source_id)
        baseline_current = next((item for item in versions_before if item.current), None)
        baseline_entries = [
            entry
            for entry in old_entries
            if baseline_current is not None and entry.version_id == baseline_current.id
        ]
        discarded_entry_ids = sorted(
            {entry.chunk_id for entry in old_entries}
            - {entry.chunk_id for entry in baseline_entries}
        )
        raw_files: dict[str, bytes] = {}
        chunks: list[KnowledgeChunk] = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative_path = path.relative_to(root).as_posix()
            if path.suffix.lower() not in {".md", ".txt", ".docx", ".pdf"}:
                continue
            raw_files[relative_path] = path.read_bytes()
            parsed = await asyncio.to_thread(
                self.document_parser.parse,
                path,
                source_id,
                version_ref,
                source.domain_id,
                relative_path=relative_path,
            )
            chunks.extend(parsed)
        if not raw_files:
            raise ValueError("document root contains no supported files")
        chunks = [self._with_document_metadata(chunk, source) for chunk in chunks]
        stale = sorted(
            {entry.chunk_id for entry in old_entries}
            - {chunk.chunk_id for chunk in chunks}
        )
        version, already_current = await self._start_version(
            source_id,
            version_ref,
            {"upload_version": version_ref},
        )
        if already_current:
            await self._delete_stale(stale)
            await self.catalog.update_source(
                source_id,
                config={**source.config, "last_synced_version": version_ref},
            )
            self.pipeline_registry.invalidate(app_id="middle-platform")
            return IndexingSummary(source_id, version_ref, version.id, 0, len(stale))

        backups = await self._backup_vectors(baseline_entries)
        mutation_started = False
        try:
            mutation_started = True
            if chunks:
                await asyncio.to_thread(self.vector_repository.upsert, chunks)
            await self._persist_chunks(
                source_id,
                version.id,
                SourceType.DOCUMENT,
                raw_files,
                chunks,
            )
            await self.catalog.update_version(version.id, status="succeeded", current=True)
            await self._delete_stale(stale)
            await self.catalog.update_source(
                source_id,
                config={**source.config, "last_synced_version": version_ref},
            )
            self.pipeline_registry.invalidate(app_id="middle-platform")
            return IndexingSummary(
                source_id,
                version_ref,
                version.id,
                len(chunks),
                len(stale),
            )
        except Exception:
            if mutation_started:
                await self._rollback_index(
                    source,
                    version,
                    baseline_current,
                    baseline_entries,
                    backups,
                    chunks,
                    discarded_entry_ids,
                )
            else:
                await self.catalog.update_version(
                    version.id, status="failed", current=False
                )
            raise

    async def delete_source_content(self, source_id: str) -> int:
        entries = await self.catalog.list_chunks(source_id=source_id)
        chunk_ids = [entry.chunk_id for entry in entries]
        if chunk_ids:
            await asyncio.to_thread(self.vector_repository.delete, chunk_ids)
            await self.catalog.delete_chunks(chunk_ids)
        self.pipeline_registry.invalidate(app_id="middle-platform")
        return len(chunk_ids)

    async def _require_source(self, source_id: str, expected_type: SourceType):
        source = await self.catalog.get_source(source_id)
        if source is None:
            raise ValueError("knowledge source does not exist")
        if source.source_type is not expected_type:
            raise ValueError(f"source must be {expected_type.value}")
        if not source.enabled:
            raise ValueError("knowledge source is disabled")
        return source

    async def _start_version(self, source_id: str, version_ref: str, metadata: dict):
        existing = next(
            (
                item
                for item in await self.catalog.list_versions(source_id)
                if item.version_ref == version_ref
            ),
            None,
        )
        if existing is not None and existing.current and existing.status == "succeeded":
            return existing, True
        if existing is not None:
            await self.catalog.delete_version(existing.id)
        version_id = self._stable_id("version", source_id, version_ref)
        return (
            await self.catalog.create_version(
                SourceVersionCreate(
                    id=version_id,
                    source_id=source_id,
                    version_ref=version_ref,
                    status="indexing",
                    current=False,
                    metadata=metadata,
                )
            ),
            False,
        )

    async def _backup_vectors(self, old_entries) -> list[KnowledgeChunk]:
        chunk_ids = [entry.chunk_id for entry in old_entries]
        if not chunk_ids:
            return []
        return await asyncio.to_thread(
            self.vector_repository.get_chunks,
            ids=chunk_ids,
        )

    async def _rollback_index(
        self,
        source,
        candidate_version,
        baseline_current,
        old_entries,
        vector_backups: list[KnowledgeChunk],
        new_chunks: list[KnowledgeChunk],
        discarded_entry_ids: list[str],
    ) -> None:
        rollback_delete_ids = sorted(
            {chunk.chunk_id for chunk in new_chunks} | set(discarded_entry_ids)
        )
        if rollback_delete_ids:
            await asyncio.to_thread(
                self.vector_repository.delete, rollback_delete_ids
            )
            await self.catalog.delete_chunks(rollback_delete_ids)
        if vector_backups:
            await asyncio.to_thread(self.vector_repository.upsert, vector_backups)
        for entry in old_entries:
            await self.catalog.upsert_chunk(
                ChunkCatalogCreate(
                    chunk_id=entry.chunk_id,
                    source_id=entry.source_id,
                    version_id=entry.version_id,
                    source_file_id=entry.source_file_id,
                    source_type=entry.source_type,
                    domain_key=entry.domain_key,
                    locator=entry.locator,
                    content_hash=entry.content_hash,
                    metadata=entry.metadata,
                )
            )
        if baseline_current is not None:
            await self.catalog.update_version(
                baseline_current.id,
                status=baseline_current.status,
                current=True,
            )
        await self.catalog.update_version(
            candidate_version.id,
            status="failed",
            current=False,
        )
        await self.catalog.update_source(source.id, config=source.config)
        self.pipeline_registry.invalidate(app_id="middle-platform")

    def _git_paths(self, snapshot: GitSnapshot) -> tuple[list[str], set[str]]:
        if snapshot.full_reconcile:
            paths = [
                path.relative_to(snapshot.worktree_path).as_posix()
                for path in snapshot.worktree_path.rglob("*")
                if path.is_file()
            ]
            return sorted(paths), set(paths)
        current_paths = []
        affected = set()
        for change in snapshot.changes:
            affected.add(change.path)
            if change.previous_path:
                affected.add(change.previous_path)
            if change.status is not GitChangeType.DELETED:
                current_paths.append(change.path)
        return sorted(set(current_paths)), affected

    def _read_code_files(self, root: Path, paths: list[str]) -> dict[str, bytes]:
        accepted = {}
        for relative_path in paths:
            path = (root / Path(relative_path)).resolve()
            if root.resolve() not in path.parents or not path.is_file():
                continue
            content = path.read_bytes()
            if self.file_policy.evaluate(relative_path, content).accepted:
                accepted[relative_path] = content
        return accepted

    async def _parse_code_files(
        self,
        raw_files: dict[str, bytes],
        *,
        source_id: str,
        branch: str,
        commit_sha: str,
        classifier: DirectoryDomainClassifier,
    ) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        vue_sources = []
        for relative_path, content in raw_files.items():
            domain_id = classifier.classify(relative_path)
            text = content.decode("utf-8")
            if relative_path.lower().endswith(".vue"):
                vue_sources.append(
                    VueSfcSource(
                        relative_path,
                        text,
                        source_id,
                        branch,
                        commit_sha,
                        domain_id,
                    )
                )
                continue
            chunks.extend(
                await asyncio.to_thread(
                    self.code_parser.parse,
                    relative_path,
                    text,
                    source_id,
                    branch,
                    commit_sha,
                    domain_id,
                )
            )
        if vue_sources:
            chunks.extend(await asyncio.to_thread(self.vue_parser.parse_many, vue_sources))
        return chunks

    @staticmethod
    def _with_git_metadata(chunk, source, commit_sha: str):
        metadata = {
            **chunk.metadata,
            "app_id": "middle-platform",
            "space_id": source.space_id,
            "source_id": source.id,
            "source_type": "code",
            "source_version": commit_sha,
            "commit_sha": commit_sha,
        }
        path = str(metadata.get("relative_path") or "")
        start = int(metadata.get("start_line") or 1)
        end = int(metadata.get("end_line") or start)
        web_url = str(source.config.get("project_web_url") or "").rstrip("/")
        if web_url:
            metadata["gitlab_url"] = (
                f"{web_url}/-/blob/{quote(commit_sha, safe='')}/"
                f"{quote(path, safe='/')}#L{start}-{end}"
            )
        return replace(chunk, metadata=metadata)

    @staticmethod
    def _with_document_metadata(chunk, source):
        return replace(
            chunk,
            metadata={
                **chunk.metadata,
                "app_id": "middle-platform",
                "space_id": source.space_id,
                "domain_id": source.domain_id,
                "source_id": source.id,
                "source_type": "product_document",
            },
        )

    async def _persist_chunks(
        self,
        source_id: str,
        version_id: str,
        catalog_source_type: SourceType,
        raw_files: dict[str, bytes],
        chunks: list[KnowledgeChunk],
    ) -> None:
        chunks_by_path: dict[str, list[KnowledgeChunk]] = {}
        for chunk in chunks:
            path = str(chunk.metadata.get("relative_path") or "")
            chunks_by_path.setdefault(path, []).append(chunk)
        file_ids = {}
        for relative_path, content in raw_files.items():
            file_id = self._stable_id("file", source_id, version_id, relative_path)
            file_ids[relative_path] = file_id
            path_chunks = chunks_by_path.get(relative_path, [])
            domain_key = (
                str(path_chunks[0].metadata.get("domain_id") or "shared")
                if path_chunks
                else "shared"
            )
            await self.catalog.create_file(
                SourceFileCreate(
                    id=file_id,
                    source_id=source_id,
                    version_id=version_id,
                    relative_path=relative_path,
                    domain_key=domain_key,
                    language=(
                        str(path_chunks[0].metadata.get("language") or "") or None
                        if path_chunks
                        else None
                    ),
                    content_hash=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                )
            )
        for chunk in chunks:
            relative_path = str(chunk.metadata.get("relative_path") or "")
            file_id = file_ids.get(relative_path)
            domain_key = str(chunk.metadata.get("domain_id") or "shared")
            await self.catalog.upsert_chunk(
                ChunkCatalogCreate(
                    chunk_id=chunk.chunk_id,
                    source_id=source_id,
                    version_id=version_id,
                    source_file_id=file_id,
                    source_type=catalog_source_type,
                    domain_key=domain_key,
                    locator=f"{relative_path}#{chunk.heading}",
                    content_hash=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                    metadata=chunk.metadata,
                )
            )
            if catalog_source_type is SourceType.GIT and file_id:
                start_line = int(chunk.metadata.get("start_line") or 1)
                end_line = int(chunk.metadata.get("end_line") or start_line)
                await self.catalog.create_symbol(
                    CodeSymbolCreate(
                        id=self._stable_id("symbol", version_id, chunk.chunk_id),
                        source_file_id=file_id,
                        symbol_type=str(chunk.metadata.get("symbol_type") or "symbol"),
                        name=str(chunk.metadata.get("symbol_name") or chunk.heading),
                        qualified_name=str(
                            chunk.metadata.get("symbol_name") or chunk.heading
                        ),
                        start_line=start_line,
                        end_line=end_line,
                        metadata={
                            key: chunk.metadata[key]
                            for key in ("calls", "extends", "implements", "annotations")
                            if key in chunk.metadata
                        },
                    )
                )

    @staticmethod
    def _stale_chunk_ids(old_entries, chunks, affected_paths: set[str] | None):
        current_ids = {chunk.chunk_id for chunk in chunks}
        eligible_old_ids = {
            entry.chunk_id
            for entry in old_entries
            if affected_paths is None
            or str(entry.metadata.get("relative_path") or "") in affected_paths
        }
        return sorted(eligible_old_ids - current_ids)

    @staticmethod
    def _diff_chunks(old_entries, chunks):
        old_by_id = {entry.chunk_id: entry for entry in old_entries}
        to_embed = []
        metadata_only = []
        for chunk in chunks:
            content_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
            old = old_by_id.get(chunk.chunk_id)
            if old is None or old.content_hash != content_hash:
                to_embed.append(chunk)
            else:
                metadata_only.append(chunk)
        return to_embed, metadata_only

    async def _delete_stale(self, stale_ids: list[str]) -> None:
        if not stale_ids:
            return
        await asyncio.to_thread(self.vector_repository.delete, stale_ids)
        await self.catalog.delete_chunks(stale_ids)

    @staticmethod
    def _stable_id(*parts: str) -> str:
        digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]
        return f"idx-{digest}"
