# BM25 + pgvector 查询性能优化设计

## 背景与基线

当前知识库约 40,385 条。审批流代表问题总耗时约 47.1 秒，其中 `collect_domain_evidence` 约 16.1 秒、BM25 `keyword_search` 约 10.9 秒、pgvector `vector_search` 约 2 秒、DeepSeek 模型调用合计约 7.5 秒。

BM25 管线构建会读取 `chunk_id`、标题、关键词和 metadata，并在 Python 内存中建立标题与关键词两个 `BM25Plus` 索引。当前热查询带领域、来源类型或分支条件时，仍通过 `get_chunk_ids()` 从 PostgreSQL 拉取大量 ID；Git 同步完成后又直接清空已有管线，导致下一次用户查询承担冷启动成本。

## 目标

- BM25 热查询不再调用仓储批量读取 eligible chunk ID。
- Git 或文档同步期间继续使用旧索引，刷新失败不影响现有查询。
- 新索引构建完成后一次性替换旧索引，不暴露半成品。
- 启动时优先预热受控证据工具使用的全局管线，并公开预热状态。
- Query Rewrite 完成后，BM25 与 pgvector 两路并行召回。
- 不改变公开 Chat/SSE、引用、RRF/Rerank、知识同步和存储接口。

## 方案选择

### 采用：版本化内存快照 + 原子刷新

`KeywordRetrievalService` 已持有完整轻量 metadata，因此增加与 Chroma/PostgreSQL where 语义一致的内存匹配器。查询先在内存记录中筛选 eligible indexes，再进行 BM25 打分，仅对 Top-K 调用 `get_chunks()` 读取正文。

`RetrievalPipelineRegistry.refresh()` 在锁外构建替换管线，构建期间旧管线继续服务；全部目标管线构建成功后，在锁内一次性替换。构建失败时保留旧管线并记录脱敏异常类型。

### 未采用：每次查询 PostgreSQL ID

实现简单，但会在 Telepresence 和远程数据库下反复传输成千上万个 ID，已确认是热查询主要耗时来源。

### 暂不采用：PostgreSQL全文检索完全替代 BM25

长期可减少 Python 内存，但会改变中文分词、排序和回归基线，改造范围明显更大。本期保留现有 Jieba + BM25Plus 语义。

## 组件设计

### 1. 内存 metadata 过滤

新增纯函数 metadata matcher，支持当前检索实际使用的：

- 简单相等条件；
- `$and`、`$or`；
- `$eq`、`$ne`、`$in`、`$nin`；
- `enabled`、`app_id`、`domain/domain_id`、`source_type`、`branch`、`symbol_name` 等字段。

`KeywordRetrievalService.search()` 使用 `self._records` 中的 metadata 计算 eligible indexes，不再调用 `repository.get_chunk_ids()`。Top-K 确定后仍通过 `get_chunks(ids=[...])` 获取少量正文，保证正文不是长期重复保存在 BM25 结构中。

如果出现 matcher 不支持的操作符，必须明确抛出 `ValueError`，不得静默扩大数据范围。

### 2. 旧索引持续服务与原子刷新

`RetrievalPipelineRegistry` 增加：

- `refresh(app_id=None, domain=None)`：复制当前目标 key，在锁外构建新管线，成功后原子替换；
- `warm_status()`：返回 `disabled/warming/available/unavailable` 和已缓存管线数量，不返回异常正文；
- 同一 key 的并发首次构建继续由锁保证只创建一次。

`SourceIndexCoordinator` 在同步成功后调用后台 Worker 内的 `refresh()`，替代 `invalidate()`。同步 Worker 可以等待刷新完成，但用户查询始终读取旧对象。刷新失败不回滚已经成功的知识入库，只保留旧 BM25 并记录异常类型。

删除知识源等必须立即阻止旧内容继续召回的操作仍使用强制 `invalidate()`；普通版本更新使用 `refresh()`。

### 3. 启动预热与 readiness

启动时首先预热 `("middle-platform", None)`，因为受控证据工具统一使用全局管线和 metadata 条件。领域专用旧工具的管线按需构建，避免启动时重复加载四份约 4 万条 metadata。

readiness 增加 `bm25` 组件：

- `warming`：关键全局管线尚未完成；
- `available`：关键管线已可用；
- `unavailable`：预热失败；
- `disabled`：配置关闭。

服务 live 不受预热影响；ready 在关键全局管线 available 前返回非 ready，防止 Kubernetes 提前导流。

### 4. BM25 与 pgvector 并行召回

Query Rewrite 保持先执行。随后使用两个受控线程任务并行执行 keyword 和 vector route，分别记录原有阶段耗时。任何一路失败时保留另一路结果：

- pgvector 失败：继续使用 BM25；
- BM25 失败：继续使用 pgvector；
- 两路都失败：返回空证据并由现有证据门禁处理。

RRF/Rerank 在两路完成后执行，候选规模和公开行为不变。

## 并发与一致性

- 查询获得管线对象引用后，该对象在本轮查询内保持有效。
- 刷新只在 registry 字典层原子替换，不原地修改旧 BM25 对象。
- 旧对象在没有查询引用后由 Python 自动回收。
- PostgreSQL/pgvector 仍是事实来源；BM25 是带版本生命周期的只读派生索引。
- 首期单 Uvicorn Worker，每个进程维护自己的 BM25；未来多副本由各 Pod 独立预热。

## 可观测性

保留现有 `retrieval.query_rewrite`、`retrieval.keyword_search`、`retrieval.vector_search`、`retrieval.rerank` span，并新增：

- `bm25.warm`；
- `bm25.refresh`；
- metadata 记录数、构建耗时和状态；

日志和质量数据不记录问题正文、metadata 正文、chunk ID、Embedding 或数据库连接信息。

## 测试与验收

- matcher 覆盖简单条件、嵌套 `$and/$or`、集合操作符、缺失字段和非法操作符。
- 仓储 spy 证明 BM25 热查询不调用 `get_chunk_ids()`，只调用一次 Top-K `get_chunks()`。
- 刷新期间并发查询仍返回旧管线；刷新完成后新查询使用新管线；刷新失败保留旧管线。
- 同步成功走 refresh，删除/禁用走 invalidate。
- readiness 覆盖 warming、available、unavailable、disabled。
- 并行召回测试证明总耗时接近较慢一路而不是两路之和，并覆盖单路失败降级。
- 全量 `pytest -q` 通过。
- 真实审批流问题热查询目标：`keyword_search <= 1.5s`、证据工具 `<= 6s`、总耗时 `<= 25s`；模型供应商异常单独记录，不计入存储验收。

## 发布与回退

改造通过独立开关控制：

- `BM25_MEMORY_FILTER_ENABLED=true`
- `BM25_STALE_WHILE_REFRESH_ENABLED=true`
- `RETRIEVAL_PARALLEL_ROUTES_ENABLED=true`

任一功能可独立关闭回退到现有实现。先在本地使用 pgvector 验证，再部署 dev 直连 PostgreSQL 重放 Critical 审批流与指标平台问题。
