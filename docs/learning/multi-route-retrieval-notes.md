# RAG 项目学习笔记：多路召回阶段

更新时间：2026-07-03

当前学习位置：已经学完“向量入库”，正在学习“多路召回”。本笔记用于记录目前对项目结构、召回链路、BM25、向量检索、RRF 与后续学习路线的理解。

---

## 1. 当前项目整体认知

这个项目是一个企业内部知识库 RAG 项目，核心目标是：

```text
Markdown 知识文档
  -> 切块
  -> embedding 向量化
  -> 存入 Chroma
  -> 用户查询时做多路召回
  -> 合并、去重、rerank
  -> 返回最终候选知识片段
```

目前项目还没有实现最终大模型回答生成，重点在知识入库和检索链路。

核心包目录：

```text
knowledge/
  cli.py
  config/
  loaders/
  repositories/
  retrieval/
  schemas/
  services/
```

目前重点阅读的文件：

```text
knowledge/cli.py
knowledge/services/multi_route_retrieval_service.py
knowledge/services/keyword_retrieval_service.py
knowledge/retrieval/tokenizer.py
knowledge/repositories/vector_store_repository.py
knowledge/services/hybrid_rerank_service.py
knowledge/services/query_rewrite_service.py
knowledge/services/qwen_rerank_service.py
knowledge/schemas/documents.py
```

---

## 2. 向量入库与多路召回的衔接

向量入库阶段已经完成。入库时，每个知识 chunk 会被写入 Chroma，主要包括：

```text
content     chunk 正文，用于 embedding 和展示
metadata    检索过滤、BM25、展示所需字段
id          chunk_id
```

metadata 中比较关键的字段：

```text
chunk_id
parent_chunk_id
heading
bm25_keywords
app_id
domain
name
chunk_type
module
interface_type
section_path
source_path
```

这些字段在多路召回阶段会被继续使用：

```text
heading / bm25_keywords
  -> 给 BM25 关键词召回使用

app_id / domain / name / chunk_type
  -> 给 metadata filter 做数据隔离和范围控制

content
  -> 给向量召回、rerank 和最终展示使用
```

所以向量入库和多路召回不是割裂的。入库时写好的 metadata，决定了后面 BM25 能不能精确召回、过滤条件能不能正确生效。

---

## 3. 多路召回主流程

入口命令：

```powershell
python -m knowledge.cli multi-search "SDK 怎么查询指标应用数据？" `
  --app-id middle-platform `
  --domain "指标平台" `
  --keyword-k 20 `
  --vector-k 20 `
  --final-k 5
```

命令入口在：

```text
knowledge/cli.py -> multi_search()
```

这里会创建几个核心对象：

```text
settings
  读取 .env 和默认配置

repository
  Chroma 数据访问层，负责向量搜索、读取 chunk、读取 metadata

keyword_service
  BM25 关键词召回服务

query_rewriter
  查询改写器，可选；没有 DeepSeek Key 时可以为空

reranker
  qwen3-rerank 适配器，可选；没有 rerank Key 时可以为空

MultiRouteRetrievalService
  多路召回总编排器
```

多路召回主链路：

```text
用户 query
  -> query rewrite
  -> BM25 keyword route
  -> Chroma vector route
  -> RouteSearchResult 统一包装
  -> HybridRerankService 合并去重
  -> qwen3-rerank 精排
  -> rerank 不可用时用 RRF 兜底
  -> MultiRouteSearchResult
```

关键类：

```text
knowledge/services/multi_route_retrieval_service.py
  MultiRouteRetrievalService.search()
```

---

## 4. Query Rewrite：查询改写

查询改写的作用不是回答问题，而是把用户口语问题变成更适合检索的结构化信息。

输入示例：

```text
这个咋查？
```

可能输出：

```text
retrieval_query:
  指标应用如何通过 MetricClient.getDataV2 查询数据？

keywords:
  SDK
  MetricClient
  getDataV2
  /api/datacenter/v2/getData
```

项目中的作用：

```text
retrieval_query
  -> 给向量召回使用
  -> 给 rerank 使用

keywords
  -> 给 BM25 精确匹配使用

retrieval_needed
  -> 判断是否需要查知识库
