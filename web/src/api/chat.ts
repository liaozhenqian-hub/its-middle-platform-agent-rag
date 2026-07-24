import { ApiError } from "./client";
import { parseSseStream, type SseEvent } from "@/utils/sse";

export interface ChatStreamRequest {
  message: string;
  conversation_id: string | null;
  knowledge_space_id: string | null;
  domain_id: string | null;
}

export async function* streamChatEvents(body: ChatStreamRequest): AsyncGenerator<SseEvent> {
  const response = await fetch("/api/v1/agent/chat/stream", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-Client-Channel": "web" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let message = response.statusText || "对话请求失败";
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") message = payload.detail;
    } catch {
      // Preserve the HTTP fallback when an upstream proxy returns non-JSON.
    }
    throw new ApiError(response.status, message);
  }
  if (!response.body) throw new ApiError(502, "对话流不可用");
  yield* parseSseStream(response.body);
}
