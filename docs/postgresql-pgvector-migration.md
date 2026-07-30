# 中台 Agent PostgreSQL + pgvector 迁移手册

## 目标

存储按以下顺序灰度，不允许跨阶段直接切换：

```text
SQLite + Chroma
  -> PostgreSQL + Chroma
  -> PostgreSQL + Chroma（pgvector shadow）
  -> PostgreSQL + pgvector
```

关系库和向量库使用独立 provider 开关。任何阶段失败都不得自动修改 provider，也不得删除 SQLite、Chroma 或 PostgreSQL 中的诊断数据。

## 已验证基线

- 数据库：`middle_agent`
- PostgreSQL：16（兼容要求 15+）
- pgvector：0.8.0
- 向量维度：1024
- 关系迁移计划：49 张业务表
- 本次快照关系数据：114,403 行
- `metric_platform_knowledge`：39,598 条
- `middle_platform_memories`：1 条
- 关系校验：count、主键集合、状态分布、抽样 hash、外键均无差异
- 向量校验：count、ID 集合、抽样正文和 metadata hash 均无差异

这些数量只代表演练快照。正式迁移必须重新生成快照并以当时的精确 count 为准。

## 安全边界

- `DATABASE_URL` 优先；否则使用 `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD` 构造连接。
- DSN、账号、密码、Token、Prompt、正文、日志原文和 embedding 不得进入日志或迁移报告。
- 加密凭证只复制密文，不解密，不写中间文件。
- Chroma 向量分页直接写入 PostgreSQL，不生成 JSON/CSV，不调用 Embedding API。
- 迁移错误只记录阶段、表或 collection、异常类型和聚合数量，不输出内部 chunk ID。
- 临时演练只允许使用独立 Schema，禁止在 `public` 中试跑。

## 配置

```dotenv
DATA_STORE_PROVIDER=sqlite
DATABASE_URL=
PGHOST=
PGPORT=5432
PGDATABASE=middle_agent
PGUSER=
PGPASSWORD=
DATABASE_SCHEMA=public
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_POOL_TIMEOUT_SECONDS=10
PGVECTOR_POOL_MAX_IDLE_SECONDS=300
DATABASE_STATEMENT_TIMEOUT_SECONDS=30
DATABASE_MIGRATION_BATCH_SIZE=5000

VECTOR_STORE_PROVIDER=chroma
VECTOR_SHADOW_ENABLED=false
VECTOR_SHADOW_SAMPLE_RATE=1.0
PGVECTOR_SCHEMA=public
PGVECTOR_TABLE=vector_entries
PGVECTOR_BATCH_SIZE=500
PGVECTOR_HNSW_EF_SEARCH=100
```

dev 当前可复用已有账号，但生产前必须拆分 migrator 和 runtime 权限。LangGraph 表由官方 PostgreSQL Checkpointer 的 `setup()` 管理，不复制进业务 Alembic。

## CLI

所有写操作默认 dry-run，只有显式 `--apply` 才写入：

```powershell
python -m knowledge.storage_cli migrate-relational
python -m knowledge.storage_cli migrate-relational --apply
python -m knowledge.storage_cli verify-relational --sample-size 100

python -m knowledge.storage_cli migrate-checkpoints
python -m knowledge.storage_cli migrate-checkpoints --apply

python -m knowledge.storage_cli migrate-vectors
python -m knowledge.storage_cli migrate-vectors --apply
python -m knowledge.storage_cli verify-vectors --sample-size 100
python -m knowledge.storage_cli build-vector-index --apply
python -m knowledge.storage_cli shadow-report
```

本地 Telepresence 只用于连通性、正确性和断点续传验证。正式迁移耗时必须在 dev Pod 内直连数据库复测，不能用 Telepresence 的延迟判断 15 分钟窗口是否达标。

## 临时 Schema 演练

1. 用不可冲突的名字创建临时 Schema，例如 `agent_migration_test_<run_id>`。
2. 执行 Alembic `upgrade head`，再重复执行一次确认幂等。
3. 通过 SQLite backup API 创建一致性快照，不直接迁移仍在写入的文件。
4. 执行关系迁移，模拟中断后用相同快照恢复。
5. 校验 count、完整主键集合、状态分布、外键和规范化抽样 hash。
6. 使用现有 Chroma embedding 迁移两个 collection。
7. 导入完成后创建 HNSW，并执行 `ANALYZE`。
8. 校验向量 count、完整 ID 集合和抽样正文/metadata hash。
9. 运行仓储契约、业务 smoke 和 Critical 5/10/30。
10. 执行 Alembic `downgrade base`，最后删除临时 Schema。

