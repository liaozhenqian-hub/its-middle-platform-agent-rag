import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  delete: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

vi.mock("@/api", () => ({
  api: { delete: mocks.delete, get: mocks.get, post: mocks.post, put: mocks.put },
}));

import { useQualityStore } from "./quality";

describe("quality store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("loads filtered turns and promotes a turn into the evaluation set", async () => {
    mocks.get.mockResolvedValueOnce({
      items: [{ id: "turn-1", question: "审批流超时", status: "completed", feedback: [] }],
      page: 1,
      page_size: 20,
      total: 1,
    });
    mocks.post.mockResolvedValueOnce({ id: "case-1", name: "审批流超时" });
    const store = useQualityStore();
    store.filters.channel = "feishu";
    store.filters.rating = "negative";
    store.filters.query = "审批流";

    await store.loadTurns();
    await store.promoteTurn("turn-1", {
      name: "审批流超时",
      required_tools: ["bug_diagnosis_expert"],
      required_citation_types: ["log_trace"],
      required_facts: ["超时"],
      forbidden_facts: [],
      tags: ["bug"],
      enabled: true,
    });

    expect(mocks.get).toHaveBeenCalledWith(
      "/v1/admin/quality/turns?page=1&page_size=20&channel=feishu&rating=negative&query=%E5%AE%A1%E6%89%B9%E6%B5%81",
    );
    expect(mocks.post).toHaveBeenCalledWith(
      "/v1/admin/quality/turns/turn-1/eval-case",
      expect.objectContaining({ name: "审批流超时" }),
    );
    expect(store.evalCases[0].id).toBe("case-1");
  });

  it("deletes a turn and runs selected evaluation cases", async () => {
    const store = useQualityStore();
    store.turns = [{ id: "turn-1", question: "问题" } as never];
    mocks.delete.mockResolvedValue(undefined);
    mocks.post.mockResolvedValue({ id: "run-1", status: "completed" });

    await store.deleteTurn("turn-1");
    await store.runEvaluation(["case-1"]);

    expect(store.turns).toEqual([]);
    expect(mocks.post).toHaveBeenCalledWith("/v1/admin/quality/eval-runs", {
      case_ids: ["case-1"],
    });
    expect(store.evalRuns[0].id).toBe("run-1");
  });

  it("loads only enabled evaluation cases by default", async () => {
    mocks.get.mockResolvedValueOnce([]);
    const store = useQualityStore();

    await store.loadEvalCases();

    expect(mocks.get).toHaveBeenCalledWith(
      "/v1/admin/quality/eval-cases?enabled=true",
    );
  });

  it("updates approval with only fields accepted by the backend", async () => {
    const store = useQualityStore();
    const item = {
      id: "case-1",
      source_turn_id: "turn-1",
      name: "审批管理员字段",
      question: "哪个字段判断审批管理员？",
      knowledge_space_id: "middle-platform",
      domain_id: "approval-flow",
      required_tools: ["collect_domain_evidence"],
      required_citation_types: ["code"],
      required_facts: ["字段名"],
      forbidden_facts: ["已部署"],
      tags: ["approval"],
      enabled: true,
      expected_behavior: "answer",
      max_latency_ms: 30_000,
      max_tool_calls: 3,
      max_citations: 8,
      turns: [],
      task_type: "code_lookup",
      suite: "real-business",
      priority: "critical",
      approval_state: "candidate",
      version: 2,
      created_at: "2026-07-22T00:00:00Z",
      updated_at: "2026-07-22T00:00:00Z",
    } as const;
    mocks.put.mockResolvedValueOnce({ ...item, approval_state: "approved", version: 3 });
    store.evalCases = [item as never];

    await store.setEvalApproval(item as never, "approved");

    expect(mocks.put).toHaveBeenCalledWith(
      "/v1/admin/quality/eval-cases/case-1",
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
        approval_state: "approved",
      },
    );
    expect(store.evalCases[0].approval_state).toBe("approved");
  });
});
