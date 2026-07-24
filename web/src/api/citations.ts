import { api } from "./index";
import type { Citation, CitationDetail } from "@/types/api";

const DETAIL_SOURCE_TYPES = new Set([
  "code",
  "product_document",
  "knowledge_chunk",
  "swagger",
]);

export function citationHasDetail(citation: Citation): boolean {
  return DETAIL_SOURCE_TYPES.has(citation.source_type);
}

export function fetchCitationDetail(
  citation: Citation,
  signal?: AbortSignal,
  view: "section" | "full" = "section",
): Promise<CitationDetail> {
  const query = new URLSearchParams({
    source_type: citation.source_type,
    source_id: citation.source_id,
  });
  if (view === "full") query.set("view", "full");
  return api.get<CitationDetail>(`/v1/citations/detail?${query.toString()}`, { signal });
}
