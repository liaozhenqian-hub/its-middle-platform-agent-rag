from knowledge.agent_runtime.context import AgentRunContext


def test_context_enforces_retrieval_budget_and_duplicate_queries():
    context = AgentRunContext("conversation-1", "run-1")

    assert context.reserve_retrieval("search_domain_code", "HTTP 节点 重试", 2, 1) == "allowed"
    assert context.reserve_retrieval("search_domain_code", "http节点重试", 2, 1) == "duplicate"
    assert context.reserve_retrieval("search_domain_code", "默认分支", 2, 1) == "allowed"
    assert context.reserve_retrieval("search_domain_documents", "变量作用域", 2, 1) == "budget_exhausted"
    serialized = context.to_dict()
    assert "retrieval_signatures" not in serialized
    assert "current_user_message" not in serialized


def test_context_deduplicates_across_tools_and_caps_four_distinct_retrievals():
    context = AgentRunContext("conversation-1", "run-1")
    base = {
        "query": "SDK authentication",
        "app_id": "middle-platform",
        "domain_id": "metric-platform",
        "branch": None,
        "task_type": "how_to",
        "max_calls": 4,
    }

    assert context.reserve_retrieval(source_type="product_document", **base) == "allowed"
    assert context.reserve_retrieval(
        source_type="product_document", **{**base, "query": " sdk  authentication! "}
    ) == "duplicate"
    assert context.reserve_retrieval(source_type="code", **base) == "allowed"
    assert context.reserve_retrieval(source_type="swagger", **base) == "allowed"
    assert context.reserve_retrieval(
        source_type="product_document", **{**base, "query": "SDK setup"}
    ) == "allowed"
    assert context.reserve_retrieval(
        source_type="code", **{**base, "query": "SDK setup"}
    ) == "budget_exhausted"


def test_context_aggregates_public_citations_by_logical_source():
    context = AgentRunContext("conversation-1", "run-1")
    context.add_knowledge_citation(
        "chunk-1",
        "连接器变量",
        "工作流",
        {
            "source_type": "product_document",
            "source_id": "workflow-docs",
            "relative_path": "connector.md",
            "heading": "连接器变量",
        },
    )
    context.add_knowledge_citation(
        "chunk-2",
        "连接器变量",
        "工作流",
        {
            "source_type": "product_document",
            "source_id": "workflow-docs",
            "relative_path": "connector.md",
            "heading": "连接器变量",
        },
    )
    context.add_knowledge_citation(
        "code-1",
        "ConnectorCmp.doProcess",
        "工作流",
        {
            "source_type": "code",
            "branch": "master",
            "relative_path": "ConnectorCmp.java",
            "symbol_name": "ConnectorCmp.doProcess",
        },
    )
    context.add_knowledge_citation(
        "code-2",
        "ConnectorCmp.doProcess",
        "工作流",
        {
            "source_type": "code",
            "branch": "master",
            "relative_path": "ConnectorCmp.java",
            "symbol_name": "ConnectorCmp.doProcess",
        },
    )

    citations = context.public_citations(10)

    assert [(item.source_type, item.title) for item in citations] == [
        ("product_document", "连接器变量"),
        ("code", "ConnectorCmp.doProcess"),
    ]


def test_context_caps_public_citations_but_keeps_source_type_coverage():
    context = AgentRunContext("conversation-1", "run-1")
    context.add_mcp_citation("searchBizMetric")
    for index in range(12):
        context.add_knowledge_citation(
            f"code-{index}",
            f"Symbol{index}",
            "指标平台",
            {
                "source_type": "code",
                "branch": "master",
                "relative_path": f"File{index}.java",
                "symbol_name": f"Symbol{index}",
            },
        )

    citations = context.public_citations(4)

    assert len(citations) == 4
    assert {item.source_type for item in citations} == {"mcp_tool", "code"}


def test_public_citations_keep_only_strong_results_without_padding():
    context = AgentRunContext("conversation", "run")
    for source_id, score in (("strong-a", 0.91), ("strong-b", 0.73), ("weak", 0.12)):
        context.add_knowledge_citation(
            source_id,
            source_id,
            "Approval flow",
            {
                "source_type": "product_document",
                "source_id": source_id,
                "_retrieval": {
                    "exact": False,
                    "rerank_applied": True,
                    "rerank_score": score,
                    "fusion_score": 0.03,
                    "rank": 1,
                },
            },
        )

    citations = context.public_citations(
        5, min_rerank_score=0.35, min_rrf_score=0.02
    )

    assert [item.source_id for item in citations] == ["strong-a", "strong-b"]


def test_public_citations_accept_exact_and_strict_rrf_fallback():
    context = AgentRunContext("conversation", "run")
    for source_id, retrieval in (
        ("exact", {"exact": True, "rank": 3}),
        (
            "rrf",
            {
                "exact": False,
                "rerank_applied": False,
                "fusion_score": 0.021,
                "rank": 2,
            },
        ),
        (
            "weak-rrf",
            {
                "exact": False,
                "rerank_applied": False,
                "fusion_score": 0.019,
                "rank": 1,
            },
        ),
    ):
        context.add_knowledge_citation(
            source_id,
            source_id,
            "Workflow",
            {
                "source_type": "code",
                "relative_path": f"{source_id}.java",
                "symbol_name": source_id,
                "_retrieval": retrieval,
            },
        )

    citations = context.public_citations(
        5, min_rerank_score=0.35, min_rrf_score=0.02
    )

    assert [item.source_id for item in citations] == ["exact", "rrf"]


def test_context_hides_same_title_code_and_document_duplicates_from_public_output():
    context = AgentRunContext("conversation-1", "run-1")
    for branch, path in (("develop", "A.java"), ("master", "B.java")):
        context.add_knowledge_citation(
            f"code-{branch}",
            "WorkflowService.execute",
            "工作流",
            {
                "source_type": "code",
                "branch": branch,
                "relative_path": path,
                "symbol_name": "WorkflowService.execute",
            },
        )
    for source_id, path in (("doc-a", "a.md"), ("doc-b", "b.md")):
        context.add_knowledge_citation(
            source_id,
            "异常分支",
            "工作流",
            {
                "source_type": "product_document",
                "source_id": source_id,
                "relative_path": path,
                "heading": "异常分支",
            },
        )

    citations = context.public_citations(10)

    assert [(item.source_type, item.title) for item in citations] == [
        ("code", "WorkflowService.execute"),
        ("product_document", "异常分支"),
    ]
