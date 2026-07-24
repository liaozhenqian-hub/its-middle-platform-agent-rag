from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        version=1,
        name="catalog_sources",
        statements=(
            """
            CREATE TABLE knowledge_spaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE knowledge_domains (
                id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL REFERENCES knowledge_spaces(id)
                    ON DELETE CASCADE,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(space_id, name)
            )
            """,
            """
            CREATE TABLE knowledge_sources (
                id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
                domain_id TEXT REFERENCES knowledge_domains(id),
                source_type TEXT NOT NULL CHECK (
                    source_type IN ('git', 'document', 'swagger')
                ),
                name TEXT NOT NULL,
                config_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_knowledge_sources_scope
            ON knowledge_sources(space_id, domain_id, source_type, enabled)
            """,
            """
            CREATE TABLE source_domain_rules (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES knowledge_sources(id)
                    ON DELETE CASCADE,
                pattern TEXT NOT NULL,
                target_domain_id TEXT REFERENCES knowledge_domains(id),
                shared INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0, 1)),
                priority INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL,
                CHECK (
                    (shared = 1 AND target_domain_id IS NULL) OR
                    (shared = 0 AND target_domain_id IS NOT NULL)
                )
            )
            """,
            """
            CREATE INDEX idx_source_domain_rules_source
            ON source_domain_rules(source_id, priority, id)
            """,
            """
            INSERT INTO knowledge_spaces(id, name, created_at)
            VALUES ('middle-platform', '中台', '2026-01-01T00:00:00+00:00')
            """,
            """
            INSERT INTO knowledge_domains(id, space_id, name, sort_order, created_at)
            VALUES
                ('metric-platform', 'middle-platform', '指标平台', 10,
                 '2026-01-01T00:00:00+00:00'),
                ('approval-flow', 'middle-platform', '审批流', 20,
                 '2026-01-01T00:00:00+00:00'),
                ('workflow', 'middle-platform', '工作流', 30,
                 '2026-01-01T00:00:00+00:00')
            """,
        ),
    ),
    Migration(
        version=2,
        name="catalog_content",
        statements=(
            """
            CREATE TABLE source_versions (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES knowledge_sources(id)
                    ON DELETE CASCADE,
                version_ref TEXT NOT NULL,
                status TEXT NOT NULL,
                current INTEGER NOT NULL DEFAULT 0 CHECK (current IN (0, 1)),
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_id, version_ref),
                UNIQUE(id, source_id)
            )
            """,
            """
            CREATE UNIQUE INDEX idx_source_versions_current
            ON source_versions(source_id) WHERE current = 1
            """,
            """
            CREATE TABLE source_files (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES knowledge_sources(id)
                    ON DELETE CASCADE,
                version_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                domain_key TEXT NOT NULL,
                language TEXT,
                content_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(version_id, relative_path),
                UNIQUE(id, source_id),
                FOREIGN KEY(version_id, source_id)
                    REFERENCES source_versions(id, source_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX idx_source_files_source_version
            ON source_files(source_id, version_id, relative_path)
            """,
            """
            CREATE TABLE code_symbols (
                id TEXT PRIMARY KEY,
                source_file_id TEXT NOT NULL REFERENCES source_files(id)
                    ON DELETE CASCADE,
                symbol_type TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT,
                start_line INTEGER NOT NULL CHECK (start_line > 0),
                end_line INTEGER NOT NULL CHECK (end_line >= start_line),
                parent_symbol_id TEXT REFERENCES code_symbols(id) ON DELETE SET NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_code_symbols_file_name
            ON code_symbols(source_file_id, name)
            """,
            """
            CREATE TABLE chunk_catalog (
                chunk_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES knowledge_sources(id)
                    ON DELETE CASCADE,
                version_id TEXT NOT NULL,
                source_file_id TEXT,
                source_type TEXT NOT NULL CHECK (
                    source_type IN ('git', 'document', 'swagger')
                ),
                domain_key TEXT NOT NULL,
                locator TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(version_id, source_id)
                    REFERENCES source_versions(id, source_id) ON DELETE CASCADE,
                FOREIGN KEY(source_file_id, source_id)
                    REFERENCES source_files(id, source_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX idx_chunk_catalog_scope
            ON chunk_catalog(source_id, version_id, domain_key, source_type)
            """,
        ),
    ),
    Migration(
        version=3,
        name="catalog_operations_and_auth",
        statements=(
            """
            CREATE TABLE sync_jobs (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES knowledge_sources(id)
                    ON DELETE CASCADE,
                kind TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('queued', 'running', 'succeeded', 'failed')
                ),
                target_commit TEXT,
                attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
                error TEXT,
                worker_id TEXT,
                available_at TEXT NOT NULL,
                claimed_at TEXT,
                finished_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE UNIQUE INDEX idx_sync_jobs_source_commit
            ON sync_jobs(source_id, target_commit)
            WHERE target_commit IS NOT NULL
            """,
            """
            CREATE INDEX idx_sync_jobs_claim
            ON sync_jobs(state, available_at, created_at)
            """,
            """
            CREATE TABLE encrypted_secrets (
                source_id TEXT NOT NULL REFERENCES knowledge_sources(id)
                    ON DELETE CASCADE,
                secret_kind TEXT NOT NULL,
                encrypted_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(source_id, secret_kind)
            )
            """,
            """
            CREATE TABLE admin_sessions (
                id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL,
                csrf_token TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_admin_sessions_expiry ON admin_sessions(expires_at)
            """,
            """
            CREATE TABLE audit_events (
                id TEXT PRIMARY KEY,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_audit_events_lookup
            ON audit_events(actor, resource_type, created_at DESC)
            """,
        ),
    ),
    Migration(
        version=4,
        name="sync_job_kind_scoped_dedupe",
        statements=(
            "DROP INDEX IF EXISTS idx_sync_jobs_source_commit",
            """
            CREATE UNIQUE INDEX idx_sync_jobs_source_kind_commit
            ON sync_jobs(source_id, kind, target_commit)
            WHERE target_commit IS NOT NULL
            """,
        ),
    ),
    Migration(
        version=5,
        name="swagger_last_good_cache",
        statements=(
            """
            CREATE TABLE swagger_cache (
                source_id TEXT PRIMARY KEY REFERENCES knowledge_sources(id)
                    ON DELETE CASCADE,
                specification_json TEXT NOT NULL,
                etag TEXT,
                last_modified TEXT,
                refreshed_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        version=6,
        name="git_webhook_secret_hashes",
        statements=(
            """
            CREATE TABLE source_webhook_secrets (
                source_id TEXT PRIMARY KEY REFERENCES knowledge_sources(id)
                    ON DELETE CASCADE,
                secret_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
)
