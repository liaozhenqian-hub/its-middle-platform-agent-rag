import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}));
vi.mock("@/api", () => ({ api: mocks }));

import { useUserIdentityStore } from "./userIdentity";

describe("user identity store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads optional anonymous or Feishu identity", async () => {
    mocks.get.mockResolvedValueOnce({
      owner_id: "anon:1",
      identity_kind: "anonymous",
      authenticated: false,
      display_name: "当前设备",
      csrf_token: null,
      scopes: ["agent:query", "memory:read"],
      merge_available: false,
    });
    const store = useUserIdentityStore();

    await store.load();

    expect(mocks.get).toHaveBeenCalledWith("/v1/auth/me");
    expect(store.identity?.display_name).toBe("当前设备");
    expect(store.authenticated).toBe(false);
  });

  it("uses the canonical backend URL for Feishu login", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { assign } });
    mocks.get.mockResolvedValueOnce({
      owner_id: "anon:1",
      identity_kind: "anonymous",
      authenticated: false,
      display_name: "Current device",
      csrf_token: null,
      scopes: ["agent:query", "memory:read"],
      merge_available: false,
      feishu_login_available: true,
      feishu_login_url:
        "http://172.18.26.1:8000/api/v1/auth/feishu/start",
    });
    const store = useUserIdentityStore();

    await store.load();
    store.login();

    expect(assign).toHaveBeenCalledWith(
      "http://172.18.26.1:8000/api/v1/auth/feishu/start",
    );
  });

  it("uses separate user CSRF for merge and token management", async () => {
    mocks.get
      .mockResolvedValueOnce({
        owner_id: "ou_user",
        identity_kind: "feishu",
        authenticated: true,
        display_name: "张三",
        csrf_token: "user-csrf",
        scopes: ["agent:query", "memory:read"],
        merge_available: true,
      })
      .mockResolvedValueOnce({ available: true, memories: 2, conversations: 1 });
    mocks.post
      .mockResolvedValueOnce({ status: "completed" })
      .mockResolvedValueOnce({
        token: "kpat_show_once",
        item: { id: "token-1", name: "Codex", scopes: ["agent:query"] },
      });
    const store = useUserIdentityStore();
    await store.load();
    await store.loadMergePreview();
    await store.mergeAnonymous(true);
    await store.createToken("Codex", ["agent:query"]);

    const expectedHeaders = { "X-User-CSRF-Token": "user-csrf" };
    expect(mocks.post).toHaveBeenNthCalledWith(
      1,
      "/v1/auth/merge-anonymous",
      { confirm: true },
      { headers: expectedHeaders },
    );
    expect(mocks.post).toHaveBeenNthCalledWith(
      2,
      "/v1/account/tokens",
      { name: "Codex", scopes: ["agent:query"] },
      { headers: expectedHeaders },
    );
    expect(store.createdToken).toBe("kpat_show_once");
  });
});
