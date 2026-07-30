# 中台 Agent PostgreSQL 与 pgvector 技术改造方案

## 1. 目标与范围

本方案将当前单机 SQLite + Chroma 数据层迁移到公司 dev PostgreSQL，并启用 pgvector。迁移后仍保持单 Uvicorn Worker 和单内置 Worker，先验证功能、数据一致性和检索质量，再考虑多副本。

目标数据库统一使用：

```text
middle_agent
```

首版使用 `public` Schema。所有业务表由 Alembic 创建，不手工逐张建表。pgvector 是同一 PostgreSQL 数据库中的扩展，不另建向量数据库。

本次包含：

- 知识目录、同步任务、认证、会话、记忆、问答质量和飞书事件迁移到 PostgreSQL。
- LangGraph checkpoint 迁移到 PostgreSQL Checkpointer。
- Chroma 中的现有 chunk、metadata 和 Embedding 原样迁移到 pgvector。
- 保留原 ID、来源、版本、分支、commit、行号和 citation 关联。
- 建立灰度开关、数据校验、检索对比和回滚机制。

本次不包含：

- 更换 Embedding 模型或重新向量化全部知识。
- 修改代码/文档 chunk 切分算法。
- 将内存 BM25 立即改为 PostgreSQL 全文检索。
- 立即启用多 Uvicorn Worker 或多副本。
- 删除 SQLite 和 Chroma 原始数据。

## 2. 当前状态

当前关系数据分散在以下 SQLite 文件：

| SQLite 文件 | 数据范围 |
|---|---|
| `knowledge_catalog.db` | 知识源、版本、文件、代码符号、chunk 目录、同步任务、凭证、Swagger 缓存、管理会话和审计 |
| `agent_sessions.db` | Agent 会话、消息、待审批运行和会话检索范围 |
| `bug_graph.db` | LangGraph Bug 诊断 checkpoint |
| `agent_memory.db` | 长期记忆、会话摘要、候选、流程记忆、实体和提取任务 |
| `agent_quality.db` | 问答、反馈、标注、质量 span、回归用例、运行和结果 |
| `user_auth.db` | 匿名身份、飞书用户、OAuth、用户 Session、个人 Token 和会话归属 |
| `feishu_bot.db` | 飞书消息去重和处理状态 |

当前 Chroma collection 为 `metric_platform_knowledge`，Embedding 维度配置为 1024。迁移时以停机快照的实际 count、维度和来源分布为准，不在文档中固化数量。

代码当前直接依赖 `sqlite3`、`aiosqlite`、OpenAI Agents SDK `SQLiteSession`、LangGraph `AsyncSqliteSaver` 和 Chroma `PersistentClient`。因此本次不是只替换一个连接字符串，需要建立存储接口并逐模块迁移。

## 3. dev 连接前置检查

Java 项目的 `application-dev.yml` 可以读取，但其中保存的是 Nacos dev 配置中心凭证，不是数据库连接。实际数据源配置由 Nacos 下发。

当前本机检查结果：

- Nacos账号信息存在。
- Nacos server、namespace、group 和 import 信息存在。
- 当前电脑无法解析 Nacos 内部 DNS，因此无法读取下发配置。
- 尚不能确认其中是否存在 PostgreSQL账号，也不能验证数据库权限。

开始代码迁移前必须从 dev 网络环境或运维 Secret 获得 PostgreSQL DSN。密码不得写入仓库、文档、日志或迁移清单。

dev 数据库需要两个角色：

1. `middle_agent_migrator`：仅发布迁移时使用，可建表、建索引和执行 Alembic。
2. `middle_agent_runtime`：服务运行账号，只允许连接、查询和业务 DML。

pgvector 扩展由 DBA 执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

运行账号至少需要：

```text
CONNECT ON DATABASE middle_agent
USAGE ON SCHEMA public
SELECT, INSERT, UPDATE, DELETE ON ALL APPLICATION TABLES
USAGE, SELECT ON APPLICATION SEQUENCES
```

本地开发不安装 PostgreSQL。代码直接连接 dev，但破坏性集成测试不能使用共享 `public`。应由 migrator 创建临时 Schema，例如 `agent_migration_test_<run_id>`；如果不允许创建 Schema，则申请独立测试库。

## 4. 目标技术架构

### 4.1 依赖

新增依赖：

```text
SQLAlchemy 2.x
asyncpg
Alembic
pgvector Python adapter
psycopg 3 + pool
langgraph-checkpoint-postgres
```

