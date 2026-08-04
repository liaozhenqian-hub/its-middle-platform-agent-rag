# 中台智能问答 Agent：dev Kubernetes 上线运维交接手册

## 1. 文档用途

本文用于将 `its-middle-platform-agent-rag` 发布到公司 dev Kubernetes 环境，供运维、DBA、网络和研发共同完成上线准备、部署、验收与回滚。

本服务包含网页问答、飞书机器人、知识源同步、审批流/工作流/指标平台专家、Bug 日志诊断、长期记忆和质量评测。首期必须单实例运行。

本文不包含任何数据库密码、模型 Key、GitLab Token、Grafana Token、飞书 Secret、MCP Token 或业务数据。所有敏感值必须通过 GitLab CI Protected Variables 或 Kubernetes Secret 注入。

## 2. 发布信息

| 项目 | 建议值 |
|---|---|
| 应用名称 | `middle-platform-agent-rag` |
| GitLab 项目 | `nexus/its-middle-platform-agent-rag` |
| dev 分支 | `master`，或由运维映射到现有 dev 发布分支 |
| Kubernetes namespace | `api-center-develop` |
| 容器端口 | `8000` |
| Service 类型 | `ClusterIP` |
| 副本数 | `1` |
| 发布策略 | `Recreate` |
| Uvicorn Worker | `1` |
| 时区 | `Asia/Shanghai` |
| 健康检查 | `/health/live`、`/health/ready` |
| 数据库 | PostgreSQL `middle_agent` |
| 数据库 Schema | `public` |
| 向量存储 | PostgreSQL pgvector |
| 文件存储 | PVC 挂载 `/app/storage` |

应用只监听 8000 端口。员工访问域名、Ingress/Gateway、TLS 证书和 DNS 由现有 dev 网关体系配置，不在代码仓库内硬编码。

## 3. 发布架构

```text
GitLab CI
  -> 后端测试、前端测试和前端构建
  -> buildah/nerdctl 构建镜像
  -> 公司私有镜像仓库
  -> 更新 Kubernetes YAML 仓库中的 Kustomize image
  -> ArgoCD 同步 dev

员工浏览器 -> 内网网关/Ingress -> ClusterIP Service:8000 -> 单 Pod
飞书机器人 -> Pod 主动建立飞书长连接
Pod -> PostgreSQL/pgvector、模型、Embedding、GitLab、Grafana、MCP
```

首期禁止扩容到两个或更多 Pod。当前飞书长连接、Source Worker、Eval Worker、Memory Worker、Git mirror 协调和进程内检索缓存均由同一进程持有。滚动发布或多副本会造成重复消费、重复任务和缓存不一致。

## 4. 运维需要提前创建的资源

### 4.1 Kubernetes Workload

- `Deployment/middle-platform-agent-rag`
- `Service/middle-platform-agent-rag`
- `ConfigMap/middle-platform-agent-rag-config`
- `Secret/middle-platform-agent-rag-secrets`
- `PersistentVolumeClaim/middle-platform-agent-rag-storage`

### 4.2 资源规格

| 资源 | requests | limits |
|---|---:|---:|
| CPU | `1` | `2` |
| Memory | `2Gi` | `4Gi` |
| PVC | `20Gi` | 按实际知识源增长扩容 |

PVC 建议使用 `ReadWriteOnce`，挂载到 `/app/storage`。Pod 使用非 root 用户运行，需通过 `fsGroup` 或存储目录权限确保运行用户可以写入该目录。

PVC 保存：

- Git mirror 和同步工作目录；
- 上传的产品文档和 Swagger 缓存文件；
- 必须跨 Pod 重启保留的临时导入状态；
- 如启用文件日志，保存轮转后的应用日志。

PostgreSQL 表和 pgvector 向量不存放在 PVC。

### 4.3 Docker 镜像交付规范

镜像由 GitLab CI 构建并推送到公司私有镜像仓库，运维只部署不可变镜像 tag，不在 Kubernetes 节点上拉取源码或现场安装依赖。

镜像必须满足：

- 使用公司批准的 Python 3.11 Linux 基础镜像；
- 在构建阶段完成后端依赖安装、前端依赖安装和 `web` 生产构建；
- 最终镜像包含 Python 应用、`web/dist` 和必要的迁移文件，不包含 Node.js 构建缓存；
- 以非 root 用户运行，工作目录为 `/app`；
- 仅声明容器端口 `8000`；
- 启动命令固定为单 Worker：

