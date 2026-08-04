## Context

服务当前运行单 Uvicorn Worker 和单内置 Worker。七个 SQLite 文件约 117,117 行；Chroma 的 `metric_platform_knowledge` 与 `middle_platform_memories` 分别约 39,598 和 1 条。PostgreSQL 15+ 数据库 `middle_agent` 的 `public` Schema 尚无业务表，pgvector 0.8.0 已启用。现有仓储大量直接依赖 aiosqlite，向量调用方则已基本收敛到 `VectorStoreRepository`。

## Goals / Non-Goals

**Goals:**

- 在公开 API 不变的前提下支持 SQLite/PostgreSQL 与 Chroma/pgvector 独立切换。
- 原样迁移 ID、正文、metadata 和既有 1024 维 embedding，不重新向量化。
- 保持用户、会话、领域、分支、来源与个人记忆隔离。
- 为并发任务领取、事务回滚、迁移续传、shadow 对比和快速回滚提供确定性机制。

**Non-Goals:**

- 本期不迁移内存 BM25、不改变 chunk 切分或 embedding 模型。
- 本期不启用多 Uvicorn Worker、多副本、Redis、CDC 或长期双写。
- 本期不删除 SQLite/Chroma，不在生产环境继续使用建表权限作为最终安全方案。

## Decisions

### Provider 边界优先于一次性重写

每个领域保留现有 SQLite 仓储，新增 PostgreSQL 实现并共享最小 Protocol。生命周期只调用工厂，不在 API、Agent 或 Worker 中判断数据库类型。这样 PostgreSQL 与 pgvector 可以分别灰度和回滚，也能让同一仓储契约测试覆盖两种实现。

### SQLAlchemy Core 与单一 Alembic 基线

业务表使用 SQLAlchemy Core metadata 和 asyncpg AsyncEngine；Alembic负责 `public` Schema 的结构版本。时间使用 TIMESTAMPTZ，结构化值使用 JSONB，布尔值使用 BOOLEAN。LangGraph 内部表不复制到业务 metadata，而由官方 PostgreSQL Checkpointer `setup()` 管理。

### 统一向量命名空间

`vector_entries` 使用 `(collection_name, id)` 复合主键，保存正文、标题、metadata、content hash、1024 维 embedding 与高频过滤列。所有查询必须限定 collection；个人记忆还必须限定 owner、scope 和 space。向量检索使用 cosine distance，保持“越小越相似”的现有语义。HNSW 在批量导入后建立并使用 pgvector 0.8 的 iterative scan 支持过滤检索。

### 离线关系切换与在线向量 shadow

关系库不做长期双写。正式切换时停止用户和后台写入，备份 SQLite，全量 COPY/upsert、校验并在 15 分钟内切换；失败直接恢复 SQLite provider。向量迁移只暂停 Source Worker，正式检索继续使用 Chroma；pgvector shadow 至少运行一个工作日并通过质量门禁后才切换。

### 敏感数据和恢复状态

迁移报告只保存表/collection、count、ID/hash 摘要、状态与耗时，不保存正文、embedding、Prompt、日志原文或凭证。加密凭证直接复制密文。迁移批次记录稳定 run ID、游标和统计，允许重复执行与断点续传。

## Data Model

业务 Alembic 基线覆盖 Catalog、Agent、Memory、Quality、Auth 和 Feishu 表，并增加 `storage_migration_runs` 与 `storage_migration_steps`。`vector_entries` 包含 `collection_name`、`id`、`content`、`heading`、`metadata`、`embedding`、`content_hash`、`app_id`、`domain`、`source_id`、`source_type`、`branch`、`owner_id`、`scope_type`、`space_id`、`enabled`、`created_at` 与 `updated_at`。

## Failure Handling And Rollback

- PostgreSQL 启动失败时，只有被选中的 PostgreSQL provider 标记 unavailable；SQLite 模式行为不变。
- 迁移失败不自动修改 provider；目标数据保留用于诊断，下次使用同一 run ID 幂等恢复。
- 关系切换失败时停止服务、恢复 SQLite 配置和备份并重新启动。
- pgvector 质量或延迟不达标时保持/恢复 Chroma，pgvector 数据不删除。
- readiness 同时暴露统一 provider 状态和一个发布周期的旧 `sqlite/chroma` 兼容字段。

## Validation Gates

- PostgreSQL 临时 Schema 可完整 upgrade/downgrade，仓储契约在 SQLite/PostgreSQL 均通过。
- 所有关系表 count、主键、外键、状态分布和抽样 hash 一致。
- 两个向量 collection 的 count、ID 集合、metadata/hash 抽样一致。
- 100 个代表问题 Top-10 overlap 不低于 90%，pgvector P90 不高于 Chroma 基线 20%。
- Critical 5/10/30、引用详情、飞书线程、会话恢复、记忆隔离和 Bug interrupt 不低于迁移前基线。
