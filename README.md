# its-middle-platform-agent-rag

企业中台知识 RAG 与多领域 Agent 服务。现有检索链路保持独立：DeepSeek Query Rewrite、
Jieba + BM25、Chroma 向量召回、RRF 融合和 Qwen Rerank。Agent 层只把这条链路包装成固定领域工具。

## Agent 架构

Manager Agent 负责对话与最终回答，通过 `Agent.as_tool()` 委托三个专家：

- 指标平台专家：指标平台 RAG + 指标 MCP
- 审批流专家：审批流 RAG
- 工作流专家：工作流 RAG

Manager 遇到企业内部事实必须调用专家，跨领域问题可顺序调用多个专家。专家不能修改固定的
`app_id=middle-platform` 和领域过滤条件。指标 MCP 只开放 10 个静态 allowlist 只读工具；服务端未来
新增的工具不会自动暴露给 Agent。

第一版没有真实写工具，但已实现 `needs_approval=True` 所需的暂停、SQLite 持久化、批准/拒绝和恢复。

## 环境要求

需要 Python `>=3.11.9`。CPython 3.11.0 的 `typing` 实现无法导入 `openai-agents==0.18.2` 使用的
泛型 Tool Context，请升级 Python 补丁版本。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

前端需要 Node.js 20 或更高版本：

```powershell
Set-Location web
npm ci
npm run build
Set-Location ..
```

至少配置一种 Agent 模型：

```dotenv
AGENT_MODEL_PROVIDER=openai
AGENT_MODEL_NAME=gpt-5.5
AGENT_OPENAI_API_KEY=<fill-locally>
AGENT_OPENAI_BASE_URL=https://www.codex2api.com/v1
```

当前部署通过 OpenAI Responses API 兼容中转站调用 GPT。密钥只填写在项目根目录的
`.env` 文件中，不要提交到代码、日志或示例配置。修改模型配置后需要重启 FastAPI 服务。

或复用现有 DeepSeek 配置：

```dotenv
AGENT_MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=<fill-locally>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_CHAT_MODEL=deepseek-v4-flash
DEEPSEEK_REASONING_MODEL=deepseek-v4-pro
DEEPSEEK_REASONING_ENABLED=true
```

Manager, domain specialists, query rewrite, and Bug intake use Flash with thinking
disabled. Only the tool-free final Bug diagnosis writer uses Pro with thinking enabled.

指标 MCP 使用 Streamable HTTP：

```dotenv
METRIC_MCP_ENABLED=true
METRIC_MCP_URL=http://127.0.0.1:8080/mcp/messages
METRIC_MCP_BEARER_TOKEN=<fill-locally>
```

MCP 连接失败时服务降级启动，知识库问答仍可用，实时指标查询会明确提示不可用。曾出现在聊天、日志
或其他非密钥系统中的 Bearer Token 必须先轮换。

## 知识源管理配置

管理端把来源目录保存在 `storage/knowledge_catalog.db`，Git mirror、上传文件和临时 worktree 默认保存在
`storage/`。要启用管理端，至少配置：

```dotenv
KNOWLEDGE_CATALOG_DB=storage/knowledge_catalog.db
KNOWLEDGE_STORAGE_ROOT=storage
FRONTEND_DIST=web/dist

ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<Argon2 hash>
KNOWLEDGE_SECRET_MASTER_KEY=<32-byte URL-safe base64 key>

GITLAB_BASE_URL=https://gitlab.example.internal
GITLAB_ACCESS_TOKEN=<read_api + read_repository token>
SOURCE_WORKER_ENABLED=true
GIT_SYNC_INTERVAL_SECONDS=600

SWAGGER_ALLOWED_HOSTS=swagger.example.internal,api-docs.example.internal
```

管理员密码哈希和 AES-256-GCM 主密钥分别这样生成：