```

如果用户只是说：

```text
你好
谢谢
```

LLM 可以返回：

```text
retrieval_needed = false
```

这样系统会直接短路，不再调用 BM25、向量检索和 rerank。

如果 query rewrite 不可用，项目会降级：

```text
retrieval_query = 原始 query
keywords = 空
继续检索
```

这是一个重要的韧性设计：外部模型不可用时，检索主流程仍然能跑。

---

## 5. BM25 关键词召回整体理解

核心文件：

```text
knowledge/services/keyword_retrieval_service.py
```

BM25 路线负责精确召回，尤其适合：

```text
接口名
方法名
类名
字段名
API 路径
英文缩写
业务关键词
```

例如：

```text
MetricClient.getDataV2
/api/datacenter/v2/getData
summaryRowFlag
metricType=APPLICATION
SDK
Knife4j
```

### 5.1 BM25 索引什么时候建？

在 `KeywordRetrievalService` 创建时，会自动调用：

```python
self.refresh()
```

然后从 Chroma 读取轻量 metadata：

```text
chunk_id
heading
bm25_keywords
metadata
```

注意，这里不会读取完整正文 content。

读取后分别建立两个 BM25 内存索引：

```text
_title_index
  基于 heading 字段

_keywords_index
  基于 bm25_keywords 字段
```

所以 BM25 这条路可以理解为：

```text
服务初始化时构建轻量内存索引
用户搜索时直接在内存索引里打分
选出 Top K 后再去 Chroma 按 chunk_id 取完整正文
```

当前项目是 CLI 形式，所以每次执行 `multi-search` 都会重新创建一次 `KeywordRetrievalService`，也会重新构建一次 BM25 内存索引。

如果以后改成 FastAPI 常驻服务，则通常会在应用启动时构建一次 BM25 索引，然后请求之间复用。

### 5.2 为什么叫字段化 BM25？

因为它不是把正文 content 整体拿来做 BM25，而是分字段：

```text
heading
bm25_keywords
```

分别打分，再按权重合成：

```text
最终 BM25 分数
  = heading 归一化分 * 0.65
  + bm25_keywords 归一化分 * 0.35
```

这样设计的原因：

```text
heading
  更代表 chunk 主题，命中价值高

bm25_keywords
  适合放接口名、字段名、别名、API 路径等精确检索入口

content
  正文较长，噪声更大，不适合作为当前 BM25 主索引字段
```

### 5.3 用户搜索时 BM25 怎么工作？

`KeywordRetrievalService.search()` 大致流程：

```text
1. 收集查询文本
   原始 query
   + retrieval_query
   + LLM 提取的 keywords

2. 自定义 tokenizer 分词

3. 对 query tokens 做保序去重

4. 根据 metadata filter 得到 eligible_ids

5. heading BM25 打分

6. bm25_keywords BM25 打分

7. 在 eligible 候选集合内部做归一化

8. 按字段权重合成最终 BM25 route 分数

9. 排序取 Top K

10. 根据 chunk_id 去 Chroma 取完整正文

11. 包装成 RouteSearchResult 返回
```

---

## 6. 自定义分词器理解

核心文件：

```text
knowledge/retrieval/tokenizer.py
```

这个项目没有直接把所有文本交给 jieba，而是做了自定义 tokenizer：

```text
原始文本
  -> 正则先提取技术 token
  -> 技术 token 从原文里替换为空格
  -> 剩余中文文本交给 jieba
  -> 技术 token + 中文 token 合并返回
```

这样做是为了保护技术词。

例如希望保留：

```text
MetricClient.getDataV2
/api/datacenter/v2/getData
summaryRowFlag
metricType=APPLICATION
SDK
```

而不是被中文分词器切坏。

BM25 是词匹配模型，如果关键技术词被切碎或丢失，精确召回效果会明显下降。

所以这里的技术分工是：

```text
正则
  负责保护技术标识符、URL、API 路径、英文方法名

jieba
  负责中文搜索分词
```

---

## 7. 向量召回理解

核心文件：

```text
knowledge/repositories/vector_store_repository.py
```

向量召回使用 Chroma：

```python
similarity_search_with_score(query, k=k, filter=where)
```

工作原理：

```text
用户 query
  -> embedding 模型转成高维向量
  -> Chroma 中每个 chunk 已经有 embedding
  -> 计算 query 向量和 chunk 向量的距离/相似度
  -> 返回 Top K
```

在这个项目里，向量路只使用：

```text
rewrite.retrieval_query
```

不用原始 query，也不用 keywords。

原因：

```text
向量模型更适合完整、自然、语义清晰的问题
BM25 更适合关键词、接口名、字段名
```

所以两条路的输入不同：

```text
BM25 路：
  原始 query + retrieval_query + keywords

向量路：
  retrieval_query
```

---

## 8. RouteSearchResult：统一召回结果结构

核心文件：

```text
knowledge/schemas/documents.py
```

BM25 路和向量路原始结果结构不同，但后续融合阶段需要统一处理。

所以两路结果都会被包装成：

```text
RouteSearchResult
```

关键字段：

```text
retrieval_route
  keyword 或 vector

rank
  当前路线内部排名

chunk_id
  用于后续合并去重

raw_score
  当前路线原始分

