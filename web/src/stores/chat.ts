import { defineStore } from "pinia";

import { api } from "@/api";
import { streamChatEvents } from "@/api/chat";
import type {
  AgentResponse,
  Citation,
  ConversationHistoryDetail,
  KnowledgeSpace,
  ToolRun,
} from "@/types/api";

export interface ChatScope {
  knowledgeSpaceId: string;
  domainId: string | null;
  label: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  toolRuns?: ToolRun[];
  qualityTurnId?: string;
  feedbackToken?: string;
  feedbackRating?: "positive" | "negative";
  feedbackLoading?: boolean;
  agentName?: string;
  routedDomains?: string[];
}

const ACTIVE_CONVERSATION_KEY = "middle-platform-agent.active-conversation";

function storedConversationId(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(ACTIVE_CONVERSATION_KEY);
}

function persistConversationId(conversationId: string | null) {
  if (typeof localStorage === "undefined") return;
  if (conversationId) localStorage.setItem(ACTIVE_CONVERSATION_KEY, conversationId);
  else localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
}

function publicChatError(error: unknown): string {
  const message = error instanceof Error ? error.message.trim() : "";
  if (/network error|networkerror|failed to fetch|load failed/i.test(message)) {
    return "网络连接已中断，请稍后重试。";
  }
  return message || "对话请求失败，请稍后重试。";
}