```powershell
python -m knowledge.cli hash-admin-password
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

`KNOWLEDGE_SECRET_MASTER_KEY` 必须稳定保管；丢失或随意更换后，SQLite 中已有的 Swagger 凭证和
Webhook Secret 将无法解密。GitLab Token 只授予 `read_api` 和 `read_repository`，由进程环境临时注入
Git 命令，禁止写进 clone URL、源码、日志或数据库。

Swagger 只读取登记 URL 的 OpenAPI 规范，不调用规范中的业务接口。`SWAGGER_ALLOWED_HOSTS` 必须明确
列出允许的主机名，不能使用 `*`；管理 API 只返回“凭证已配置”，不会回传密码或 Token。

## 知识源能力

- GitLab：管理员逐个选择项目和一个分支，通过目录规则映射到指标平台、审批流、工作流，未匹配目录进入 `shared`。
- 产品文档：支持 Markdown、TXT、DOCX 和文本型 PDF，也可上传安全 ZIP；扫描 PDF 暂不支持 OCR。
- Swagger：每次工具调用通过 ETag/Last-Modified 检查规范更新，远端失败时使用带时间戳的最后成功缓存。
- 同步：Webhook 触发增量任务，10 分钟远端扫描兜底；任务失败最多重试 3 次。

管理员页面为 `/admin`，客户端问答页面为 `/chat`。客户端接口不能新增、修改、同步或删除知识源。

## 启动服务

### 飞书机器人

飞书机器人使用官方 SDK 的长连接模式，不需要公网 Webhook。启用前必须先在飞书开放平台重置曾经暴露过的
App Secret，并把新凭证只写入服务器本地 `.env`：

```dotenv
FEISHU_BOT_ENABLED=true
FEISHU_APP_ID=<fill-locally>
FEISHU_APP_SECRET=<fill-locally>
FEISHU_EVENT_DB=storage/feishu_bot.db
FEISHU_REPLY_MAX_CHARS=3500
FEISHU_GROUP_REQUIRE_MENTION=true
FEISHU_AGENT_TIMEOUT_SECONDS=180
```

飞书开放平台需要完成以下配置：

1. 选择“使用长连接接收事件”。
2. 订阅 `im.message.receive_v1`。
3. 订阅 `im.message.reaction.created_v1` 和 `im.message.reaction.deleted_v1`，用于收集机器人回答的表态反馈。
4. 申请并发布私聊/群聊消息读取、“以应用身份发送消息”和通讯录用户基本信息读取权限。缺少通讯录权限时仍保存用户 open ID，但用户名为空。
5. 把机器人加入测试群。

私聊文本会直接进入 Agent；群聊默认只有 `@机器人` 才会触发。飞书 `chat_id` 会映射为本地
`feishu:<chat_id>` 会话，同一群的上下文共享且通过现有会话锁串行执行。机器人只处理文本消息，长回答
按段落分片回复，引用只附带类型、标题和公开 source ID，不附带正文、原始日志或工具输出。

事件去重状态保存在 `storage/feishu_bot.db`，只记录事件/消息/群 ID、状态、次数和异常类型。用户原始问题、
最终公开回答和反馈按下方“问答质量数据集”规则保存到独立的 `storage/agent_quality.db`；两个数据库职责
不同。`GET /health/ready` 中的 `feishu_bot` 会动态显示 `available`、`unavailable` 或 `disabled`；飞书
断线不会影响 Web 聊天、RAG、MCP 和 Bug Graph。

### LangGraph Bug 诊断

Bug、报错、异常和 trace 请求由 Manager 直接调用 `bug_diagnosis_expert`。该工具使用固定 LangGraph 流程，
不会让模型自行决定 Grafana 查询范围、Loki datasource、namespace 或代码分支。启用配置如下：

```dotenv
BUG_GRAPH_ENABLED=true
BUG_GRAPH_DB=storage/bug_graph.db
BUG_GRAPH_INTERRUPT_TTL_SECONDS=86400
BUG_GRAPH_LOG_RETRY_COUNT=2
BUG_GRAPH_LOG_RANGE_MINUTES=1440
BUG_GRAPH_CODE_TOP_K=5
BUG_GRAPH_MIN_RERANK_SCORE=0.35
CITATION_DETAIL_MAX_CHARS=6000
GRAFANA_LOG_MAX_RANGE_MINUTES=1440
```

诊断必须同时提供环境和原始 trace ID。缺少字段时会按 `conversation_id` 暂停，24 小时内补充可继续；发送
`取消诊断` 会清除当前暂停。环境映射固定为：开发和测试日志检索 `develop` 代码，生产日志检索 `master`
代码。最近 24 小时没有日志时流程立即结束，不会继续检索代码。

证据等级为 `none`、`log_only`、`correlated`、`contract_supported`。只有日志与代码相关，或进一步获得
Swagger/产品文档支持时，报告才允许提出可能的代码根因。Grafana 或 Bug Graph 初始化失败会在
`/health/ready` 中显示 `unavailable`，其他 RAG 和指标 MCP 能力仍降级可用。

聊天中的代码、产品文档、知识片段和 Swagger 引用可通过
`GET /api/v1/citations/detail` 按需加载，正文最多返回 6000 字符。日志引用和 MCP 引用不会调用该接口，
也不会展示原始日志或完整工具输出。Graph checkpoint 只保存结构化字段和引用 ID，不保存日志正文、
代码正文、凭证、Prompt 或模型完整响应。

首版客户端查询接口没有鉴权，管理接口使用 HttpOnly Session Cookie + CSRF。只允许绑定回环地址，
禁止直接暴露到共享网络；接入公司共享网络前必须增加网关身份认证或 JWT：

```powershell
uvicorn knowledge.api.app:app --host 127.0.0.1 --port 8000 --workers 1
```

应用：`http://127.0.0.1:8000/chat`  
管理端：`http://127.0.0.1:8000/admin`  
接口文档：`http://127.0.0.1:8000/docs`

