import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));
vi.mock("@/api", () => ({ api: mocks }));

import { useHistoryStore } from "./history";
import { useUserIdentityStore } from "./userIdentity";

describe("conversation history store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    useUserIdentityStore().identity = {
      owner_id: "ou_user",
      identity_kind: "feishu",
      authenticated: true,
      display_name: "测试用户",
      csrf_token: "user-csrf",
      scopes: ["agent:query", "memory:read"],
      merge_available: false,
      feishu_login_available: true,
      feishu_login_url: "/api/v1/auth/feishu/start",
    };
  });

  it("loads, opens, renames and removes the current identity's conversations", async () => {
    mocks.get
      .mockResolvedValueOnce({
        items: [{ conversation_id: "c-1", title: "审批流问题" }],
        total: 1,
        page: 1,
        page_size: 20,
      })
      .mockResolvedValueOnce({
        conversation_id: "c-1",
        title: "审批流问题",
        knowledge_space_id: "middle-platform",
        domain_id: "approval-flow",
        messages: [{ id: 1, role: "user", content: "如何对接" }],
      });
    mocks.patch.mockResolvedValueOnce({
      conversation_id: "c-1",
      title: "审批流接入",
    });
    mocks.delete.mockResolvedValueOnce(undefined);
    const store = useHistoryStore();

    await store.load("审批流");
    await store.open("c-1");
    await store.rename("c-1", "审批流接入");
    await store.remove("c-1");

    expect(mocks.get).toHaveBeenNthCalledWith(
      1,
      "/v1/agent/conversations?page=1&page_size=20&query=%E5%AE%A1%E6%89%B9%E6%B5%81",
    );
    expect(mocks.get).toHaveBeenNthCalledWith(2, "/v1/agent/conversations/c-1");
    expect(mocks.patch).toHaveBeenCalledWith(
      "/v1/agent/conversations/c-1",
      { title: "审批流接入" },
      { headers: { "X-User-CSRF-Token": "user-csrf" } },
    );
    expect(mocks.delete).toHaveBeenCalledWith("/v1/agent/conversations/c-1");
    expect(store.items).toEqual([]);
    expect(store.selected).toBeNull();
  });
});
