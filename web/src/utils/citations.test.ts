import { describe, expect, it } from "vitest";

import {
  citationDisplayName,
  citationLabel,
  citationPermalink,
  sanitizeCitationText,
  visibleCitationMetadata,
} from "./citations";

describe("citation utilities", () => {
  it("maps typed citations and exposes code permalinks", () => {
    const citation = {
      source_type: "code" as const,
      source_id: "code-1",
      title: "MetricService.query",
      domain: "指标平台",
      metadata: {
        gitlab_url: "https://gitlab.example/blob/abc/MetricService.java#L10-20",
        start_line: 10,
        end_line: 20,
        content: "private body",
      },
    };

    expect(citationLabel(citation)).toBe("代码");
    expect(citationPermalink(citation)).toContain("gitlab.example");
    expect(visibleCitationMetadata(citation)).toEqual([
      ["行号", "10-20"],
    ]);
  });

  it("labels every backend citation type", () => {
    expect(
      ["knowledge_chunk", "mcp_tool", "product_document", "swagger", "log_trace"].map((source_type) =>
        citationLabel({ source_type } as never),
      ),
    ).toEqual(["知识片段", "MCP 工具", "产品文档", "Swagger", "日志 Trace"]);
  });

  it("shows public log metadata without raw lines", () => {
    const rows = visibleCitationMetadata({
      source_type: "log_trace",
      source_id: "trace-1",
      title: "test trace",
      domain: "中台",
      metadata: {
        environment: "test",
        log_count: 3,
        exception_types: ["NullPointerException"],
      },
    });

    expect(rows).toEqual([
      ["环境", "test"],
      ["日志数量", "3"],
      ["异常类型", "NullPointerException"],
    ]);
  });

  it("uses a Chinese semantic name and never falls back to source_id", () => {
    expect(citationDisplayName({
      source_type: "code",
      source_id: "code-889c460d7d7d4e46a824",
      title: "",
      domain: "审批流",
      metadata: {
        relative_path: "approval/TransferService.java",
        symbol_name: "TransferService.run",
      },
    })).toBe("代码：TransferService.java / TransferService.run");

    expect(citationDisplayName({
      source_type: "knowledge_chunk",
      source_id: "chunk-0123456789abcdef",
      title: "chunk-0123456789abcdef",
      domain: "工作流",
      metadata: {},
    })).toBe("知识文档");
  });

  it("sanitizes IDs in previously stored answers", () => {
    const citation = {
      source_type: "product_document" as const,
      source_id: "doc-3d111c3c76884e91",
      title: "管理员转办说明",
      domain: "审批流",
      metadata: {},
    };

    const answer = sanitizeCitationText(
      "来源是 doc-3d111c3c76884e91，另见 chunk-0123456789abcdef。",
      [citation],
    );

    expect(answer).toContain("文档：《管理员转办说明》");
    expect(answer).toContain("知识文档");
    expect(answer).not.toContain("doc-3d111");
    expect(answer).not.toContain("chunk-012345");
  });
});
