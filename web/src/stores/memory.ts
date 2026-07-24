import { defineStore } from "pinia";

import { api } from "@/api";
import type { DomainMemoryPromotion, LongTermMemory, MemoryCandidate, PersonalMemoryStatistics } from "@/types/api";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "长期记忆操作失败";
}

export const useMemoryStore = defineStore("memory", {
  state: () => ({
    candidates: [] as MemoryCandidate[],
    memories: [] as LongTermMemory[],
    personalStatistics: null as PersonalMemoryStatistics | null,
    promotions: [] as DomainMemoryPromotion[],
    loading: false,
    actionLoading: "",
    error: "",
  }),
  actions: {
    async load() {
      this.loading = true;
      this.error = "";
      try {
        const [candidates, memories, personalStatistics, promotions] = await Promise.all([
          api.get<MemoryCandidate[]>("/v1/admin/memory/candidates?status=candidate&limit=200"),
          api.get<LongTermMemory[]>("/v1/admin/memory?status=confirmed&limit=200"),
          api.get<PersonalMemoryStatistics>("/v1/admin/memory/personal-statistics"),
          api.get<DomainMemoryPromotion[]>("/v1/admin/memory/promotions?state=pending"),
        ]);
        this.candidates = candidates;
        this.memories = memories;
        this.personalStatistics = personalStatistics;
        this.promotions = promotions ?? [];
      } catch (error) {
        this.error = errorMessage(error);
      } finally {
        this.loading = false;
      }
    },
    async approve(id: string) {
      this.actionLoading = id;
      try {
        const memory = await api.post<LongTermMemory>(
          `/v1/admin/memory/candidates/${encodeURIComponent(id)}/approve`,
          {},
        );
        this.candidates = this.candidates.filter((item) => item.id !== id);
        this.memories = [memory, ...this.memories.filter((item) => item.id !== memory.id)];
      } catch (error) {
        this.error = errorMessage(error);
        throw error;
      } finally {
        this.actionLoading = "";
      }
    },
    async reject(id: string) {
      this.actionLoading = id;
      try {
        await api.post(`/v1/admin/memory/candidates/${encodeURIComponent(id)}/reject`, {});
        this.candidates = this.candidates.filter((item) => item.id !== id);
      } catch (error) {
        this.error = errorMessage(error);
        throw error;
      } finally {
        this.actionLoading = "";
      }
    },
    async remove(id: string) {
      this.actionLoading = id;
      try {
        await api.delete(`/v1/admin/memory/${encodeURIComponent(id)}`);
        this.memories = this.memories.filter((item) => item.id !== id);
      } catch (error) {
        this.error = errorMessage(error);
        throw error;
      } finally {
        this.actionLoading = "";
      }
    },
    async reviewPromotion(id: string, decision: "approve" | "reject") {
      this.actionLoading = id;
      try {
        await api.post(
          `/v1/admin/memory/promotions/${encodeURIComponent(id)}/${decision}`,
          {},
        );
        this.promotions = this.promotions.filter((item) => item.id !== id);
        if (decision === "approve") await this.load();
      } catch (error) {
        this.error = errorMessage(error);
        throw error;
      } finally {
        this.actionLoading = "";
      }
    },
  },
});
