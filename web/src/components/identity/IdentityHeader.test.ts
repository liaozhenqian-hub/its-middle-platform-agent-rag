import { createPinia, setActivePinia } from "pinia";
import ElementPlus from "element-plus";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock("@/api", () => ({ api: mocks }));

import IdentityHeader from "./IdentityHeader.vue";

describe("IdentityHeader", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("keeps anonymous chat usable and offers optional Feishu login", async () => {
    mocks.get.mockResolvedValueOnce({
      owner_id: "anon:1",
      identity_kind: "anonymous",
      authenticated: false,
      display_name: "当前设备",
      csrf_token: null,
      scopes: ["agent:query"],
      merge_available: false,
      feishu_login_available: false,
    });
    const wrapper = mount(IdentityHeader, {
      global: {
        plugins: [ElementPlus],
        stubs: { "router-link": { template: "<a><slot /></a>" } },
      },
    });

    await vi.waitFor(() => expect(wrapper.text()).toContain("当前设备"));

    expect(wrapper.text()).toContain("飞书登录未配置");
    expect(wrapper.text()).not.toContain("必须登录");
  });

  it("shows Feishu identity and merge confirmation actions", async () => {
    mocks.get
      .mockResolvedValueOnce({
        owner_id: "ou_user",
        identity_kind: "feishu",
        authenticated: true,
        display_name: "张三",
        csrf_token: "csrf",
        scopes: ["agent:query"],
        merge_available: true,
        feishu_login_available: true,
      })
      .mockResolvedValueOnce({ available: true, memories: 2, conversations: 1 });
    const wrapper = mount(IdentityHeader, {
      global: {
        plugins: [ElementPlus],
        stubs: { "router-link": { template: "<a><slot /></a>" } },
      },
    });

    await vi.waitFor(() => expect(wrapper.text()).toContain("张三"));

    expect(wrapper.text()).toContain("合并当前设备数据");
    expect(wrapper.text()).toContain("稍后处理");
  });
});
