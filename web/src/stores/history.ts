import { defineStore } from "pinia";

import { api } from "@/api";
import type {
  ConversationHistoryDetail,
  ConversationHistoryItem,
  ConversationHistoryPage,
} from "@/types/api";
import { useUserIdentityStore } from "./userIdentity";

function userWriteHeaders(): Record<string, string> {
  const token = useUserIdentityStore().identity?.csrf_token;
  return token ? { "X-User-CSRF-Token": token } : {};
}

export const useHistoryStore = defineStore("conversation-history", {
  state: () => ({
    items: [] as ConversationHistoryItem[],
    total: 0,
    page: 1,
    pageSize: 20,
    query: "",
    selected: null as ConversationHistoryDetail | null,
    loading: false,
    detailLoading: false,
    actionLoading: "",
    error: "",
  }),
  actions: {
    async load(query?: string, page = 1) {
      this.loading = true;
      this.error = "";
      const resolvedQuery = query ?? this.query;
      this.query = resolvedQuery;
      this.page = page;
      try {
        const params = new URLSearchParams({
          page: String(page),
          page_size: String(this.pageSize),
          query: resolvedQuery.trim(),
        });
        const result = await api.get<ConversationHistoryPage>(
          `/v1/agent/conversations?${params.toString()}`,
        );
        this.items = result.items;
        this.total = result.total;
        this.page = result.page;
        this.pageSize = result.page_size;
      } catch (error) {
        this.error = error instanceof Error ? error.message : "无法加载历史会话";
      } finally {
        this.loading = false;
      }
    },
    async open(conversationId: string) {
      this.detailLoading = true;
      this.error = "";
      try {
        this.selected = await api.get<ConversationHistoryDetail>(
          `/v1/agent/conversations/${encodeURIComponent(conversationId)}`,
        );
        return this.selected;
      } catch (error) {
        this.error = error instanceof Error ? error.message : "无法打开历史会话";
        return null;
      } finally {
        this.detailLoading = false;
      }
    },
    async rename(conversationId: string, title: string) {
      this.actionLoading = conversationId;
      try {
        const updated = await api.patch<ConversationHistoryItem>(
          `/v1/agent/conversations/${encodeURIComponent(conversationId)}`,
          { title },
          { headers: userWriteHeaders() },
        );
        this.items = this.items.map((item) =>
          item.conversation_id === conversationId ? { ...item, ...updated } : item,
        );
        if (this.selected?.conversation_id === conversationId) {
          this.selected = { ...this.selected, title: updated.title };
        }
      } finally {
        this.actionLoading = "";
      }
    },
    async remove(conversationId: string) {
      this.actionLoading = conversationId;
      try {
        await api.delete(`/v1/agent/conversations/${encodeURIComponent(conversationId)}`);
        this.items = this.items.filter((item) => item.conversation_id !== conversationId);
        this.total = Math.max(0, this.total - 1);
        if (this.selected?.conversation_id === conversationId) this.selected = null;
      } finally {
        this.actionLoading = "";
      }
    },
  },
});
