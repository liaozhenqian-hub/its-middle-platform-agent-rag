import json
import logging
from typing import Any, Protocol

from pydantic import BaseModel, Field

from knowledge.config.app_profiles import AppProfile, AppProfileRegistry
from knowledge.schemas.documents import QueryRewriteResult


logger = logging.getLogger(__name__)


class ChatCompletions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class QueryRewritePayload(BaseModel):
    retrieval_needed: bool = True
    rewritten_query: str
    keywords: list[str] = Field(default_factory=list)
    domain_candidates: list[str] = Field(default_factory=list)
    clarification_needed: bool = False
    task_type: str = "unknown"


class LlmQueryRewriteService:
    """LLM 查询改写服务。

    它不回答用户问题，只把用户原话整理成结构化检索信息：
    - retrieval_query：适合向量检索的完整问题
    - keywords：适合 BM25 精确检索的关键词
    - retrieval_needed：是否需要查知识库
    - clarification_needed：是否信息不足需要追问
    """

    def __init__(
        self,
        client: Any,
        model: str,
        profiles: AppProfileRegistry,
    ):
        self.client = client
        self.model = model
        self.profiles = profiles

    def rewrite(self, query: str, app_id: str) -> QueryRewriteResult:
        original_query = query.strip()
        # AppProfile 是可信应用背景。
        # LLM 只能基于这里的领域和术语做改写，不能自己编造内部系统概念。
        profile = self.profiles.get(app_id)
        try:
            # 要求模型直接返回 JSON，方便后面用 Pydantic 做结构校验。
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                messages=[
                    {"role": "system", "content": self._system_prompt(profile)},
                    {"role": "user", "content": original_query},
                ],
            )
            content = response.choices[0].message.content
            payload = QueryRewritePayload.model_validate_json(content)
            retrieval_query = payload.rewritten_query.strip() or original_query
            # 关键词用于 BM25，保序去重，并限制最多 16 个。
            # 这里适合保留接口名、字段名、类名、业务术语等精确匹配词。
            keywords = tuple(
                dict.fromkeys(
                    keyword.strip()
                    for keyword in payload.keywords
                    if keyword and keyword.strip()
                )
            )[:16]
            allowed_domains = set(profile.domains)
            # domain_candidates 只能从 AppProfile 允许的 domains 中选。
            # 这是为了防止 LLM 把问题“猜”到一个不存在或无权限的领域。
            domains = tuple(
                dict.fromkeys(
                    domain.strip()
                    for domain in payload.domain_candidates
                    if domain
                    and domain.strip()
                    and (not allowed_domains or domain.strip() in allowed_domains)
                )
            )
            return QueryRewriteResult(
                original_query=original_query,
                retrieval_query=retrieval_query,
                keywords=keywords,
                domain_candidates=domains,
                retrieval_needed=payload.retrieval_needed,
                clarification_needed=payload.clarification_needed,
                rewrite_applied=retrieval_query != original_query or bool(keywords),
                task_type=(
                    payload.task_type
                    if payload.task_type in {
                        "unknown", "how_to", "api_contract", "code_lookup",
                        "requirement_analysis", "metric_query", "bug",
                    }
                    else "unknown"
                ),
            )
        except Exception:
            # 查询改写失败不能影响检索主流程。
            # 失败时使用原问题继续走 BM25 + 向量召回。
            logger.warning(
                "Query rewrite failed; using original query app_id=%r query=%r",
                app_id,
                original_query,
                exc_info=True,
            )
            return self.fallback(original_query)

    @staticmethod
    def fallback(query: str) -> QueryRewriteResult:
        # 最小可用降级：不改写、不提关键词，但仍然允许继续检索。
        return QueryRewriteResult(
            original_query=query,
            retrieval_query=query,
            retrieval_needed=True,
            rewrite_applied=False,
        )

    @staticmethod
    def _system_prompt(profile: AppProfile) -> str:
        profile_json = json.dumps(
            {
                "app_id": profile.app_id,
                "display_name": profile.display_name,
                "description": profile.description,
                "domains": profile.domains,
                "glossary": profile.glossary,
            },
            ensure_ascii=False,
        )
        return f"""
你是企业知识库查询改写器，不负责回答用户问题。

可信应用信息（JSON）：
{profile_json}

任务要求：
1. 将口语问题改写成适合向量检索的完整技术问题。
2. 保留原问题中的接口路径、类名、方法名、字段名和版本号。
3. 只能使用可信应用信息中的内部术语，不得编造公司内部概念。
4. 提取适合 BM25 精确检索的关键词，最多 16 个。
5. 问候、感谢等无需知识库的问题设置 retrieval_needed=false。
6. 信息不足时设置 clarification_needed=true，但不要直接回答。
7. 只输出 JSON，不要输出 Markdown。

JSON 格式：
{{
  "retrieval_needed": true,
  "rewritten_query": "改写后的问题",
  "keywords": ["关键词"],
  "domain_candidates": ["候选领域"],
  "clarification_needed": false,
  "task_type": "how_to|api_contract|code_lookup|requirement_analysis|metric_query|bug|unknown"
}}
""".strip()