必须保持 `--workers 1`。当前架构把同步 Worker、Git mirror 操作和 BM25 缓存放在同一进程中；启动多个
Uvicorn worker 会重复扫描来源、并发修改 mirror，并创建互相不一致的内存索引。

前端开发模式需要两个终端：

```powershell
# Terminal 1
uvicorn knowledge.api.app:app --host 127.0.0.1 --port 8000 --workers 1

# Terminal 2
Set-Location web
npm run dev
```

Vite 会把 `/api` 代理到 `127.0.0.1:8000`。生产方式先执行 `npm run build`，FastAPI 再从 `web/dist`
同源提供页面，不需要单独运行 Vite。

主要接口：

- `POST /api/v1/agent/chat`
- `POST /api/v1/agent/chat/stream`
- `POST /api/v1/agent/runs/{run_id}/decisions`
- `POST /api/v1/agent/runs/{run_id}/decisions/stream`
- `DELETE /api/v1/agent/conversations/{conversation_id}`
- `GET /health/live`
- `GET /health/ready`

JSON 对话示例：

```json
{
  "conversation_id": "可选；为空时生成 UUID",
  "message": "销售额指标的口径是什么？"
}
```

SSE 事件固定为 `run.started`、`agent.updated`、`text.delta`、`tool.started`、
`tool.completed`、`approval.required`、`run.completed` 和 `run.error`。

## 状态与隐私

对话保存在 `storage/agent_sessions.db`。完整历史持续保留，每轮默认只加载最近 50 个 item；同一
conversation 的请求通过异步锁串行执行。待审批 RunState 单独保存在 `agent_pending_runs` 表。

日志和 API tool audit 只记录名称、状态、耗时、usage 和脱敏参数。禁止记录 API Key、Authorization
Header、Bearer Token、chunk 正文、Embedding、Prompt、模型完整响应或 MCP 完整输出。Tracing 默认
设置 `trace_include_sensitive_data=false`，并以 `conversation_id` 作为 group ID。

本地数据库和目录仍包含公司内部知识，至少应纳入服务器磁盘权限、备份和恢复策略。上传来源删除采用
先停用检索、再由后台任务清理的方式；任务失败时应在管理页重试，不能直接手工删除 SQLite 行或
Chroma 目录。

