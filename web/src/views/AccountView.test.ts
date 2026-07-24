import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock("@/api", () => ({ api: mocks }));

import AccountView from "./AccountView.vue";

describe("AccountView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("shows named personal tokens without exposing their secret", async () => {
    mocks.get
      .mockResolvedValueOnce({
        owner_id: "ou_user",
        identity_kind: "feishu",
        authenticated: true,
        display_name: "张三",
        csrf_token: "csrf",
        scopes: ["agent:query", "memory:read"],
        merge_available: false,
      })
      .mockResolvedValueOnce([
        {
          id: "token-1",
          name: "Codex laptop",
          display_prefix: "kpat_abcd123",
          scopes: ["agent:query", "memory:read"],
          created_at: "2026-07-23T00:00:00Z",
          last_used_at: null,
          revoked_at: null,
        },
      ]);
    const wrapper = mount(AccountView, {
      global: {
        plugins: [ElementPlus],
        stubs: { "router-link": { template: "<a><slot /></a>" } },
      },
    });

    await vi.waitFor(() => expect(wrapper.text()).toContain("Codex laptop"));

    expect(wrapper.text()).toContain("个人 Token");
    expect(wrapper.text()).toContain("新建 Token");
    expect(wrapper.text()).toContain("kpat_abcd123");
    expect(wrapper.text()).not.toContain("完整 Token 会显示在这里");
  });
});