```text
python -m uvicorn knowledge.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

- 将 `/app/storage` 作为持久化挂载点，代码、前端静态资源和 Python 依赖保持只读；
- stdout/stderr 作为默认日志出口，不把容器临时文件系统当作日志或业务数据存储；
- 镜像内不得包含 `.env`、本地数据库、Chroma 数据、运行日志、Git 凭证、测试报告、IDE 配置或任何 Secret；
- 镜像 tag 使用 commit SHA 或流水线生成的不可变版本，不使用 `latest` 作为发布和回滚依据；
- CI 构建完成后执行镜像漏洞扫描和凭证扫描，高危问题或明文凭证命中时阻断发布。

建议由 CI 验证镜像，而不是依赖开发机验证：

```bash
docker run --rm --entrypoint python <image>:<tag> -c "import knowledge.api.app"
docker inspect <image>:<tag>
```

第一条只验证应用模块可以装载，不连接真实 dev 数据源；完整 readiness 和业务 Smoke 必须在 Kubernetes Secret、ConfigMap、PVC 与网络策略就绪后执行。

## 5. PostgreSQL 与 pgvector

### 5.1 数据库要求

- PostgreSQL 15 或更高版本；
- 数据库：`middle_agent`；
- Schema：`public`；
- 已启用 pgvector 扩展；
- 当前向量维度：1024；
- Pod 到数据库地址和端口网络可达；
- 数据库要求 SSL 时使用 `DATABASE_SSL_MODE=require`。

DBA 验证：

```sql
SELECT extversion FROM pg_extension WHERE extname = 'vector';
SELECT current_database(), current_schema();
```

禁止在工单、聊天、Git 仓库或 Pod 日志中粘贴数据库密码和完整 DSN。

### 5.2 运行权限

运行账号需要：

- 连接 `middle_agent`；
- 使用 `public` Schema；
- 对应用表执行 SELECT、INSERT、UPDATE、DELETE；
- 使用应用 sequence；
- 使用 pgvector 查询和索引。

生产前建议拆分迁移账号和运行账号。dev 首次上线可以复用已批准的 dev 账号，但不能将密码写进镜像。

### 5.3 数据库迁移

数据库结构由 Alembic 管理。升级命令必须在单独的迁移 Job 或发布前运维步骤执行：

```bash
alembic upgrade head
```

禁止让每个 Pod 在启动时自动执行 Schema 迁移。迁移失败时不得继续启动新版本。

## 6. ConfigMap 配置

建议的 dev 非敏感配置如下：

```dotenv
TZ=Asia/Shanghai
LOG_LEVEL=INFO
DATA_STORE_PROVIDER=postgres
VECTOR_STORE_PROVIDER=pgvector
VECTOR_SHADOW_ENABLED=false
BUG_GRAPH_CHECKPOINT_PROVIDER=postgres
DATABASE_SSL_MODE=require
DATABASE_SCHEMA=public
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_POOL_TIMEOUT_SECONDS=10
DATABASE_POOL_RECYCLE_SECONDS=1800
DATABASE_STATEMENT_TIMEOUT_SECONDS=30
PGVECTOR_SCHEMA=public
PGVECTOR_TABLE=vector_entries
PGVECTOR_BATCH_SIZE=500
PGVECTOR_HNSW_EF_SEARCH=100
KNOWLEDGE_STORAGE_ROOT=/app/storage
FRONTEND_DIST=/app/web/dist
SOURCE_WORKER_ENABLED=true
FEISHU_BOT_ENABLED=true
RETRIEVAL_WARMUP_ENABLED=true
AGENT_TRACING_ENABLED=false
```

说明：

- 不要把本地 Telepresence 使用的短连接回收参数复制到 dev；
- dev 的 Bug Graph checkpoint 必须走 PostgreSQL，不使用本地 SQLite；
- 首次启动如 BM25 预热影响探针，可临时关闭 `RETRIEVAL_WARMUP_ENABLED`，验证后再开启；
- 日志默认输出 stdout/stderr，由 Kubernetes 日志采集；如保留文件日志，路径必须在 `/app/storage/logs` 下。

其余非敏感业务开关按 `.env.example` 选择，并通过 ConfigMap 管理。

## 7. Secret 键名清单

创建 `Secret/middle-platform-agent-rag-secrets`，只注入实际启用功能需要的键。仓库不保存 Secret YAML 的值。

### 7.1 数据库

优先提供：

- `DATABASE_URL`

或者提供以下拆分键：

- `PGHOST`
- `PGPORT`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`

二者同时存在时以 `DATABASE_URL` 为准。

### 7.2 模型与 Embedding

