import ElementPlus from "element-plus";
import { createPinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/api", () => ({
  api: { get: mocks.get },
  setCsrfToken: vi.fn(),
}));

import ChatView from "./ChatView.vue";

describe("ChatView", () => {
  beforeEach(() => {
    localStorage.clear();
    mocks.get.mockReset();
  });

  it("loads and exposes the middle-platform scope choices", async () => {
    mocks.get.mockImplementation(async (path: string) => {
      if (path.startsWith("/v1/agent/conversations?")) return {
        items: [], total: 0, page: 1, page_size: 20,
      };
      return [{
        id: "middle-platform",
        name: "中台",
        domains: [
          { id: "metric-platform", name: "指标平台", sort_order: 10 },
          { id: "approval-flow", name: "审批流", sort_order: 20 },
        ],
      }];
    });
    const wrapper = mount(ChatView, {
      global: {
        plugins: [createPinia(), ElementPlus],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });

    await flushPromises();

    expect(wrapper.text()).toContain("中台知识工作台");
    expect(wrapper.text()).toContain("中台");
    expect(wrapper.text()).toContain("指标平台");
    expect(wrapper.text()).toContain("审批流");
    expect(wrapper.findComponent({ name: "ElDrawer" }).props("size")).toBe("88%");
  });

  it("shows collapsible recent conversations at the bottom-left instead of the header", async () => {
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/v1/knowledge/spaces") return [{
        id: "middle-platform", name: "中台",
        domains: [{ id: "approval-flow", name: "审批流", sort_order: 1 }],
      }];
      if (path.startsWith("/v1/agent/conversations?")) return {
        items: [{
          conversation_id: "c-1", title: "管理员转办接口", preview: "接口入参",
          channel: "web", message_count: 2,
          created_at: "2026-07-23T08:00:00+00:00", updated_at: "2026-07-23T08:01:00+00:00",
        }],
        total: 1, page: 1, page_size: 20,
      };
      throw new Error(`unexpected path ${path}`);
    });
    const wrapper = mount(ChatView, {
      global: {
        plugins: [createPinia(), ElementPlus],
        stubs: {
          RouterLink: {
            props: ["to"],
            template: '<a :href="to"><slot /></a>',
          },
        },
      },
    });

    await flushPromises();

    expect(wrapper.find('header nav a[href="/history"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("历史会话");
    expect(wrapper.text()).toContain("管理员转办接口");
    expect(wrapper.text()).toContain("查看全部");
    expect(wrapper.find(".recent-history__toggle").attributes("aria-expanded")).toBe("true");
  });
});
