## Why

当前服务的 Catalog、会话、记忆、质量、认证、飞书事件与 Bug Graph 分散在七个 SQLite 文件中，知识与个人记忆向量存放在两个 Chroma collection。该结构适合单机验证，但不利于 dev 环境统一备份、并发任务领取、未来多节点和标准化运维。公司 dev PostgreSQL `middle_agent` 已可连接并启用 pgvector 0.8.0，因此需要在不改变公开问答接口、不重新生成 embedding 的前提下完成持久化改造。

## What Changes

- 增加 PostgreSQL 连接资源、SQLAlchemy Core 表模型、Alembic 基线和统一 readiness。
- 为关系仓储建立最小协议，保留 SQLite 实现并新增 PostgreSQL 实现，由 provider 工厂选择。
- 使用 PostgreSQL Session 实现 Agents SDK 会话，使用官方 PostgreSQL Checkpointer 保存 Bug Graph 状态。
- 增加统一命名空间 pgvector 表，兼容现有知识库与个人记忆 collection。
- 增加可恢复、幂等且不输出正文、凭证或 embedding 的关系/向量迁移与校验 CLI。
- 采用 PostgreSQL + Chroma、pgvector shadow、PostgreSQL + pgvector 的分阶段切换与独立回滚。

## Capabilities

### New Capabilities

- `postgres-relational-persistence`: PostgreSQL 连接、Schema、仓储协议、会话、任务并发和 readiness。
- `pgvector-vector-persistence`: 两个 collection 的统一 pgvector 存储、隔离过滤、检索和 shadow。
- `storage-migration-rollout`: SQLite/Chroma 数据迁移、一致性校验、切换、回滚和运维门禁。

### Modified Capabilities

None.

## Impact

- 新增 SQLAlchemy、asyncpg、Alembic、psycopg pool、pgvector 与 LangGraph PostgreSQL Checkpointer 依赖。
- 新增 PostgreSQL 与 pgvector provider 配置，但默认仍为 SQLite + Chroma。
- 现有 Chat、SSE、管理端、飞书、同步、记忆、引用和质量 API 保持兼容。
- dev 首次迁移使用最多 15 分钟停写窗口；SQLite 与 Chroma 至少保留一个发布周期。
