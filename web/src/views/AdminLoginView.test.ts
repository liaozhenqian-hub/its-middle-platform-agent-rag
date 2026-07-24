import ElementPlus from "element-plus";
import { createPinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ post: vi.fn(), setCsrfToken: vi.fn() }));
vi.mock("@/api", () => ({
  api: { post: mocks.post },
  setCsrfToken: mocks.setCsrfToken,
}));

import AdminLoginView from "./AdminLoginView.vue";

describe("AdminLoginView", () => {
  it("submits credentials and routes to the admin workspace", async () => {
    mocks.post.mockResolvedValue({
      username: "admin",
      csrf_token: "csrf-1",
      expires_at: "2026-07-16T00:00:00Z",
    });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/chat", component: { template: "<div>Chat</div>" } },
        { path: "/admin/login", component: AdminLoginView },
        { path: "/admin", name: "admin", component: { template: "<div>Admin</div>" } },
      ],
    });
    await router.push("/admin/login");
    await router.isReady();
    const wrapper = mount(AdminLoginView, {
      global: { plugins: [createPinia(), ElementPlus, router] },
    });

    await wrapper.get('input[data-testid="username"]').setValue("admin");
    await wrapper.get('input[data-testid="password"]').setValue("correct");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(mocks.post).toHaveBeenCalledWith("/v1/admin/auth/login", {
      username: "admin",
      password: "correct",
    });
    expect(router.currentRoute.value.name).toBe("admin");
  });
});
