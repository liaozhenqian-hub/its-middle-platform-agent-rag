import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { watch } from "vue";

const mocks = vi.hoisted(() => ({
  stream: vi.fn(),
  post: vi.fn(),
  get: vi.fn(),
}));

vi.mock("@/api/chat", () => ({
  streamChatEvents: mocks.stream,
}));

vi.mock("@/api", () => ({
  api: { post: mocks.post, get: mocks.get },
}));

import { useChatStore } from "./chat";

describe("chat scope", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    mocks.get.mockReset();
  });

  it("persists the active conversation as soon as a run starts", async () => {
    mocks.stream.mockImplementation(async function* () {
      yield { event: "run.started", data: { conversation_id: "c-persist", run_id: "r1" } };
      yield {
        event: "run.completed",
        data: {
          status: "completed", conversation_id: "c-persist", run_id: "r1",
          answer: "完成", last_agent: "Manager Agent", citations: [], tool_runs: [],
          approvals: [], trace_id: null,
        },
      };
    });
    const store = useChatStore();
    store.selectScope({ knowledgeSpaceId: "middle-platform", domainId: null, label: "中台" });

    await store.sendMessage("测试保存");

    expect(localStorage.getItem("middle-platform-agent.active-conversation")).toBe("c-persist");
  });

  it("restores the persisted conversation from the owned history endpoint", async () => {
    localStorage.setItem("middle-platform-agent.active-conversation", "c-history");
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/v1/knowledge/spaces") return [{
        id: "middle-platform", name: "中台",
        domains: [{ id: "approval-flow", name: "审批流", sort_order: 1 }],
      }];
      return {
        conversation_id: "c-history", title: "审批流接入", channel: "web",
        knowledge_space_id: "middle-platform", domain_id: "approval-flow",
        created_at: "2026-07-23T08:00:00+00:00", updated_at: "2026-07-23T08:01:00+00:00",
        messages: [{ id: 1, role: "user", content: "怎么对接", created_at: "2026-07-23T08:00:00+00:00" }],
      };
    });
    const store = useChatStore();

    await store.restorePersistedConversation();

    expect(mocks.get).toHaveBeenCalledWith("/v1/agent/conversations/c-history");
    expect(store.conversationId).toBe("c-history");
    expect(store.messages[0].content).toBe("怎么对接");
  });

  it("clears an inaccessible persisted conversation", async () => {
    localStorage.setItem("middle-platform-agent.active-conversation", "foreign");
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/v1/knowledge/spaces") return [{ id: "middle-platform", name: "中台", domains: [] }];
      throw new Error("Forbidden");
    });
    const store = useChatStore();

    await store.restorePersistedConversation();

    expect(localStorage.getItem("middle-platform-agent.active-conversation")).toBeNull();
    expect(store.conversationId).toBeNull();
  });

  it("resets conversation state only when the selected scope changes", () => {
    const store = useChatStore();
    store.selectScope({ knowledgeSpaceId: "middle-platform", domainId: null, label: "中台" });
    store.conversationId = "conversation-1";
    store.messages.push({ id: "m1", role: "assistant", content: "answer", citations: [] });

    store.selectScope({ knowledgeSpaceId: "middle-platform", domainId: null, label: "中台" });
    expect(store.conversationId).toBe("conversation-1");

    store.selectScope({
      knowledgeSpaceId: "middle-platform",
      domainId: "metric-platform",
      label: "指标平台",
    });
    expect(store.conversationId).toBeNull();
    expect(store.messages).toEqual([]);
  });

  it("restores a history transcript and its original knowledge scope", () => {
    const store = useChatStore();
    store.spaces = [{
      id: "middle-platform",
      name: "中台",
      domains: [{ id: "approval-flow", name: "审批流", sort_order: 1 }],
    }];

    store.restoreConversation({
      conversation_id: "c-history",
      title: "审批流接入",
      channel: "web",
      knowledge_space_id: "middle-platform",
      domain_id: "approval-flow",
      created_at: "2026-07-23T08:00:00+00:00",
      updated_at: "2026-07-23T08:01:00+00:00",
      messages: [
        { id: 1, role: "user", content: "怎么对接", created_at: "2026-07-23T08:00:00+00:00" },
        { id: 2, role: "assistant", content: "按文档接入", created_at: "2026-07-23T08:01:00+00:00" },
      ],
    });

    expect(store.conversationId).toBe("c-history");
    expect(store.scope?.domainId).toBe("approval-flow");
    expect(store.scope?.label).toBe("审批流");
    expect(store.messages.map((item) => item.content)).toEqual(["怎么对接", "按文档接入"]);
  });

  it("collects stream deltas, tools, and canonical completion", async () => {
    mocks.stream.mockImplementation(async function* () {
      yield { event: "run.started", data: { conversation_id: "c1", run_id: "r1" } };
      yield { event: "tool.started", data: { tool_call_id: "t1", tool_name: "search_domain_code" } };
      yield { event: "text.delta", data: { delta: "临时" } };
      yield { event: "tool.completed", data: { tool_call_id: "t1", tool_name: "search_domain_code" } };
      yield {
        event: "run.completed",
        data: {
          status: "completed",
          conversation_id: "c1",
          run_id: "r1",
          answer: "最终答案",
          last_agent: "指标平台专家",
          routed_domains: ["metric-platform"],
          specialists_used: ["指标平台专家"],
          citations: [
            {
              source_type: "code",
              source_id: "code-1",
              title: "MetricService",
              domain: "指标平台",
              metadata: {},
            },
          ],
          tool_runs: [],
          approvals: [],
          trace_id: null,
          quality_turn_id: "turn-1",
          feedback_token: "feedback-token",
        },
      };
    });
    const store = useChatStore();
    store.selectScope({
      knowledgeSpaceId: "middle-platform",
      domainId: "metric-platform",
      label: "指标平台",
    });

    await store.sendMessage("指标口径");

    expect(store.conversationId).toBe("c1");
    expect(store.messages.map((message) => message.content)).toEqual(["指标口径", "最终答案"]);
    expect(store.messages[1].citations[0].source_type).toBe("code");
    expect(store.activeTools[0].status).toBe("completed");
    expect(store.messages[1].qualityTurnId).toBe("turn-1");
    expect(store.messages[1].feedbackToken).toBe("feedback-token");
    expect(store.messages[1].agentName).toBe("指标平台专家");
    expect(store.streaming).toBe(false);
  });

  it("keeps the delegated specialist on each completed message", async () => {
    mocks.stream.mockImplementation(async function* () {
      yield { event: "run.started", data: { conversation_id: "c-route", run_id: "r-route" } };
      yield { event: "agent.updated", data: { agent: "Manager Agent" } };
      yield {
        event: "run.completed",
        data: {
          status: "completed",
          conversation_id: "c-route",
          run_id: "r-route",
          answer: "审批流回答",
          last_agent: "Manager Agent",
          routed_domains: ["approval-flow"],
          specialists_used: ["审批流专家"],
          citations: [],
          tool_runs: [{ tool_call_id: "a", tool_name: "approval_flow_expert", agent_name: "Manager Agent", status: "completed", arguments: {} }],
          approvals: [],
          trace_id: null,
        },
      };
    });
    const store = useChatStore();
    store.selectScope({ knowledgeSpaceId: "middle-platform", domainId: null, label: "中台" });

    await store.sendMessage("审批流转交接口更新了吗");

    expect(store.messages[1].agentName).toBe("审批流专家");
    expect(store.messages[1].routedDomains).toEqual(["approval-flow"]);
  });

  it("submits feedback for the selected assistant answer", async () => {
    mocks.post.mockResolvedValue(undefined);
    const store = useChatStore();
    store.messages.push({
      id: "assistant-1",
      role: "assistant",
      content: "回答",
      citations: [],
      qualityTurnId: "turn-1",
      feedbackToken: "feedback-token",
    });

    await store.submitFeedback("assistant-1", "negative", "引用错误");

    expect(mocks.post).toHaveBeenCalledWith("/v1/quality/turns/turn-1/feedback", {
      feedback_token: "feedback-token",
      rating: "negative",
      reason: "引用错误",
      reason_code: "",
    });
    expect(store.messages[0].feedbackRating).toBe("negative");
  });

  it("stops consuming once the terminal event arrives", async () => {
    mocks.stream.mockImplementation(async function* () {
      yield { event: "run.started", data: { conversation_id: "c2", run_id: "r2" } };
      yield {
        event: "run.completed",
        data: {
          status: "completed",
          conversation_id: "c2",
          run_id: "r2",
          answer: "最终答案",
          last_agent: "Manager Agent",
          citations: [],
          tool_runs: [],
          approvals: [],
          trace_id: null,
        },
      };
      yield { event: "text.delta", data: { delta: "迟到内容" } };
    });

    const store = useChatStore();
    store.selectScope({
      knowledgeSpaceId: "middle-platform",
      domainId: null,
      label: "中台",
    });

    await store.sendMessage("测试结束事件");

    expect(store.messages[1].content).toBe("最终答案");
    expect(store.streaming).toBe(false);
  });

  it("publishes every text delta through the reactive message proxy", async () => {
    mocks.stream.mockImplementation(async function* () {
      yield { event: "run.started", data: { conversation_id: "c3", run_id: "r3" } };
      yield { event: "text.delta", data: { delta: "逐" } };
      yield { event: "text.delta", data: { delta: "字" } };
      yield {
        event: "run.completed",
        data: {
          status: "completed",
          conversation_id: "c3",
          run_id: "r3",
          answer: "逐字",
          last_agent: "Manager Agent",
          citations: [],
          tool_runs: [],
          approvals: [],
          trace_id: null,
        },
      };
    });
    const store = useChatStore();
    store.selectScope({ knowledgeSpaceId: "middle-platform", domainId: null, label: "中台" });
    const observed: string[] = [];
    const stop = watch(
      () => store.messages[1]?.content,
      (value) => {
        if (value !== undefined) observed.push(value);
      },
      { flush: "sync" },
    );

    await store.sendMessage("流式测试");
    stop();

    expect(observed).toContain("逐");
    expect(observed).toContain("逐字");
  });

  it("replaces browser network errors with an actionable Chinese message", async () => {
    mocks.stream.mockImplementation(async function* () {
      throw new TypeError("network error");
    });
    const store = useChatStore();
    store.selectScope({ knowledgeSpaceId: "middle-platform", domainId: null, label: "中台" });

    await store.sendMessage("审批实例详情 operationSource 枚举值有哪些");

    expect(store.error).toBe("网络连接已中断，请稍后重试。");
    expect(store.messages[1].content).toBe("网络连接已中断，请稍后重试。");
    expect(store.error).not.toContain("network error");
  });
});