score_type
  fielded_bm25 或 chroma_distance

higher_is_better
  BM25 为 True
  Chroma distance 为 False
```

这个设计避免了直接混用不同量纲的分数。

BM25：

```text
分数越大越好
```

Chroma distance：

```text
距离越小越好
```

因此项目没有做：

```text
BM25 分数 + 向量分数
```

而是先统一结构，再交给融合器处理。

---

## 9. RRF 融合理解

RRF 全称：

```text
Reciprocal Rank Fusion
```

可以理解为：

```text
倒数排名融合
```

它不直接比较不同路线的原始分数，只看每一路内部排名。

公式：

```text
score = 1 / (k + rank)
```

项目里默认：

```text
k = 60
```

例如某个 chunk：

```text
BM25 排第 1
向量排第 2
```

RRF 分数：

```text
1 / (60 + 1) + 1 / (60 + 2)
```

如果另一个 chunk 只在向量里排第 1：

```text
1 / (60 + 1)
```

那么两路都命中的 chunk 往往会更占优势。

RRF 的好处：

```text
不要求 BM25 和向量分数同量纲
只依赖每一路内部排名
对多路召回融合很稳健
适合作为 rerank 不可用时的兜底排序
```

核心文件：

```text
knowledge/services/hybrid_rerank_service.py
```

---

## 10. Rerank 精排理解

核心文件：

```text
knowledge/services/hybrid_rerank_service.py
knowledge/services/qwen_rerank_service.py
```

多路召回得到的是候选集合。候选召回的目标是：

```text
尽量不要漏掉可能相关的 chunk
```

rerank 的目标是：

```text
在候选集合里重新判断谁最能回答问题
```

rerank 通常会同时看：

```text
query
candidate document
```

相比单纯向量召回，它更细粒度。

在这个项目里：

```text
如果 qwen3-rerank 可用：
  使用 rerank 结果作为 Final Results

如果 qwen3-rerank 不可用或调用失败：
  使用 RRF 排序兜底
```

这也是一个重要韧性设计：

```text
rerank 是增强能力，不是系统硬依赖
```

---

## 11. 当前已经掌握的关键点

目前已经理解：

```text
1. 项目整体是 Python RAG 项目，不是 Java 项目

2. 向量入库阶段会把 content 和 metadata 写入 Chroma

3. 多路召回入口是 knowledge.cli 的 multi_search()

4. MultiRouteRetrievalService 是召回编排层

5. Query Rewrite 用于生成 retrieval_query 和 BM25 keywords

6. BM25 索引在 KeywordRetrievalService 初始化时构建

7. BM25 使用 heading 和 bm25_keywords 两个字段

8. 用户搜索时，BM25 先在内存索引打分，再按 chunk_id 去 Chroma 取正文

9. 自定义 tokenizer 用正则保护技术词，再用 jieba 处理中文

10. 向量召回只使用 retrieval_query

11. BM25 和向量结果统一包装成 RouteSearchResult

12. BM25 分数和向量 distance 不能直接相加

13. RRF 根据排名融合多路召回结果

14. rerank 可用时做最终精排，不可用时 RRF 兜底
```

---

## 12. 下一步学习路线

建议接下来继续按这个顺序阅读：

### 12.1 tokenizer.py

目标：真正看懂技术词是怎么被正则保护的。

重点：

```text
TECHNICAL_TOKEN_RE
DEFAULT_STOPWORDS
JiebaSearchTokenizer.tokenize()
```

### 12.2 vector_store_repository.py

目标：理解 Chroma 如何被封装成项目仓储。

重点：

```text
from_settings()
upsert()
search()
get_chunks()
get_keyword_index_records()
get_chunk_ids()
_normalize_where()
```

### 12.3 hybrid_rerank_service.py

目标：理解按 chunk_id 去重、多路命中记录、RRF 计算、rerank 降级。

重点：

```text
merge()
rank()
_final_result()
```

### 12.4 qwen_rerank_service.py

目标：理解项目如何调用 qwen3-rerank，以及 candidate text 如何拼装。

重点：

```text
rerank()
_candidate_text()
```

### 12.5 query_rewrite_service.py

目标：理解 AppProfile 如何约束 LLM 改写，避免编造内部术语。

重点：

```text
rewrite()
fallback()
_system_prompt()
```

---

## 13. 一句话总括当前阶段

当前阶段可以这样概括：

```text
这个项目的多路召回不是把 BM25 分数和向量分数硬加起来，
而是让 BM25 负责精确词召回，让向量检索负责语义召回，
两路结果统一包装后按 chunk_id 合并，用 RRF 做稳健兜底，
有 rerank 时再交给 qwen3-rerank 做最终精排。
```

