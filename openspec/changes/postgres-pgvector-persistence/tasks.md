## 1. Specification And Baseline

- [x] 1.1 Validate proposal, design and all three capability specs with `openspec validate postgres-pgvector-persistence --strict`.
- [x] 1.2 Record non-sensitive SQLite table counts, Chroma collection counts, PostgreSQL/pgvector versions and the current Python regression baseline.

## 2. PostgreSQL Foundation

- [x] 2.1 Add failing settings tests for providers, DSN precedence, split PG variables, identifiers, pool limits, statement timeout and secret-safe representations; then implement settings.
- [x] 2.2 Add failing DatabaseResources tests for start, close, readiness, transaction rollback and degraded startup; then implement the async engine resource.
- [x] 2.3 Add failing metadata/Alembic tests for PostgreSQL-native types, indexes, empty-schema upgrade, repeat upgrade and downgrade; then add the baseline migration.

## 3. Relational Providers

- [x] 3.1 Add shared contract tests and PostgreSQL implementations for Feishu events, conversation scope, pending runs and user auth.
- [x] 3.2 Add PostgreSQL Agent Session implementing SessionABC and migrate history/read-only conversation access.
- [x] 3.3 Add shared contract tests and PostgreSQL implementations for Catalog, sync claim/retry/recovery and admin/audit storage.
- [x] 3.4 Add shared contract tests and PostgreSQL implementations for Memory and Quality, including `FOR UPDATE SKIP LOCKED` worker claims.
- [x] 3.5 Add PostgreSQL LangGraph Checkpointer lifecycle and checkpoint migration through official Saver APIs.

## 4. pgvector Provider

- [x] 4.1 Add failing vector repository contract tests for upsert, metadata update, delete, pagination, count, cosine search and filter normalization.
- [x] 4.2 Add failing isolation tests for collection, branch/domain/source and memory owner/scope/space; then implement `PostgresVectorStoreRepository`.
- [x] 4.3 Add provider factory and shadow comparison tests proving shadow never changes the primary answer and records only IDs, timing and overlap.

## 5. Migration And Verification

- [x] 5.1 Add failing migration-state tests for stable run IDs, batching, resume, idempotent replay and secret-safe reports.
- [x] 5.2 Implement SQLite-to-PostgreSQL migration in foreign-key order, sequence correction and relationship verification.
- [x] 5.3 Implement Chroma-to-pgvector migration for both collections without embedding API calls or intermediate content/vector files.
- [x] 5.4 Implement count, ID, status, hash, metadata and Top-K verification commands.

## 6. Lifecycle, Readiness And Rollout

- [x] 6.1 Wire provider factories, dynamic readiness and compatibility fields into FastAPI lifespan without changing public APIs.
- [x] 6.2 Run Alembic and repository contracts in a temporary dev Schema, then remove the Schema through downgrade/cleanup.
- [ ] 6.3 Run relationship migration rehearsal, PostgreSQL + Chroma smoke tests and Critical 5/10/30.
- [ ] 6.4 Migrate vectors, build HNSW/ANALYZE, run one-workday shadow and enforce overlap/latency gates before pgvector cutover.
- [x] 6.5 Document the 15-minute cutover, provider rollback, queue drain, backup, readiness and one-release retention procedure.
