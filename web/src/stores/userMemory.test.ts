import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ delete: vi.fn(), get: vi.fn(), post: vi.fn() }));
vi.mock("@/api", () => ({ api: mocks }));

import { useUserMemoryStore } from "./userMemory";
import { useUserIdentityStore } from "./userIdentity";

describe("user memory store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("loads and forgets only the current user's memories", async () => {
    mocks.get.mockResolvedValueOnce([{ id: "memory-1", summary: "偏好简洁回答" }]);
    mocks.delete.mockResolvedValueOnce(undefined);
    const store = useUserMemoryStore();

    await store.load();
    await store.forget("memory-1");

    expect(mocks.get).toHaveBeenCalledWith("/v1/memory");
    expect(mocks.delete).toHaveBeenCalledWith("/v1/memory/memory-1");
    expect(store.memories).toEqual([]);
  });

  it("loads and confirms the current Feishu user's pending memories", async () => {
    useUserIdentityStore().identity = {
      owner_id: "ou_user",
      identity_kind: "feishu",
      authenticated: true,
      display_name: "测试用户",
      csrf_token: "user-csrf",
      scopes: ["memory:read"],
      merge_available: false,
      feishu_login_available: true,
      feishu_login_url: "/api/v1/auth/feishu/start",
    };
    mocks.get
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: "candidate-1", summary: "偏好完整接口契约" }]);
    mocks.post.mockResolvedValueOnce({
      id: "candidate-1",
      status: "confirmed",
      summary: "偏好完整接口契约",
    });
    const store = useUserMemoryStore();

    await store.load();
    await store.confirm("candidate-1");

    expect(mocks.get).toHaveBeenNthCalledWith(2, "/v1/memory/candidates");
    expect(mocks.post).toHaveBeenCalledWith(
      "/v1/memory/candidates/candidate-1/confirm",
      {},
      { headers: { "X-User-CSRF-Token": "user-csrf" } },
    );
    expect(store.candidates).toEqual([]);
    expect(store.memories[0].id).toBe("candidate-1");
  });

  it("separates procedural memories from personal facts", () => {
    const store = useUserMemoryStore();
    store.candidates = [
      { id: "procedure-1", memory_type: "procedural_memory" },
      { id: "event-1", memory_type: "episodic_memory" },
    ] as never;
    store.memories = [
      { id: "procedure-2", memory_type: "procedural_memory" },
      { id: "preference-1", memory_type: "user_preference" },
    ] as never;

    expect(store.pendingProcedures.map((item) => item.id)).toEqual(["procedure-1"]);
    expect(store.pendingFacts.map((item) => item.id)).toEqual(["event-1"]);
    expect(store.confirmedProcedures.map((item) => item.id)).toEqual(["procedure-2"]);
    expect(store.confirmedFacts.map((item) => item.id)).toEqual(["preference-1"]);
  });

  it("separates auto-confirm candidates and explicitly reviewed candidates", () => {
    const store = useUserMemoryStore();
    store.candidates = [
      { id: "preference", memory_type: "user_preference" },
      { id: "context", memory_type: "user_context" },
      { id: "decision", memory_type: "decision_memory" },
      { id: "episode", memory_type: "episodic_memory" },
      { id: "procedure", memory_type: "procedural_memory" },
    ] as never;

    expect(store.autoConfirmCandidates.map((item) => item.id)).toEqual(["preference", "context"]);
    expect(store.explicitReviewCandidates.map((item) => item.id)).toEqual([
      "decision", "episode", "procedure",
    ]);
  });

  it("allows the current user to reject a pending memory", async () => {
    useUserIdentityStore().identity = {
      owner_id: "ou_user", identity_kind: "feishu", authenticated: true,
      display_name: "测试用户", csrf_token: "user-csrf", scopes: ["memory:read"],
      merge_available: false, feishu_login_available: true,
      feishu_login_url: "/api/v1/auth/feishu/start",
    };
    mocks.post.mockResolvedValueOnce({ id: "candidate-1", status: "rejected" });
    const store = useUserMemoryStore();
    store.candidates = [{ id: "candidate-1" }] as never;

    await store.reject("candidate-1");

    expect(mocks.post).toHaveBeenCalledWith(
      "/v1/memory/candidates/candidate-1/reject", {},
      { headers: { "X-User-CSRF-Token": "user-csrf" } },
    );
    expect(store.candidates).toEqual([]);
  });
});