## 问答质量数据集

系统默认启用独立质量库，自动收集网页、API 和飞书中的成功、失败、超时、待补充、无答案与中断请求：

```dotenv
AGENT_QUALITY_ENABLED=true
AGENT_QUALITY_DB=storage/agent_quality.db
AGENT_QUALITY_RUNNING_TIMEOUT_SECONDS=600
AGENT_QUALITY_PAGE_SIZE=20
AGENT_APPLICATION_VERSION=0.1.0
AGENT_PROMPT_VERSION=v1
```

按照当前内部使用决策，用户原始问题和最终公开回答永久保留，不做内容脱敏，并保存飞书用户 open ID 和
可获取的真实名称。管理员应通过 `/admin` 的“问答质量”页查看、筛选、删除和导出数据；客户端不能读取
质量记录。用户主动粘贴到问题中的密码或 Token 会作为原始问题的一部分保存，因此管理员必须及时删除
误发记录，并将 `storage/agent_quality.db` 纳入服务器磁盘访问控制和备份策略。

系统持有的 API Key、Authorization Header、服务端 Token、Prompt、完整工具输入输出、原始日志、代码
正文、知识 chunk 正文和 Embedding 不进入质量库。工具与引用只保存名称、状态、耗时、来源 ID 和受控
metadata。

网页回答下方支持点赞和点踩；飞书机器人回答支持表态反馈。管理员可以把典型问答手动加入回归评测集，
配置必须调用的工具、引用类型、必要事实和禁止内容，再使用当前最新知识库与工具重跑。评测会话相互隔离，
结果只进入 `eval_runs/eval_results`，不会再次写成真实问答样本。

问答可靠性默认启用以下确定性门禁：高置信度领域路由会把单领域问题交给受限 Manager；指标数据和
SQL 查询必须先由用户明确确认指标应用；同一轮最多执行 6 次知识检索且相同查询只执行一次；公开引用
按文档章节或代码符号去重并限制为 10 条。模型仅检索到 DTO 字段、普通文档或没有结果时，不允许把
“未找到实现”写成“系统没有/不支持”。这些行为可通过下列环境变量灰度控制：

```dotenv
AGENT_INTENT_ROUTER_ENABLED=true
AGENT_INTENT_ROUTER_MIN_CONFIDENCE=0.75
AGENT_RETRIEVAL_MAX_CALLS=6
AGENT_RETRIEVAL_MAX_IDENTICAL_QUERIES=1
AGENT_PUBLIC_CITATION_LIMIT=10
METRIC_QUERY_GUARD_ENABLED=true
```

仓库内的 100 条审批流、工作流、指标平台、跨领域和范围护栏用例可以幂等登记到正式评测库。先执行
校验，再正式写入；稳定 case ID 使重复导入只更新同一条记录：

```powershell
python -m knowledge.quality.import_cases --dry-run
python -m knowledge.quality.import_cases
```

每条正式用例除工具、引用类型和回答行为外，还会校验最大延迟、工具调用数与公开引用数量。

主要接口：

- `POST /api/v1/quality/turns/{turn_id}/feedback`
- `GET /api/v1/admin/quality/turns`
- `GET /api/v1/admin/quality/turns/{turn_id}`
- `GET /api/v1/admin/quality/export?format=jsonl|csv`
- `POST /api/v1/admin/quality/turns/{turn_id}/eval-case`
- `GET /api/v1/admin/quality/eval-cases`
- `POST /api/v1/admin/quality/eval-runs`
- `GET /api/v1/admin/quality/eval-runs/{run_id}`

## 用户历史会话与长期记忆

- `/history` 展示当前匿名设备或飞书身份拥有的网页会话，可搜索、打开、重命名、删除并继续对话。
- `/memory` 展示可跨会话召回的长期记忆。它不是聊天记录，也不会保存普通问答全文。
- 用户级记忆候选会显示在“待你确认”，飞书用户确认后才进入长期记忆；领域级候选仍由管理员审核。
- 飞书登录后的会话和记忆按应用 `open_id` 隔离。登录前的匿名数据只有在用户明确确认合并后才迁移。
- 历史接口只返回用户和助手可见消息，不返回工具参数、工具输出、模型上下文或凭证。

