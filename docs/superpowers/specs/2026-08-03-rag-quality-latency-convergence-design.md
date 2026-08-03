# RAG Quality And Latency Convergence Design

## Goal

在不修改飞书链路、不更换 Embedding 模型、不重新向量化现有正文的前提下，修复 PostgreSQL pgvector 产品文档领域过滤错误，收敛单领域工具调用和公开引用，并把网页问答 P90 降到 30 秒以内。完成后使用已批准的 Critical 5/10/30 批次验证准确性，30 条必须全部通过后才能扩大 dev 用户范围。

## Scope

本次包含：

- pgvector 产品文档领域列规范化与现有 268 条数据回填；
- 单领域底层检索的全局预算与标准化重复查询抑制；
- 公开引用的相关性门禁、去重和最多 5 条限制；
- 网页渠道端到端延迟优化与分阶段 Critical 回归；
- 与上述行为直接相关的配置、测试、迁移工具和运维说明。

本次不包含：

- 飞书响应耗时、格式或线程模型优化；
- 更换模型、Embedding 或 chunk 切分规则；
- 重新生成现有 Embedding；
- 为通过评测而降低事实、证据、工具或延迟门禁；
- 多 Pod、多 Worker 或分布式 Worker 改造。

## Chosen Approach

采用分层证据预算方案。数据层修复稳定领域标识；检索层控制实际外部调用和并发；证据层只公开强相关结果；评测层以不可放宽的 Critical 5/10/30 和网页 P90 验证结果。

单纯修改 `top_k` 和配置无法修复产品文档过滤错误，也无法阻止不同工具入口重复执行同一底层查询。重写 Agent Graph 虽然控制力更强，但会扩大本次 dev 上线风险，因此不采用。

## Pgvector Domain Semantics

`vector_entries.domain` 是用于过滤和索引的稳定领域 ID，必须优先取 metadata 的 `domain_id`。metadata 中的 `domain` 保留中文展示名称，不参与 `domain_id` 过滤。

新写入规则：

```text
vector_entries.domain = metadata.domain_id or metadata.domain
```

过滤规则继续把 `domain_id` 映射到 `vector_entries.domain`，从而兼容现有查询接口。代码知识的领域列当前已经等于领域 ID；产品文档 268 条需要一次性回填。

回填只更新独立 `domain` 列：

```sql
UPDATE vector_entries
SET domain = metadata ->> 'domain_id'
WHERE collection_name = :knowledge_collection
  AND source_type = 'product_document'
  AND metadata ->> 'domain_id' IS NOT NULL
  AND domain IS DISTINCT FROM metadata ->> 'domain_id';
```

回填命令必须幂等，执行前后只输出总数、待修复数、已修复数和按领域统计，不输出正文、Embedding、chunk ID 或完整 source ID。回填后必须验证 268 条全部一致，并验证 `approval-flow`、`workflow`、`metric-platform` 和 `shared` 均能通过 metadata filter 召回。

## Retrieval Budget And Deduplication

单领域一次用户请求最多允许 4 次实际底层检索。预算按 Agent run 共享，而不是每个工具各自计数。

标准化查询键由以下字段组成：

```text
normalized_query + app_id + domain_id + source_type + branch + task_type
```

查询文本执行 Unicode 兼容归一化、大小写折叠、空白和常见标点压缩。相同键再次出现时复用本轮已有证据，并记录一次 `duplicate_query` 审计事件，不调用 BM25、pgvector、Rerank、Swagger 或 MCP。

`collect_domain_evidence` 仍根据任务类型选择来源：

- `how_to`：产品文档优先；
- `api_contract`：代码与 Swagger，产品文档补充；
- `code_lookup`：代码；
- `requirement_analysis`：产品文档与代码；
- `metric_query`：产品文档；实时数据才进入受控 MCP 状态机。

允许并行的代码和文档检索并行执行，但共享预算预留必须是原子的。预算耗尽时返回已有证据摘要和 `budget_exhausted`，不得让模型通过其他工具名绕过限制。

## Citation Selection

公开引用与内部证据分离。内部可以保留更多候选用于生成答案，公开响应只选择最强证据。

