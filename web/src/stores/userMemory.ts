import { defineStore } from "pinia";

import { api } from "@/api";
import type { LongTermMemory, MemoryCandidate } from "@/types/api";
import { useUserIdentityStore } from "./userIdentity";

export const useUserMemoryStore = defineStore("user-memory", {
  state: () => ({
    memories: [] as LongTermMemory[],
    candidates: [] as MemoryCandidate[],
    loading: false,
    forgetting: "",
    confirming: "",
    rejecting: "",
    error: "",
  }),
  getters: {
    autoConfirmCandidates: (state) =>
      state.candidates.filter((item) =>
        ["user_preference", "user_context"].includes(item.memory_type)),
    explicitReviewCandidates: (state) =>
      state.candidates.filter((item) =>
        !["user_preference", "user_context"].includes(item.memory_type)),
    pendingProcedures: (state) =>
      state.candidates.filter((item) => item.memory_type === "procedural_memory"),
    pendingFacts: (state) =>
      state.candidates.filter((item) => item.memory_type !== "procedural_memory"),
    confirmedProcedures: (state) =>
      state.memories.filter((item) => item.memory_type === "procedural_memory"),
    confirmedFacts: (state) =>
      state.memories.filter((item) => item.memory_type !== "procedural_memory"),
  },
  actions: {
    async load() {
      this.loading = true;
      this.error = "";
      try {
        const [memories, candidates] = await Promise.all([
          api.get<LongTermMemory[]>("/v1/memory"),
          api.get<MemoryCandidate[]>("/v1/memory/candidates"),
        ]);
        this.memories = memories ?? [];
        this.candidates = candidates ?? [];
      } catch (error) {
        this.error = error instanceof Error ? error.message : "无法加载长期记忆";
      } finally {
        this.loading = false;
      }
    },
    async forget(id: string) {
      this.forgetting = id;
      try {
        await api.delete(`/v1/memory/${encodeURIComponent(id)}`);
        this.memories = this.memories.filter((item) => item.id !== id);
      } finally {
        this.forgetting = "";
      }
    },
    async confirm(id: string) {
      const identity = useUserIdentityStore().identity;
      const csrfToken = identity?.csrf_token;
      if (!identity) throw new Error("当前身份尚未加载");
      this.confirming = id;
      try {
        const memory = await api.post<LongTermMemory>(
          `/v1/memory/candidates/${encodeURIComponent(id)}/confirm`,
          {},
          { headers: csrfToken ? { "X-User-CSRF-Token": csrfToken } : {} },
        );
        this.candidates = this.candidates.filter((item) => item.id !== id);
        this.memories = [memory, ...this.memories.filter((item) => item.id !== id)];
      } finally {
        this.confirming = "";
      }
    },
    async reject(id: string) {
      const identity = useUserIdentityStore().identity;
      const csrfToken = identity?.csrf_token;
      if (!identity) throw new Error("当前身份尚未加载");
      this.rejecting = id;
      try {
        await api.post(
          `/v1/memory/candidates/${encodeURIComponent(id)}/reject`,
          {},
          { headers: csrfToken ? { "X-User-CSRF-Token": csrfToken } : {} },
        );
        this.candidates = this.candidates.filter((item) => item.id !== id);
      } finally {
        this.rejecting = "";
      }
    },
  },
});