长期记忆按用途分层：

- 会话摘要：由异步 Memory Worker 生成，只用于当前会话连续性，不作为企业事实证据。
- 用户长期记忆：偏好、上下文、事件和决策，必须经过用户或管理员确认。
- Bug 事件记忆：只有 `correlated` 或 `contract_supported` 诊断才生成待确认候选；不保存 trace ID、原始日志、代码正文或完整模型回答。
- 实体关系记忆：保存服务、接口及证据引用之间的关系，并按用户、领域、环境和分支隔离。
- 排障程序记忆：保存经过证据支持且由用户确认的步骤模板，不保存模型思考过程或原始工具轨迹。
- 产品文档、代码和 Swagger 继续以 Chroma/知识目录为唯一事实来源，不复制到记忆数据库。

相关配置：

- `MEMORY_SUMMARY_MAX_CHARS`
- `MEMORY_INCIDENT_CANDIDATE_TTL_SECONDS`
- `MEMORY_ENTITY_RECALL_LIMIT`
- `MEMORY_PROCEDURAL_ENABLED`

主要接口：

- `GET /api/v1/agent/conversations`
- `GET /api/v1/agent/conversations/{conversation_id}`
- `PATCH /api/v1/agent/conversations/{conversation_id}`
- `DELETE /api/v1/agent/conversations/{conversation_id}`
- `GET /api/v1/memory/candidates`
- `POST /api/v1/memory/candidates/{candidate_id}/confirm`

## 旧向量迁移

升级已有 Chroma 数据时先 dry-run，再执行带自动备份的迁移。迁移只补 metadata，不重新计算
Embedding：

```powershell
python -m knowledge.cli migrate-legacy-catalog
python -m knowledge.cli migrate-legacy-catalog --apply
```

执行 `--apply` 前停止服务，确认 `storage/chroma` 和 `storage/agent_sessions.db` 可读且磁盘空间足够。
命令会先备份到 `storage/backups/<timestamp>/`。迁移后检查 `python -m knowledge.cli stats` 的向量数量，
再分别执行旧 CLI 检索和 `/chat` 范围检索做对照。不要在未备份的真实数据上反复试跑。

## 原有 RAG 命令

```powershell
python -m knowledge.cli ingest --app-id middle-platform --domain "指标平台" --name "指标平台"
python -m knowledge.cli multi-search "SDK 怎么查询指标应用数据？" --app-id middle-platform --domain "指标平台"
python -m knowledge.cli stats
```

BM25 在初始化或 refresh 时加载轻量 metadata 并构建内存索引；查询先在内存计算 Top K，再从 Chroma
读取候选正文。向量路线直接查询 Chroma，两路结果按 chunk ID 合并后执行 Rerank，失败时退回 RRF。

## 测试

```powershell
python -m pytest
Set-Location web
npm test
npm run test:parser
npm run build
npm run test:e2e
```

默认测试不会连接飞书。需要显式 live smoke 时，使用已轮换的测试凭证并设置
`RUN_FEISHU_LIVE_SMOKE=1`、`FEISHU_TEST_MESSAGE_ID` 后单独运行：

```powershell
python -m pytest tests/test_feishu_live.py -m live -q
```

默认测试不会调用真实模型或 MCP。显式设置 `RUN_AGENT_LIVE_SMOKE=1` 并提供测试凭证后，才运行 live
smoke。Agent eval 样例位于 `tests/evals/agent_eval_cases.json`。

OpenAI Agents SDK 设计参考：[Agents](https://openai.github.io/openai-agents-python/agents/)、
[MCP](https://openai.github.io/openai-agents-python/mcp/)、
[Sessions](https://openai.github.io/openai-agents-python/sessions/)、
[Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/) 和
[Tracing](https://openai.github.io/openai-agents-python/tracing/)。