业务仓储使用 SQLAlchemy AsyncEngine + asyncpg。Alembic负责表结构版本。LangGraph 使用官方 PostgreSQL Checkpointer；它依赖 psycopg，不与业务连接池混用。

### 4.2 配置

新增环境变量：

```dotenv
DATA_STORE_PROVIDER=sqlite
DATABASE_URL=
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_POOL_TIMEOUT_SECONDS=10
DATABASE_STATEMENT_TIMEOUT_SECONDS=30
DATABASE_SCHEMA=public

VECTOR_STORE_PROVIDER=chroma
PGVECTOR_SCHEMA=public
PGVECTOR_TABLE=knowledge_chunks
PGVECTOR_HNSW_EF_SEARCH=100
```

灰度矩阵：

| 阶段 | 关系数据 | 向量数据 |
|---|---|---|
| 当前 | SQLite | Chroma |
| 阶段 A | PostgreSQL | Chroma |
| 阶段 B | PostgreSQL | pgvector shadow 验证 |
| 阶段 C | PostgreSQL | pgvector 正式读取 |

SQLite 和 Chroma 兼容开关在 dev 稳定前保留，便于回滚。

### 4.3 PostgreSQL 表组

所有表先放在 `public`，按现有名称或明确前缀避免冲突：

- Catalog：`knowledge_spaces`、`knowledge_domains`、`knowledge_sources`、`source_versions`、`source_files`、`code_symbols`、`chunk_catalog`、`sync_jobs`、`swagger_cache`、`encrypted_secrets`、`audit_events`。
- Agent：`agent_sessions`、`agent_messages`、`agent_pending_runs`、`agent_conversation_scopes`。
- Memory：现有记忆、候选、摘要、流程、实体、冲突、修复和任务表。
- Quality：现有 turn、feedback、span、annotation、eval case/run/result 表。
- Auth：现有匿名身份、飞书用户、OAuth state、用户 Session、个人 Token、会话归属和合并任务表。
- Integration：飞书事件处理表。
- Bug Graph：由 PostgreSQL Checkpointer 建立 checkpoint 相关表。

时间统一存为 `TIMESTAMPTZ`，JSON 字段统一为 `JSONB`，布尔值使用 `BOOLEAN`，不再以 SQLite 的整数或 JSON 字符串模拟。

### 4.4 pgvector 表

核心表：

```sql
CREATE TABLE knowledge_chunks (
    id TEXT PRIMARY KEY,
    source_id TEXT,
    version_id TEXT,
    domain_id TEXT,
    source_type TEXT NOT NULL,
    branch TEXT,
    commit_sha TEXT,
    relative_path TEXT,
    symbol_name TEXT,
    start_line INTEGER,
    end_line INTEGER,
    title TEXT,
    content TEXT NOT NULL,
    content_hash TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding VECTOR(1024) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

过滤索引：

```sql
CREATE INDEX ix_chunks_scope
ON knowledge_chunks(domain_id, branch, source_type, enabled);

CREATE INDEX ix_chunks_source
ON knowledge_chunks(source_id, version_id);

CREATE INDEX ix_chunks_symbol
ON knowledge_chunks(symbol_name, branch)
WHERE source_type = 'code' AND enabled = TRUE;

