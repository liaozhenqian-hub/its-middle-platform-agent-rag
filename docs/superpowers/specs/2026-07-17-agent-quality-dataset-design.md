# Agent 问答质量数据集设计

## 1. 目标

系统自动收集网页、API 和飞书中的真实问答，包括成功、失败、超时、澄清、无答案和中断状态。管理员能够筛选这些记录、收集用户反馈、将典型问题加入正式评测集，并在 Agent 优化后使用最新知识库和工具批量重跑，比较优化前后的质量。

现有 `storage/agent_sessions.db` 继续只负责 Agent 会话恢复。问答质量数据使用独立的 `storage/agent_quality.db`，避免依赖 OpenAI Agents SDK 的内部消息结构。

## 2. 已确认决策

- 原始用户问题和最终公开回答永久保存，不做内容脱敏。
- 保存真实飞书用户 ID 和名称；未登录的网页用户只保存会话 ID。
- 收集所有结果状态，包括成功、失败、超时、澄清、无答案、取消和连接中断。
- 网页提供点赞、点踩和可选原因；飞书通过机器人回答消息的表态事件收集反馈。
- 所有问答进入质量库，只有管理员手动选择的记录进入正式评测集。
- 回归评测使用最新知识库和最新可用工具重跑，原始回答作为对照基线。
- 质量数据仅允许管理员查看、删除和导出。

## 3. 安全边界

用户已明确选择保存原始问题和回答，因此其中可能包含用户主动输入的个人信息或敏感内容。管理端必须提供单条记录删除能力。

用户主动输入在问题中的密码或 Token 属于原始问题的一部分，将按已确认的 C 方案原样保存。系统不能把它们误认为服务端配置再扩散到其他字段。以下由系统产生、读取或持有的敏感内容仍不得写入质量库：

- 服务端 API Key、Access Token、Authorization Header 和配置凭证。
- 系统 Prompt、开发者 Prompt 和完整模型内部消息。
- 工具完整输入输出、Embedding、原始 Grafana 日志。
- 代码正文、产品文档正文和知识 chunk 正文。

工具和引用只保存确定性的审计字段及公开 metadata。原始问答库不得被用于扩大现有客户端权限。

## 4. 架构

新增 `QualityCaptureService` 作为唯一写入入口。Agent API、SSE 流式接口和飞书桥接层向它提交标准化的运行生命周期事件。它负责幂等写入、状态转换、短事务和有限锁重试。

```text
Web / API / Feishu
        |
        v
AgentService / FeishuBridge
        |
        +----> Agent response returned to user
        |
        +----> QualityCaptureService
                    |
                    v
          storage/agent_quality.db
                    |
          Admin quality and eval APIs
                    |
                    v
             Vue admin console
```

质量采集失败不能阻断 Agent 回答。失败只记录不含正文和凭证的错误日志，并由管理端健康状态显示质量库是否可用。

## 5. 数据模型

### 5.1 quality_turns

每个用户请求对应一条记录：

- `id`、`conversation_id`、`run_id`、`channel_message_id`。
- `channel`: `web`、`api`、`feishu`。
- `user_id`、`user_name`、`chat_id`。
- `question`、`answer`。
- `knowledge_space_id`、`domain_id`。
- `status`: `running`、`completed`、`clarification_required`、`no_answer`、`error`、`timeout`、`cancelled`、`interrupted`。
- `provider`、`model_name`、`last_agent`、`application_version`、`prompt_version`。
- `duration_ms`、`error_type`、`created_at`、`completed_at`。

`run_id` 使用唯一约束；飞书等具备消息 ID 的渠道另建 `channel + channel_message_id` 唯一索引。这样网页没有外部消息 ID 时也能保证幂等。时间、渠道、用户、领域、状态和评价字段建立查询索引。

### 5.2 quality_tool_runs

保存工具名、Agent 名称、状态、耗时和执行顺序。只允许保存经过现有脱敏规则处理的标量参数，不保存工具正文输出。

### 5.3 quality_citations

保存引用类型、来源 ID、领域以及代码路径、branch、commit、symbol、文档版本、Swagger operation 或 trace ID 等公开 metadata。不得复制引用正文。

### 5.4 quality_feedback

保存 `turn_id`、渠道、评价人、`positive/negative`、可选原因、飞书 reaction 标识和时间。相同用户对相同回答修改评价时更新原记录。

### 5.5 eval_cases

管理员从质量记录创建评测用例，保存：

- 原始问题及固定知识范围。
- 必须调用的专家或工具。
- 必须存在的引用类型。
- 必须覆盖的关键事实。
- 禁止出现的内容。
- 用例标签、说明、启用状态和来源 turn ID。

### 5.6 eval_runs 和 eval_results

保存一次批量评测的应用版本、模型、开始/结束时间、每个用例的新回答、工具路线、引用、耗时和规则评分。原回答保持只读，作为优化前基线。

数据库启用 foreign keys、WAL 和显式 schema migration。关联记录删除使用事务和外键级联。

## 6. 采集生命周期

### 6.1 普通 JSON 请求