关系迁移使用稳定 run ID、持久化 cursor 和幂等 upsert。向量迁移每批提交，可按已持久化 cursor 恢复；不得根据不完整或非确定性 ID 集合盲目跳过数据。

## 关系库正式切换（15 分钟窗口）

### 窗口前

1. 完成临时 Schema 全流程演练和回滚演练。
2. 在 dev Pod 内直连数据库测量全量迁移时间，预留至少 30% 余量。
3. 确认 SQLite 和 Chroma 备份目录有足够空间且备份可读取。
4. 记录迁移前应用版本、配置版本、知识源版本和 Critical 基线。
5. 确认 `DATA_STORE_PROVIDER=sqlite`、`VECTOR_STORE_PROVIDER=chroma`。

### 队列归零

开始停机前必须确认以下任务均为 `queued=0`、`running=0`：

- 知识源同步任务
- 质量评测任务
- 记忆提取与索引修复任务
- 身份合并和恢复任务

若无法归零，停止进入切换窗口。不得在任务写入过程中复制 SQLite。

### 窗口内

1. 停止网页写入、飞书消费、Source Worker、Eval Worker 和 Memory Worker。
2. 确认单 Uvicorn Worker 和单飞书实例均已停止写入。
3. 使用 SQLite backup API 生成最终快照，同时备份 Chroma。
4. 对目标 Schema 执行 Alembic `upgrade head`。
5. 执行关系迁移并校正 `agent_messages` sequence。
6. 执行 `verify-relational`；任一 mismatch 都立即回滚。
7. 迁移有效 LangGraph checkpoint，按 24 小时 TTL 跳过过期会话。
8. 设置 `DATA_STORE_PROVIDER=postgres`，保持 `VECTOR_STORE_PROVIDER=chroma`。
9. 启动单实例服务，检查 database、catalog、worker、model、Chroma、MCP、Grafana 和 bug_graph readiness。
10. 运行登录、历史会话、飞书线程、审批流、指标平台、工作流、Bug interrupt/resume 和 Critical 5。
11. 通过后恢复流量和 Worker。

## 关系库回滚

出现以下任一情况立即回滚：

- 关系校验不一致
- readiness 关键组件 unavailable
- 会话、身份、个人记忆出现跨 owner 或跨 scope 访问
- 同一任务被重复领取
- 飞书事件重复消费
- Critical 或核心 smoke 低于迁移前基线
- 窗口剩余时间不足以完成验证

回滚步骤：

1. 再次停止所有写入和 Worker。
2. 设置 `DATA_STORE_PROVIDER=sqlite`、`VECTOR_STORE_PROVIDER=chroma`。
3. 恢复切换前 SQLite/Chroma 快照和原配置版本。
4. 启动单实例服务并运行 readiness、Critical 5 和核心 smoke。
5. 恢复流量。
6. PostgreSQL 数据只读保留用于差异诊断，不删除、不反向覆盖 SQLite。

## pgvector shadow 与切换

关系库稳定后才进入向量阶段：

1. 暂停 Source Worker，聊天继续使用 Chroma。
2. 迁移两个 collection 的现有 ID、正文、metadata 和 embedding。
3. 创建 HNSW 并执行 `ANALYZE`，随后恢复 Source Worker。
4. 设置 `VECTOR_SHADOW_ENABLED=true`，正式答案仍以 Chroma 为准。
5. shadow 至少运行一个完整工作日。
6. 报告只包含样本数、主/影子平均与 P90 延迟、Top-K overlap 和失败率。
7. 使用 100 个代表问题验证 Top-10 overlap >= 90%，pgvector P90 不高于 Chroma 基线 20%。
8. Critical 5/10/30、引用详情、分支过滤和个人记忆隔离必须不低于基线。
9. 达标后设置 `VECTOR_STORE_PROVIDER=pgvector`；否则继续使用 Chroma。

SQLite 和 Chroma 至少只读保留一个发布周期。观察期结束前不得归档旧存储。

## 上线前仍需完成

- 在 dev Pod 内完成一次全量关系和向量迁移计时。
- 运行至少一个工作日的 shadow 报告。
- 完成 100 题 Top-10 overlap 与 P90 对比。
- 完成 Critical 5/10/30 和前后端完整回归。
- 将 dev 共用数据库账号拆成 migrator/runtime 账号。
- 接入 PostgreSQL 备份、慢查询、连接池、容量和告警。
