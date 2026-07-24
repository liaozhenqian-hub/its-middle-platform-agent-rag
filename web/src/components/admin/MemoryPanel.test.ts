import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api", () => ({ api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

import { useMemoryStore } from "@/stores/memory";
import MemoryPanel from "./MemoryPanel.vue";

describe("MemoryPanel", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("reviews domain memory without exposing personal owner details", () => {
    const store = useMemoryStore();
    store.candidates = [{
      id: "domain-1", scope_type: "domain", owner_id: "approval-flow",
      domain_id: "approval-flow", memory_type: "procedural_memory",
      subject: "审批流排障", summary: "领域排障流程", confidence: 0.9,
    }] as never;
    store.personalStatistics = {
      candidate: { user_preference: 2 }, confirmed: {}, rejected: {}, deleted: {},
    } as never;
    const wrapper = mount(MemoryPanel, { global: { plugins: [ElementPlus] } });

    expect(wrapper.text()).toContain("领域记忆审核");
    expect(wrapper.text()).toContain("个人候选 2");
    expect(wrapper.text()).not.toContain("anon:");
  });
});
