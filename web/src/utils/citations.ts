import type { Citation, CitationSourceType } from "@/types/api";

const LABELS: Record<CitationSourceType, string> = {
  knowledge_chunk: "知识片段",
  mcp_tool: "MCP 工具",
  code: "代码",
  product_document: "产品文档",
  swagger: "Swagger",
  log_trace: "日志 Trace",
};

export function citationLabel(citation: Pick<Citation, "source_type">): string {
  return LABELS[citation.source_type] ?? "引用";
}

function basename(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "";
  return value.trim().replaceAll("\\", "/").split("/").at(-1) || "";
}

function semanticTitle(citation: Citation): string {
  const title = citation.title?.trim() || "";
  if (!title || title === citation.source_id) return "";
  if (/^(?:chunk|code|doc(?:ument)?|source|swagger)[-_][a-z0-9._:/-]{6,}$/i.test(title)) {
    return "";
  }
  return title;
}

export function citationDisplayName(citation: Citation): string {
  const metadata = citation.metadata;
  const title = semanticTitle(citation);
  if (citation.source_type === "code") {
    const file = basename(metadata.relative_path ?? metadata.path);
    const symbol = String(metadata.symbol_name ?? metadata.symbol ?? metadata.method ?? title ?? "").trim();
    const parts = [...new Set([file, symbol].filter(Boolean))];
    return parts.length ? `代码：${parts.join(" / ")}` : "代码位置";
  }
  if (citation.source_type === "product_document") {
    const name = title || basename(metadata.relative_path ?? metadata.path);
    return name ? `文档：《${name}》` : "产品文档";
  }
  if (citation.source_type === "knowledge_chunk") {
    const name = title || String(metadata.heading ?? "").trim();
    return name ? `知识文档：《${name}》` : "知识文档";
  }
  if (citation.source_type === "swagger") {
    const method = String(metadata.method ?? "").trim().toUpperCase();
    const path = String(metadata.path ?? "").trim();
    const operation = [method, path].filter(Boolean).join(" ") || title;
    return operation ? `接口：${operation}` : "接口定义";
  }
  if (citation.source_type === "log_trace") {
    const environment = String(metadata.environment ?? "").trim().toLowerCase();
    const environmentName = ({
      develop: "开发环境", dev: "开发环境", test: "测试环境",
      prod: "生产环境", production: "生产环境", master: "生产环境",
    } as Record<string, string>)[environment] || "目标环境";
    return `${environmentName}日志证据`;
  }
  if (citation.source_type === "mcp_tool") return "指标平台查询结果";
  return "检索证据";
}

export function sanitizeCitationText(text: string, citations: Citation[]): string {
  let answer = text || "";
  for (const citation of [...citations].sort((left, right) => right.source_id.length - left.source_id.length)) {
    if (citation.source_id) answer = answer.replaceAll(citation.source_id, citationDisplayName(citation));
  }
  return answer.replace(
    /\b(?:chunk|code|doc(?:ument)?|source|swagger)[-_](?=[a-z0-9._:/-]{6,})(?=[a-z0-9._:/-]*\d)[a-z0-9._:/-]+/gi,
    (token) => {
      const normalized = token.toLowerCase();
      if (normalized.startsWith("code-") || normalized.startsWith("code_")) return "代码位置";
      if (normalized.startsWith("swagger-") || normalized.startsWith("swagger_")) return "接口定义";
      return "知识文档";
    },
  );
}

export function citationPermalink(citation: Citation): string | null {
  const value = citation.metadata.gitlab_url;
  return typeof value === "string" && /^https?:\/\//.test(value) ? value : null;
}

export function visibleCitationMetadata(citation: Citation): Array<[string, string]> {
  const metadata = citation.metadata;
  const rows: Array<[string, string]> = [];
  if (metadata.start_line !== undefined || metadata.end_line !== undefined) {
    const start = String(metadata.start_line ?? metadata.end_line ?? "");
    const end = String(metadata.end_line ?? metadata.start_line ?? "");
    rows.push(["行号", start === end ? start : `${start}-${end}`]);
  }
  const mappings: Array<[string, unknown]> = [
    ["文件", metadata.relative_path],
    ["页码", metadata.page_number],
    ["方法", metadata.method],
    ["路径", metadata.path],
    ["刷新时间", metadata.refreshed_at],
    ["环境", metadata.environment],
    ["日志数量", metadata.log_count],
    [
      "异常类型",
      Array.isArray(metadata.exception_types)
        ? metadata.exception_types.join(", ")
        : metadata.exception_types,
    ],
  ];
  for (const [label, value] of mappings) {
    if (value !== undefined && value !== null && value !== "") rows.push([label, String(value)]);
  }
  if (metadata.stale === true) rows.push(["状态", "缓存数据"]);
  return rows;
}
