import { defineStore } from "pinia";

import { api } from "@/api";
import type {
  EvalCase,
  EvalCasePayload,
  EvalResult,
  EvalRun,
  QualityAnalytics,
  QualityAnnotation,
  QualityAnnotationPage,
  QualityTurn,
  QualityTurnPage,
} from "@/types/api";

function messageFrom(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export const useQualityStore = defineStore("quality", {
  state: () => ({
    turns: [] as QualityTurn[],
    total: 0,
    page: 1,
    pageSize: 20,
    selectedTurn: null as QualityTurn | null,
    evalCases: [] as EvalCase[],
    evalRuns: [] as EvalRun[],
    selectedRun: null as EvalRun | null,
    evalResults: [] as EvalResult[],
    analytics: null as QualityAnalytics | null,
    annotations: [] as QualityAnnotation[],
    annotationTotal: 0,
    filters: {
      channel: "",
      status: "",
      rating: "",
      domainId: "",
      userId: "",
      query: "",
      modelName: "",
      annotationCode: "",
    },
    loading: false,
    detailLoading: false,
    actionLoading: false,
    error: "",
  }),
  actions: {
    async loadTurns(page?: number) {
      this.loading = true;
      this.error = "";
      try {
        const targetPage = page ?? this.page;
        const query = new URLSearchParams({ page: String(targetPage), page_size: String(this.pageSize) });
        if (this.filters.channel) query.set("channel", this.filters.channel);
        if (this.filters.status) query.set("status", this.filters.status);
        if (this.filters.rating) query.set("rating", this.filters.rating);
        if (this.filters.domainId) query.set("domain_id", this.filters.domainId);
        if (this.filters.userId) query.set("user_id", this.filters.userId);
        if (this.filters.query.trim()) query.set("query", this.filters.query.trim());
        const result = await api.get<QualityTurnPage>(`/v1/admin/quality/turns?${query}`);
        this.turns = result.items;
        this.total = result.total;
        this.page = result.page;
      } catch (error) {
        this.error = messageFrom(error, "问答质量数据加载失败");
      } finally {
        this.loading = false;
      }
    },
    async loadAnalytics() {
      const query = new URLSearchParams();
      if (this.filters.channel) query.set("channel", this.filters.channel);
      if (this.filters.domainId) query.set("domain_id", this.filters.domainId);
      if (this.filters.modelName) query.set("model_name", this.filters.modelName);
      if (this.filters.annotationCode) query.set("annotation_code", this.filters.annotationCode);
      this.analytics = await api.get<QualityAnalytics>(`/v1/admin/quality/analytics?${query}`);
    },
    async loadAnnotations() {
      const query = new URLSearchParams({ page: "1", page_size: "100" });
      if (this.filters.annotationCode) query.set("code", this.filters.annotationCode);
      const page = await api.get<QualityAnnotationPage>(`/v1/admin/quality/annotations?${query}`);
      this.annotations = page.items;
      this.annotationTotal = page.total;
    },
    async reviewAnnotation(annotationId: string, reviewStatus: "confirmed" | "dismissed") {
      const updated = await api.patch<QualityAnnotation>(
        `/v1/admin/quality/annotations/${encodeURIComponent(annotationId)}`,
        { review_status: reviewStatus },
      );
      this.annotations = this.annotations.map((item) => item.id === updated.id ? updated : item);
      return updated;
    },
    async loadTurnDetail(turnId: string) {
      this.detailLoading = true;
      try {
        this.selectedTurn = await api.get<QualityTurn>(
          `/v1/admin/quality/turns/${encodeURIComponent(turnId)}`,
        );
      } finally {
        this.detailLoading = false;
      }
    },
    async deleteTurn(turnId: string) {
      return this.runAction(async () => {
        await api.delete(`/v1/admin/quality/turns/${encodeURIComponent(turnId)}`);
        this.turns = this.turns.filter((item) => item.id !== turnId);
        this.total = Math.max(0, this.total - 1);
        if (this.selectedTurn?.id === turnId) this.selectedTurn = null;
      });
    },
    async promoteTurn(turnId: string, payload: EvalCasePayload) {
      return this.runAction(async () => {
        const created = await api.post<EvalCase>(
          `/v1/admin/quality/turns/${encodeURIComponent(turnId)}/eval-case`,
          payload,
        );
        this.evalCases = [created, ...this.evalCases.filter((item) => item.id !== created.id)];
        return created;
      });
    },
    async loadEvalCases() {
      this.evalCases = await api.get<EvalCase[]>("/v1/admin/quality/eval-cases");
    },
    async deleteEvalCase(caseId: string) {
      await this.runAction(async () => {
        await api.delete(`/v1/admin/quality/eval-cases/${encodeURIComponent(caseId)}`);
        this.evalCases = this.evalCases.filter((item) => item.id !== caseId);
      });
    },
    async setEvalApproval(item: EvalCase, approvalState: "approved" | "rejected") {
      const updated = await api.put<EvalCase>(
        `/v1/admin/quality/eval-cases/${encodeURIComponent(item.id)}`,
        {
          name: item.name,
          question: item.question,
          knowledge_space_id: item.knowledge_space_id,
          domain_id: item.domain_id,
          required_tools: item.required_tools,
          required_citation_types: item.required_citation_types,
          required_facts: item.required_facts,
          forbidden_facts: item.forbidden_facts,
          tags: item.tags,
          enabled: item.enabled,
          expected_behavior: item.expected_behavior,
          max_latency_ms: item.max_latency_ms,
          max_tool_calls: item.max_tool_calls,
          max_citations: item.max_citations,
          turns: item.turns,
          task_type: item.task_type,
          suite: item.suite,
          priority: item.priority,
          approval_state: approvalState,
        },
      );
      this.evalCases = this.evalCases.map((value) => value.id === updated.id ? updated : value);
      return updated;
    },
    async runEvaluation(caseIds: string[]) {
      return this.runAction(async () => {
        const run = await api.post<EvalRun>("/v1/admin/quality/eval-runs", {
          case_ids: caseIds,
        });
        this.evalRuns = [run, ...this.evalRuns.filter((item) => item.id !== run.id)];
        return run;
      });
    },
    async loadEvalRuns() {
      this.evalRuns = await api.get<EvalRun[]>("/v1/admin/quality/eval-runs");
    },
    async loadEvalRunDetail(runId: string) {
      const detail = await api.get<{ run: EvalRun; results: EvalResult[] }>(
        `/v1/admin/quality/eval-runs/${encodeURIComponent(runId)}`,
      );
      this.selectedRun = detail.run;
      this.evalResults = detail.results;
    },
    async cancelEvaluation(runId: string) {
      const run = await api.post<EvalRun>(
        `/v1/admin/quality/eval-runs/${encodeURIComponent(runId)}/cancel`,
        {},
      );
      this.evalRuns = this.evalRuns.map((item) => item.id === run.id ? run : item);
      this.selectedRun = run;
      return run;
    },
    async retryFailedEvaluation(runId: string) {
      const run = await api.post<EvalRun>(
        `/v1/admin/quality/eval-runs/${encodeURIComponent(runId)}/retry-failed`,
        {},
      );
      this.evalRuns = [run, ...this.evalRuns.filter((item) => item.id !== run.id)];
      return run;
    },
    async runAction<T>(operation: () => Promise<T>): Promise<T> {
      this.actionLoading = true;
      this.error = "";
      try {
        return await operation();
      } catch (error) {
        this.error = messageFrom(error, "质量管理操作失败");
        throw error;
      } finally {
        this.actionLoading = false;
      }
    },
  },
});