CREATE INDEX ix_chunks_metadata
ON knowledge_chunks USING gin(metadata);
```

批量数据迁移完成后再创建 HNSW，减少导入耗时：

```sql
CREATE INDEX ix_chunks_embedding_hnsw
ON knowledge_chunks
USING hnsw (embedding vector_cosine_ops);
```

向量检索使用 cosine distance，并先应用领域、branch、source type、source ID 和 enabled 过滤。代码符号精确召回继续优先于向量召回。

## 5. 实施阶段

### 阶段 0：访问与基线

操作：

1. 运维创建 `middle_agent` 并启用 vector 扩展。
2. 提供 migrator/runtime Secret，不把密码写入 `.env.example`。
3. 验证连接、事务、JSONB、vector(1024) 和临时 Schema 权限。
4. 记录 SQLite 各表 count、Chroma count、按 source/domain/branch 分布、Git 当前 commit 和知识源版本。
5. 运行当前完整测试和 5 条 Critical，保存基线延迟、引用和答案。

通过条件：只读连接、迁移连接、扩展和测试 Schema 全部可用；基线报告完整。

回滚：无业务改动。

### 阶段 1：连接层与 Alembic

操作：

1. 增加 PostgreSQL依赖和配置校验。
2. 建立 AsyncEngine、连接池、事务上下文和 readiness 检查。
3. 初始化 Alembic，生成首版 PostgreSQL schema。
4. 为 SQLite 和 PostgreSQL 建立相同仓储协议；API 和 Agent 不直接判断数据库类型。
5. 测试连接耗尽、statement timeout、事务回滚和启动失败降级。

通过条件：临时 Schema 可完整 upgrade/downgrade；应用在 SQLite 模式行为不变。

回滚：保持 `DATA_STORE_PROVIDER=sqlite`。

### 阶段 2：低耦合仓储迁移

先迁移：

1. 飞书事件去重仓储。
2. 会话 scope 和 pending run。
3. 用户认证与会话归属。
4. 只读历史查询。

任务领取使用 PostgreSQL 原子语句或 `FOR UPDATE SKIP LOCKED`，禁止先查后改。

通过条件：仓储契约测试同时覆盖 SQLite 和 PostgreSQL；飞书重复事件只处理一次；身份和会话隔离不变。

回滚：切回 SQLite provider，PostgreSQL 数据保留。

### 阶段 3：Catalog、同步任务和管理端

操作：

1. 迁移知识空间、领域、来源、规则、版本、文件、符号和 chunk catalog。
2. 迁移同步任务 claim/retry/recover 状态机。
3. 迁移加密凭证、Webhook hash、Swagger cache、管理员 Session 和审计事件。
4. 使用现有 `KNOWLEDGE_SECRET_MASTER_KEY` 解密/重新加密迁移数据；明文不落日志和临时文件。
5. 验证删除来源、版本替换和失败保留旧知识。

通过条件：各表 count 一致；同一任务不会被重复领取；Git 增量同步通过。

回滚：停止 Worker，切回 SQLite catalog，再启动服务。

### 阶段 4：Memory 与 Quality

操作：

1. 迁移会话摘要、候选、确认记忆、流程记忆、实体、冲突和提取任务。
2. 迁移真实问答、反馈、标注、span、回归用例和评测历史。
3. 将自动确认、过期清理、Worker 恢复改为 PostgreSQL事务。
4. 保持个人、会话、领域记忆作用域隔离。

通过条件：记忆数量和状态一致；64 条启用回归用例保持不变；个人记忆无跨用户泄漏。

回滚：停 Memory/Quality Worker 后切回 SQLite。

### 阶段 5：Agent Session 与 LangGraph

操作：

1. 实现符合 Agents SDK `SessionABC` 的 PostgreSQL Session，保留 history limit 和清理能力。
2. 迁移历史消息，但不迁移 SDK 内部无公开意义的临时对象。
3. 将 `AsyncSqliteSaver` 替换为 PostgreSQL Checkpointer。
4. 验证 Bug interrupt、同 conversation resume、24 小时过期和取消诊断。

通过条件：刷新页面后历史不丢；同会话上下文连续；Bug Graph 重启后可恢复；checkpoint 不含日志/代码正文和凭证。

回滚：会话和 Bug Graph 分别保留独立 provider 开关。

### 阶段 6：Chroma → pgvector 数据迁移

迁移前暂停 Source Worker，确认 `queued=0/running=0`，创建 Chroma 和 PostgreSQL备份。

迁移脚本按 500～1000 条分页：

1. 从 Chroma 读取 ID、document、metadata 和 embedding。
2. 验证每条向量维度为 1024，异常记录只输出 ID 和错误类型，不输出向量或正文。
3. 按原 ID 写入 `knowledge_chunks`，使用 upsert 保证幂等。
4. 不写 Embedding 中间 JSON/CSV，不重新调用 Embedding API。
5. 保留没有 catalog 对应项的历史向量并生成 orphan 统计，人工确认后处理。
6. 完成批量导入后创建 HNSW 和过滤索引并执行 `ANALYZE`。

通过条件：

- Chroma 和 pgvector 总 count 一致。
- 按 source/domain/branch/source type 的 count 一致。
- chunk ID 集合无缺失。
- document hash 和 metadata 抽样一致。
- 100 个代表问题 Top-10 overlap 不低于 90%。
- Critical 5/10/30 回归不低于迁移前基线。

回滚：`VECTOR_STORE_PROVIDER=chroma`，pgvector 表保留用于排查。

### 阶段 7：shadow、切换和稳定期

1. 正式查询仍返回 Chroma 结果，同时后台执行 pgvector shadow 查询。
2. 只记录脱敏的结果 ID、耗时和 overlap，不记录问题正文、chunk 内容或 Embedding。
3. 连续观察至少一个工作日。
4. 将正式读取切换为 pgvector，Chroma 继续保留至少一个发布周期。
5. 恢复 Git Webhook、10 分钟补偿扫描、文档版本替换和 Swagger 更新。

通过条件：readiness 中 `postgres` 和 `pgvector` 均 available；P90 检索延迟不高于原方案 20%；增量同步、删除和回滚演练通过。

### 阶段 8：dev 运维接入

对齐 Java dev 服务：

- 使用现有 Jenkins/Docker/Kubernetes模板。
- Secret 注入 `DATABASE_URL`，不复制到镜像。
- 持久化目录仅保留 Git mirror、上传文件和迁移期 Chroma 回滚副本。
- 日志输出到公司采集方式，不记录 DSN、凭证、Prompt、正文和 Embedding。
- 配置 `/health/live`、`/health/ready`。
- dev 保持 replica=1、Uvicorn workers=1、飞书单实例。
- PostgreSQL备份、慢查询、连接池和磁盘告警交由现有数据库运维体系。

## 6. 数据迁移顺序

关系数据按外键依赖导入：

```text
space/domain
  → source/rule/version
  → file/symbol/chunk catalog
  → cache/secret/audit/job