排序优先级：

1. 精确符号、精确接口或精确文档章节命中；
2. 可用的 Rerank 分数；
3. RRF 融合分数；
4. 来源类型和标题多样性；
5. 原始检索顺序。

公开引用最多 5 条。默认目标为 3 至 5 条，但只有 1 至 2 条达到门禁时只展示实际强证据，不补弱引用；没有合格证据时保持零引用并触发现有证据不足说明。

候选需要满足至少一个条件：

- 精确符号、接口或章节命中；
- Rerank 成功且达到配置阈值；
- Rerank 降级时达到更严格的 RRF 阈值。

同一 source、同一符号或同一文档章节的重复结果只保留得分最高的一条。公开层继续隐藏内部 chunk ID，使用中文可读标题和产品文档 URL。

## Latency Design

网页 P90 以 `quality_turns.channel = 'web'`、`status = 'completed'` 的端到端 `duration_ms` 计算。验收窗口必须包含修复后至少 30 条代表性网页请求；旧版本历史数据不与修复后窗口混算。

目标：

- 单领域网页 P50 不超过 15 秒；
- 单领域网页 P90 不超过 30 秒；
- 单领域实际工具调用不超过 4 次；
- 相同标准化查询实际执行次数为 1。

主要优化措施：

- 对允许并行的检索来源并行执行；
- 同一标准化查询只执行一次 Query Rewrite、BM25、pgvector 和 Rerank；
- 产品文档过滤修复后避免空召回导致的重复补查；
- 对已经拥有足够强证据的单领域任务提前停止检索；
- 保持质量记录异步，不阻塞最终 SSE 完成事件；
- 按 route、rewrite、BM25、pgvector、Rerank、tool、LLM 和 total 记录脱敏耗时，不记录 Prompt、正文或 Embedding。

飞书渠道不参与本期性能修改和验收。

## Critical Regression Strategy

使用已批准、事实约束完整的 `critical-v2` 用例，依次运行固定的 5、10、30 批次。每一批必须全部通过后才能进入下一批。

失败按以下类型处理：

- `route`：领域或任务类型错误；
- `evidence_support`：召回证据不足或不支持结论；
- `semantic_score`：回答没有覆盖必需事实或出现关键矛盾；
- `tool_count`：实际工具调用超过预算；
- `latency`：超过用例或全局延迟门禁；
- `judge_error`：裁判失败，保持失败关闭。

只有用当前代码、文档或接口证据证明评测事实本身错误时，才能修改用例，并保留版本记录。禁止删除失败用例、放宽分数、清空 required facts 或添加无关引用来追求 30/30。

## Failure Handling

- 数据回填在事务中执行；校验失败则回滚。
- Rerank 不可用时使用严格 RRF 门禁，不把低相关最近邻当作强证据。
- 单个检索来源超时不取消已经完成的其他来源，但不得突破 4 次预算重试。
- Critical 任一批失败即停止扩大批次，先修复对应分类。
- 网页性能样本不足 30 条时报告样本不足，不宣称 P90 达标。

## Verification

自动测试覆盖：

- metadata 同时含 `domain` 和 `domain_id` 时独立列优先保存 `domain_id`；
- 回填 SQL 幂等且不修改正文、Embedding、metadata 和 ID；
- 四个产品文档领域过滤均能召回；
- 单领域最多 4 次实际检索；
- 标准化重复查询不重复执行；
- 并发检索不能竞争绕过预算；
- 引用最多 5 条、弱相关被过滤、证据不足时允许少于 3 条；
- Rerank 降级使用更严格门禁；
- 质量耗时统计只使用修复后的网页完成请求。

发布验证顺序：

1. 运行后端全量测试；
2. 运行前端测试和生产构建；
3. 执行 pgvector 回填 dry-run；
4. 执行正式回填并校验 268 条；
5. 重启单 Worker，确认 readiness；
6. 运行 Critical 5/5；
7. 运行 Critical 10/10；
8. 运行 Critical 30/30；
9. 运行至少 30 条网页代表请求并计算修复后 P50/P90；
10. 只有全部门禁通过后才扩大 dev 用户范围。

