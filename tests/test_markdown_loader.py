from pathlib import Path

from knowledge.loaders.markdown_loader import MarkdownKnowledgeLoader


SAMPLE_MARKDOWN = """---
title: 指标平台知识库
domain: 指标平台
last_reviewed: 2026-06-25
---

# 指标平台知识库

## SDK 开放接口

> chunk_id: mp-sdk
> chunk_type: sdk_interface
> domain: 指标平台
> module: SDK开放接口
> interface_type: SDK开放接口
> retrieval_priority: high
> bm25_keywords: MetricClient, /api/datacenter/v2/getData

接口类型：SDK开放接口
适用问题：SDK 怎么查指标应用数据
关键词：MetricClient, getDataV2

SDK 用户应使用 `MetricClient.getDataV2`。

### FAQ：SDK 怎么查询指标应用数据？

> chunk_id: mp-faq-sdk-data
> chunk_type: faq
> domain: 指标平台
> module: 售后FAQ
> interface_type: SDK开放接口
> retrieval_priority: high
> bm25_keywords: SDK查询, getDataV2

标准回答：使用 `POST /api/datacenter/v2/getData`。
"""


def test_loader_parses_frontmatter_and_chunk_metadata(tmp_path: Path):
    source = tmp_path / "knowledge.md"
    source.write_text(SAMPLE_MARKDOWN, encoding="utf-8")

    loader = MarkdownKnowledgeLoader(max_chunk_chars=2000, overlap_chars=100)
    result = loader.load(source)

    assert result.frontmatter["title"] == "指标平台知识库"
    assert result.frontmatter["domain"] == "指标平台"
    assert [chunk.chunk_id for chunk in result.chunks] == ["mp-sdk", "mp-faq-sdk-data"]
    assert result.chunks[0].metadata["chunk_type"] == "sdk_interface"
    assert result.chunks[0].metadata["interface_type"] == "SDK开放接口"
    assert result.chunks[1].metadata["chunk_type"] == "faq"
    assert "POST /api/datacenter/v2/getData" in result.chunks[1].content


def test_faq_chunk_remains_independent(tmp_path: Path):
    source = tmp_path / "knowledge.md"
    source.write_text(SAMPLE_MARKDOWN, encoding="utf-8")

    result = MarkdownKnowledgeLoader(max_chunk_chars=2000, overlap_chars=100).load(source)

    faq = next(chunk for chunk in result.chunks if chunk.metadata["chunk_type"] == "faq")
    assert faq.heading == "FAQ：SDK 怎么查询指标应用数据？"
    assert "## SDK 开放接口" not in faq.content
    assert faq.metadata["section_path"] == "SDK 开放接口 > FAQ：SDK 怎么查询指标应用数据？"


def test_long_chunk_is_split_and_keeps_parent_chunk_id(tmp_path: Path):
    source = tmp_path / "knowledge.md"
    long_text = "指标平台查询规则。" * 80
    source.write_text(
        f"""---
title: 长文本
---

## 长文档

> chunk_id: mp-long
> chunk_type: concept
> domain: 指标平台

{long_text}
""",
        encoding="utf-8",
    )

    result = MarkdownKnowledgeLoader(max_chunk_chars=120, overlap_chars=20).load(source)

    assert len(result.chunks) > 1
    assert result.chunks[0].chunk_id == "mp-long#p001"
    assert result.chunks[1].chunk_id == "mp-long#p002"
    assert all(chunk.metadata["parent_chunk_id"] == "mp-long" for chunk in result.chunks)


def test_real_metric_document_dry_run_reads_existing_chunk_ids():
    source = Path(r"D:\javaProgram\metric-platform-knowledge.md")
    result = MarkdownKnowledgeLoader(max_chunk_chars=5000, overlap_chars=200).load(source)

    assert len({chunk.metadata["parent_chunk_id"] for chunk in result.chunks}) >= 70
    assert any(chunk.metadata.get("chunk_type") == "faq" for chunk in result.chunks)