export const useChatStore = defineStore("chat", {
  state: () => ({
    spaces: [] as KnowledgeSpace[],
    spacesLoading: false,
    restoringConversation: false,
    scope: null as ChatScope | null,
    conversationId: null as string | null,
    messages: [] as ChatMessage[],
    streaming: false,
    activeTools: [] as Array<{ id: string; name: string; status: string }>,
    activeAgent: "",
    error: "",
  }),
  actions: {
    selectScope(scope: ChatScope) {
      const changed =
        !this.scope ||
        this.scope.knowledgeSpaceId !== scope.knowledgeSpaceId ||
        this.scope.domainId !== scope.domainId;
      this.scope = { ...scope };
      if (changed) this.resetConversation();
    },
    resetConversation() {
      this.conversationId = null;
      this.messages = [];
      this.activeTools = [];
      this.streaming = false;
      this.error = "";
      this.activeAgent = "";
      persistConversationId(null);
    },
    restoreConversation(detail: ConversationHistoryDetail) {
      const space = this.spaces.find(
        (item) => item.id === detail.knowledge_space_id,
      );
      const domain = space?.domains.find((item) => item.id === detail.domain_id);
      this.resetConversation();
      this.scope = {
        knowledgeSpaceId: detail.knowledge_space_id || space?.id || "middle-platform",
        domainId: detail.domain_id,
        label: domain?.name || space?.name || detail.domain_id || "中台",
      };
      this.conversationId = detail.conversation_id;
      persistConversationId(this.conversationId);
      this.messages = detail.messages.map((item) => ({
        id: `history-${item.id}`,
        role: item.role,
        content: item.content,
        citations: [],
        agentName: item.role === "assistant" ? "知识助手" : undefined,
      }));
    },
    async loadSpaces() {
      this.spacesLoading = true;
      this.error = "";
      try {
        this.spaces = await api.get<KnowledgeSpace[]>("/v1/knowledge/spaces");
        if (!this.scope && this.spaces[0]) {
          this.selectScope({
            knowledgeSpaceId: this.spaces[0].id,
            domainId: null,
            label: this.spaces[0].name,
          });
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : "知识范围加载失败";
        if (!this.scope) {
          this.selectScope({
            knowledgeSpaceId: "middle-platform",
            domainId: null,
            label: "中台",
          });
        }
      } finally {
        this.spacesLoading = false;
      }
    },
    async restorePersistedConversation() {
      const conversationId = storedConversationId();
      this.restoringConversation = true;
      try {
        const spacesRequest = this.loadSpaces();
        if (!conversationId) {
          await spacesRequest;
          return;
        }
        const detailRequest = api.get<ConversationHistoryDetail>(
          `/v1/agent/conversations/${encodeURIComponent(conversationId)}`,
        );
        const [, detailResult] = await Promise.allSettled([
          spacesRequest,
          detailRequest,
        ]);
        if (detailResult.status === "rejected") throw detailResult.reason;
        this.restoreConversation(detailResult.value);
      } catch {
        persistConversationId(null);
      } finally {
        this.restoringConversation = false;
      }
    },
    async sendMessage(value: string) {
      const message = value.trim();
      if (!message || !this.scope || this.streaming) return;
      const createId = () =>
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random()}`;
      const assistant: ChatMessage = {
        id: createId(),
        role: "assistant",
        content: "",
        citations: [],
      };
      this.messages.push({ id: createId(), role: "user", content: message, citations: [] });
      this.messages.push(assistant);
      const assistantIndex = this.messages.length - 1;
      const currentAssistant = () => this.messages[assistantIndex];
      this.streaming = true;
      this.error = "";
      this.activeTools = [];
      this.activeAgent = "";
      try {
        const events = streamChatEvents({
          message,
          conversation_id: this.conversationId,
          knowledge_space_id: this.scope.knowledgeSpaceId,
          domain_id: this.scope.domainId,
        });
        for await (const event of events) {
          const data = event.data as Record<string, unknown>;
          if (event.event === "run.started") {
            this.conversationId = String(data.conversation_id ?? this.conversationId ?? "");
            persistConversationId(this.conversationId || null);
          } else if (event.event === "agent.updated") {
            this.activeAgent = String(data.agent ?? "");
          } else if (event.event === "text.delta") {
            currentAssistant().content += String(data.delta ?? "");
          } else if (event.event === "tool.started") {
            this.activeTools.push({
              id: String(data.tool_call_id ?? ""),
              name: String(data.tool_name ?? "unknown"),
              status: "running",
            });
          } else if (event.event === "tool.completed") {
            const tool = this.activeTools.find(
              (item) => item.id === String(data.tool_call_id ?? ""),
            );
            if (tool) tool.status = "completed";
          } else if (event.event === "run.completed" || event.event === "approval.required") {
            const result = event.data as AgentResponse;
            this.conversationId = result.conversation_id;
            persistConversationId(this.conversationId);
            currentAssistant().content = result.answer ?? currentAssistant().content;
            currentAssistant().citations = result.citations;
            currentAssistant().toolRuns = result.tool_runs;
            currentAssistant().agentName =
              result.specialists_used?.join(" + ") ||
              (result.last_agent !== "Manager Agent" ? result.last_agent : "知识助手");
            currentAssistant().routedDomains = result.routed_domains ?? [];
            currentAssistant().qualityTurnId = result.quality_turn_id ?? undefined;
            currentAssistant().feedbackToken = result.feedback_token ?? undefined;
            this.streaming = false;
            break;
          } else if (event.event === "run.error") {
            this.streaming = false;
            throw new Error(String(data.error ?? "对话运行失败"));
          }
        }
      } catch (error) {
        this.error = publicChatError(error);
        if (!currentAssistant().content) currentAssistant().content = this.error;
      } finally {
        this.streaming = false;
      }
    },
    async submitFeedback(
      messageId: string,
      rating: "positive" | "negative",
      reason = "",
      reasonCode = "",
    ) {
      const message = this.messages.find((item) => item.id === messageId);
      if (!message?.qualityTurnId || !message.feedbackToken || message.feedbackLoading) return;
      message.feedbackLoading = true;
      try {
        await api.post(`/v1/quality/turns/${encodeURIComponent(message.qualityTurnId)}/feedback`, {
          feedback_token: message.feedbackToken,
          rating,
          reason,
          reason_code: reasonCode,
        });
        message.feedbackRating = rating;
      } finally {
        message.feedbackLoading = false;
      }
    },
  },
});