开始执行 Agent 前先建立 `running` turn。Agent 生成最终 `AgentResponse` 后、返回客户端前更新完整记录。错误、超时和 clarification 通过同一个完成接口写入对应状态。这样进程异常退出后也能在启动恢复阶段识别未完成请求。

### 6.2 SSE 请求

收到 `run.started` 时建立 `running` 记录。公开文本增量只在当前请求内存中聚合，不逐 token 写库。收到 `run.completed` 或 `run.error` 时更新最终回答和状态；客户端断开时写入 `interrupted` 或 `cancelled`。

### 6.3 飞书请求

飞书桥接层传递事件 ID、消息 ID、用户 ID、用户名和群聊 ID。Agent 回复成功后保存机器人回复消息 ID，供后续 reaction 事件准确关联。飞书事件重投不得重复生成 turn 或 feedback。

### 6.4 重启恢复

应用启动时把超过合理运行窗口仍为 `running` 的记录改为 `interrupted`。删除 Agent conversation 不删除质量数据。

## 7. 反馈与管理端

网页回答下方增加点赞和点踩。点踩后允许选择原因或填写短评。聊天响应同时返回随机反馈令牌，数据库只保存其哈希；反馈接口必须同时提交 turn ID 和反馈令牌，避免仅凭可猜测 ID 修改他人评价。飞书网关订阅机器人回答消息的 reaction 事件，并把配置的正向和负向表态映射为统一评价。

管理端新增“问答质量”页面：

- 按时间、渠道、用户、领域、Agent、状态和评价分页筛选。
- 查看原始问题、最终回答、工具路线、引用、耗时和错误类型。
- 删除误发敏感信息、无效记录及其关联数据。
- 将记录手动加入评测集。
- 导出筛选结果或评测集为 JSONL/CSV。

管理接口继续使用现有管理员 Session、CSRF 和同源访问规则。

## 8. 回归评测

管理员选择评测集并发起批量运行。系统使用最新知识库、最新代码索引、最新 Swagger 缓存和当前可用工具执行相同问题。每条评测使用独立的临时 conversation，不能读取原会话上下文，也不能写回真实问答质量数据；评测输出只进入 `eval_results`。评测不做全文字符串相等判断，而是组合以下确定性规则：

- 是否路由到要求的专家和工具。
- 是否取得要求的引用类型。
- 最终状态是否成功。
- 回答是否包含必要关键事实。
- 回答是否包含禁止内容。
- 耗时是否超过设定退化阈值。

可选评审模型分数只能作为辅助指标，不能替代确定性规则。结果页面并排展示原回答和新回答，并突出工具、引用、事实覆盖和耗时变化。

## 9. 公共接口

- `POST /api/v1/quality/turns/{turn_id}/feedback`，请求必须携带该 turn 的反馈令牌。
- `GET /api/v1/admin/quality/turns`
- `GET /api/v1/admin/quality/turns/{turn_id}`
- `DELETE /api/v1/admin/quality/turns/{turn_id}`
- `POST /api/v1/admin/quality/turns/{turn_id}/eval-case`
- 评测集 CRUD、批量运行、运行详情和 JSONL/CSV 导出接口。

客户端聊天响应增加可选 `quality_turn_id` 和 `feedback_token`，用于网页提交反馈，不改变现有回答和 citation 结构。令牌只具备修改该条反馈的能力，不能读取问答记录。

## 10. 错误处理

- SQLite 锁冲突进行有限次数、短间隔重试。
- 采集失败不改变 Agent 的成功或失败语义，也不向用户泄露内部错误。
- 重复回调通过唯一约束转为幂等更新。
- 导出采用分页或流式响应，避免一次加载全部永久数据。
- 管理员删除操作写入现有审计事件，但审计中不复制问题和回答正文。

## 11. 测试

- Repository：迁移幂等、WAL、外键、状态转换、锁重试、幂等写入和级联删除。
- Capture：网页、API、飞书的成功、失败、超时、澄清、无答案和取消。
- SSE：正常完成、客户端断开、服务异常和重复事件。
- Feishu：真实用户信息、机器人回复消息绑定、表态新增和修改。
- Security：服务端凭证、Prompt、工具完整输出、原始日志和 chunk 正文不落质量库；用户主动输入的问题保持原文。
- API/Auth：管理员鉴权、CSRF、分页、筛选、删除和导出。
- Eval：创建用例、使用最新知识重跑、规则评分、失败隔离和前后对比。
- Vue：点赞/点踩、质量列表、详情、评测创建和结果展示。
- Regression：现有聊天、飞书、会话恢复、LangGraph Bug 诊断和知识检索测试保持通过。

## 12. 验收标准

- 质量库可用时，每次真实提问都能在管理端查询到，失败和断开请求不遗漏；质量库持续不可写时 readiness 明确告警，但不阻断聊天。
- 网页和飞书反馈能准确关联到对应回答，重复事件不重复计数。
- 管理员能够删除记录、导出数据并把历史问答加入评测集。
- 优化后能够使用最新知识和工具批量重跑评测集，查看原回答与新回答的可解释差异。
- 质量采集故障不会阻断聊天主链路。
- 系统持有的禁止内容不会写入 `agent_quality.db`；用户主动输入的原始问题按已确认策略保留。