- `AGENT_OPENAI_API_KEY` 或当前 Agent provider 对应的 Key
- `AGENT_OPENAI_BASE_URL`
- `DEEPSEEK_API_KEY`
- `EMBEDDING_API_KEY`
- `RERANK_API_KEY`

Base URL 是否放入 Secret 由公司规范决定；Key 必须放 Secret。

### 7.3 内部系统

- `GITLAB_ACCESS_TOKEN`
- `GRAFANA_LOG_BEARER_TOKEN`
- `METRIC_MCP_BEARER_TOKEN`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_OAUTH_APP_ID`
- `FEISHU_OAUTH_APP_SECRET`
- `KNOWLEDGE_SECRET_MASTER_KEY`
- `ADMIN_PASSWORD_HASH`

未启用的集成不得为了“配置完整”放入无关凭证。

## 8. 网络放行清单

Pod 需要访问以下目标：

| 方向 | 目标 | 用途 |
|---|---|---|
| 出站 | PostgreSQL dev | 关系数据、pgvector、LangGraph |
| 出站 | 模型中转站或 DeepSeek | Agent 推理 |
| 出站 | Embedding/Rerank 服务 | 知识同步和检索 |
| 出站 | 公司 GitLab | Git 知识源同步 |
| 出站 | Grafana/Loki | Bug trace 日志查询 |
| 出站 | 指标 MCP | 指标数据查询 |
| 出站 | 飞书开放平台 | 机器人长连接、回复、OAuth |
| 出站 | 已登记 Swagger 地址 | 接口契约同步 |
| 入站 | 内网网关/Ingress | 员工网页/API 访问 |

飞书机器人通过 Pod 主动建立长连接接收消息，不需要飞书公网回调直接访问 Pod。

## 9. 探针配置

### 9.1 Startup Probe

- Path：`/health/live`
- Port：8000
- `periodSeconds: 10`
- `failureThreshold: 30`
- 最长允许约 5 分钟冷启动

### 9.2 Liveness Probe

- Path：`/health/live`
- Port：8000
- `periodSeconds: 20`
- `timeoutSeconds: 3`
- `failureThreshold: 3`

### 9.3 Readiness Probe

- Path：`/health/ready`
- Port：8000
- `periodSeconds: 10`
- `timeoutSeconds: 5`
- `failureThreshold: 6`

`/health/live` 只判断进程存活；`/health/ready` 会显示 PostgreSQL、pgvector、模型、MCP、Grafana、Bug Graph、飞书、Catalog 和 Worker 等组件状态。

## 10. GitLab CI 和 GitOps

沿用 Java 服务的发布模式：

1. GitLab Runner 执行 pytest、Vitest 和前端 build；
2. 使用 buildah 或 nerdctl 构建镜像；
3. 镜像推送到公司私有仓库；
4. 克隆 Kubernetes YAML 仓库；
5. 使用 `kustomize edit set image` 更新镜像；
6. 提交并推送 GitOps 变更；
7. ArgoCD sync 并等待应用 Healthy。

建议 GitOps 路径：

```text
api-center/middle-platform-agent-rag/env/oci-develop
```

建议 ArgoCD 应用名称：

```text
oci-middle-platform-agent-rag-develop
```

如果运维采用其他名称，应同时修改 GitLab CI 变量、GitOps 路径和 ArgoCD 应用名，三处必须一致。

CI 需要由运维配置现有同类变量，至少包括：

- 镜像仓库地址、账号和密码；
- Kubernetes YAML 仓库账号和密码；
- ArgoCD 地址、账号或 Token；
- 应用名、GitOps 目录、发布分支；
- Python/Node 基础镜像地址（如 Runner 不允许访问公共镜像）。

CI 日志不得打印上述变量值。

## 11. 发布前检查

- [ ] PostgreSQL 和 pgvector 可从集群 Pod 网络访问；
- [ ] Alembic 已执行到最新版本；
- [ ] Kubernetes Secret 已创建；
- [ ] ConfigMap 不包含密码和 Token；
- [ ] PVC 已绑定且非 root 用户可写；
- [ ] 镜像已通过后端测试、前端测试和生产构建；
- [ ] 镜像扫描未发现高危漏洞或明文凭证；
- [ ] Deployment 副本数为 1，策略为 Recreate；
- [ ] Service、网关和员工访问域名已配置；
- [ ] PostgreSQL、模型、Embedding、GitLab、Grafana、MCP、飞书网络已放行；
- [ ] 旧服务同步任务、评测任务和记忆任务没有 running 状态；
- [ ] 已记录当前镜像版本和数据库备份点。

## 12. 发布步骤

1. 暂停旧实例的管理端写操作和知识同步；
2. 确认同步、评测和记忆任务 `queued=0/running=0`；
3. 备份 PostgreSQL，并记录 pgvector count；
4. 运行 Alembic迁移 Job；
5. GitLab CI 构建并推送不可变镜像 tag；
6. CI 更新 GitOps image；
7. ArgoCD 同步；
8. 等待 Startup、Readiness 通过；
9. 确认只有一个 Pod 和一个飞书长连接；
10. 执行第 13 节验收；
11. 验收通过后恢复知识同步和用户访问。

## 13. 上线验收

### 13.1 基础检查

```bash
curl -fsS http://<service-address>:8000/health/live
curl -fsS http://<service-address>:8000/health/ready
```

预期：

- live 返回 `live`；
- readiness 总状态为 ready；
- database/postgres 为 available；
- vector_store/pgvector 为 available；
- bug_graph、catalog、worker 为 available；
- 启用的 Grafana、飞书和 MCP 为 available。

### 13.2 业务 Smoke

- [ ] 网页能新建、刷新和恢复会话；
- [ ] 审批流接口查询能返回代码/文档证据；
- [ ] 指标平台知识说明不误调用实时 MCP；
- [ ] 指标实时数据按状态机完成应用确认；
- [ ] 工作流问题能检索对应领域；
- [ ] Bug 问题携带环境和 trace ID 后能查询 Grafana 并结合代码；
- [ ] 飞书私聊和群聊回复格式正确且不串会话；
- [ ] 引用详情可以加载，用户界面不显示内部 chunk ID；
- [ ] 个人记忆不跨用户，领域记忆不泄露个人 owner；
- [ ] Git develop/master 知识源分支过滤正确；
- [ ] 新 Git 同步任务能写入 PostgreSQL 和 pgvector。

### 13.3 数据核对

- Catalog、会话、记忆和质量数据 count 与发布前一致；
- `vector_entries` 总数和按 collection/source/branch 分布符合迁移快照；
- 没有重复 running 任务；
- Pod 重启后历史会话、Bug checkpoint 和向量数据仍可访问。

## 14. 监控与告警

建议接入：

- Pod CPU、内存、重启次数和 OOM；
- HTTP 请求量、P50/P90/P99、5xx 和 SSE 中断；
- PostgreSQL 活跃连接、等待、慢查询和 statement timeout；
- pgvector 查询延迟和 Top-K 失败；
- 同步/评测/记忆队列 queued、running、failed 和 stale；
- 飞书长连接在线状态和重复事件；
- 模型、Embedding、Rerank、MCP 和 Grafana 外部调用耗时/错误率；
- PVC 使用率；
- `/health/ready` 组件状态变化。

日志只允许记录状态、组件名、节点名、耗时、异常类型和脱敏标识。不得记录凭证、完整 Prompt、Embedding、代码正文、原始 Grafana 日志或 MCP 完整输出。

## 15. 回滚方案

触发条件：

- readiness 持续失败；
- PostgreSQL/pgvector 数据不一致；
- 飞书重复消费；
- 会话或记忆跨用户；
- 核心问答无法使用；
- 5xx、超时或资源使用明显高于基线；
- 数据库迁移校验失败。

回滚步骤：

1. 停止用户访问和后台 Worker 写入；
2. 在 GitOps 仓库回退到上一个已验证镜像 tag；
3. ArgoCD sync；
4. 等待单 Pod readiness；
5. 如果本次包含不兼容数据库迁移，按对应 Alembic 和数据库备份方案恢复；
6. 核对 Catalog、会话、任务和向量 count；
7. 运行基础检查和 Critical smoke 后恢复流量。

禁止为了快速恢复而删除 PostgreSQL 表、`vector_entries`、PVC、知识源或质量记录。

## 16. 运维交接确认

运维确认以下实际值后才能首次发布：

| 项目 | 运维填写 |
|---|---|
| 私有镜像完整名称 |  |
| GitOps 仓库及目录 |  |
| ArgoCD 应用名 |  |
| namespace |  |
| storageClass |  |
| 内网访问域名 |  |
| PostgreSQL Service/DNS |  |
| ConfigMap 名称 | `middle-platform-agent-rag-config` |
| Secret 名称 | `middle-platform-agent-rag-secrets` |
| PVC 名称 | `middle-platform-agent-rag-storage` |
| 首次发布时间 |  |
| 回滚镜像 tag |  |

研发负责提供镜像构建文件、Kustomize 模板、CI 脚本、数据库迁移版本和业务 Smoke；运维负责集群资源、Secret、网络、GitOps、ArgoCD、域名、监控和备份。
