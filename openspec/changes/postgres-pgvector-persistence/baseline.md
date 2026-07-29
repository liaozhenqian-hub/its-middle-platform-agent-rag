## 2026-07-28 Dev Baseline

- PostgreSQL database: `middle_agent`
- PostgreSQL major version: 15 or newer
- pgvector: enabled, version 0.8.0
- PostgreSQL `public` base tables before migration: 0
- SQLite: 7 files, 72 application tables, approximately 117,117 rows
- Chroma `metric_platform_knowledge`: 39,598 entries
- Chroma `middle_platform_memories`: 1 entry
- Embedding dimension: 1024
- Python baseline: full suite reached 100% with one worktree-only missing ignored benchmark script; after copying that runtime dependency, its targeted test passed

This baseline contains counts and versions only. It intentionally excludes credentials, DSNs, document content, log content, prompts and embeddings.