identity
  → session/token/conversation owner
  → agent session/message/scope/pending run

memory candidate/summary
  → confirmed memory/procedure/entity/conflict/job

quality turn
  → feedback/span/annotation
  → eval case/run/result
```

所有导入均使用稳定主键和幂等 upsert。迁移完成后校正 sequence，避免新记录主键冲突。

## 7. 测试矩阵

### 仓储测试

- 每个仓储协议对 SQLite 和 PostgreSQL运行同一组契约测试。
- 外键、唯一约束、级联、JSONB、时间和事务回滚。
- Worker 并发 claim、stale recovery、重试和取消。

### pgvector 测试

- cosine 距离、Top-K、metadata filter、branch/domain/source过滤。
- 精确 symbol 优先、BM25 + Vector + RRF + Rerank兼容。
- 分页 get、批量 upsert、metadata update、删除和 count。
- 1024 维校验、空向量拒绝、重复 ID 幂等。

### 业务回归

- 审批流接口契约和代码定位。
- 指标平台说明与 MCP 查询。
- 工作流变量、异常分支和连接器。
- Bug develop/test/prod 日志与代码分支映射。
- 飞书线程隔离、网页历史、记忆隔离和 citation详情。
- Critical 5/10/30 以及安全用例。

## 8. 上线与回滚步骤

切换窗口：

1. 禁止管理端新增/删除来源。
2. 等待同步、记忆和评测任务归零。
3. 停止服务，备份全部 SQLite、Chroma 和 PostgreSQL。
4. 执行 Alembic upgrade。
5. 执行关系数据迁移与校验。
6. 执行 Chroma→pgvector迁移与校验。
7. 启动单 Worker，验证 readiness。
8. 运行 smoke、Critical 5 和真实接口问题。
9. 开放网页和飞书流量。

触发回滚的条件：

- 任何核心表 count 或 ID 不一致。
- Top-10 overlap 低于 90%。
- citation 无法读取详情。
- Git 增量同步出现重复或丢失。
- 用户、会话或记忆发生跨作用域访问。
- P90 检索延迟超过基线 20% 且无法在窗口内修复。

回滚操作：停止服务，将 provider 切回 SQLite + Chroma，恢复原配置并启动；PostgreSQL数据不删除，用于差异诊断。

## 9. 实施交付物

- PostgreSQL/Alembic schema 与迁移版本。
- SQLite/PostgreSQL共用仓储协议与实现。
- PostgreSQL Agent Session 和 LangGraph Checkpointer。
- pgvector repository 和 Chroma兼容 provider。
- SQLite→PostgreSQL、Chroma→pgvector幂等迁移脚本。
- count/hash/分布/Top-K 对比报告。
- dev 部署配置、健康检查、回滚脚本和运维说明。
- Critical 5/10/30 迁移前后对比报告。

## 10. 下一步

第一步不是修改业务代码，而是解决 dev 连接门禁：在能解析 Nacos 内部 DNS 的网络环境获取 PostgreSQL Secret，或由运维单独提供 `middle_agent` 的 migrator/runtime Secret。拿到连接后只验证权限和临时 Schema，不立即创建正式表；验证通过后再开始阶段 1。
