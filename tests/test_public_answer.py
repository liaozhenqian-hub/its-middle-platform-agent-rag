from knowledge.agent_runtime.context import Citation
from knowledge.agent_runtime.public_answer import (
    PublicAnswerStream,
    public_citation_name,
    sanitize_public_answer,
)


def test_public_answer_replaces_internal_citation_ids_with_chinese_names():
    citations = [
        Citation(
            source_type="code",
            source_id="code-889c460d7d7d4e46a824",
            title="TransferService.run",
            domain="审批流",
            metadata={
                "relative_path": "approval/TransferService.java",
                "symbol_name": "TransferService.run",
            },
        ),
        Citation(
            source_type="product_document",
            source_id="doc-3d111c3c76884e91",
            title="管理员转办说明",
            domain="审批流",
            metadata={"relative_path": "docs/admin-transfer.md"},
        ),
    ]

    answer = sanitize_public_answer(
        "证据来自 code-889c460d7d7d4e46a824 和 doc-3d111c3c76884e91。",
        citations,
    )

    assert "code-889c" not in answer
    assert "doc-3d111" not in answer
    assert "代码：TransferService.java / TransferService.run" in answer
    assert "文档：《管理员转办说明》" in answer


def test_public_citation_name_never_falls_back_to_internal_id():
    citation = Citation(
        source_type="knowledge_chunk",
        source_id="chunk-aabbccddeeff0011",
        title="chunk-aabbccddeeff0011",
        domain="工作流",
        metadata={},
    )

    assert public_citation_name(citation) == "知识文档"


def test_public_answer_stream_hides_id_split_across_deltas():
    citation = Citation(
        source_type="code",
        source_id="code-889c460d7d7d4e46a824",
        title="TransferService.run",
        metadata={"relative_path": "TransferService.java"},
    )
    stream = PublicAnswerStream(lambda: [citation], tail_chars=48)

    output = "".join(
        [
            stream.feed("实现见 code-889c460d"),
            stream.feed("7d7d4e46a824，调用完成。"),
            stream.flush(),
        ]
    )

    assert "code-889c" not in output
    assert "代码：TransferService.java" in output


def test_public_answer_removes_unmapped_chunk_identifiers():
    answer = sanitize_public_answer(
        "根据 chunk_id: chunk-0123456789abcdef 可以确认。",
        [],
    )

    assert "chunk_id" not in answer.casefold()
    assert "chunk-012345" not in answer
    assert "知识文档" in answer
