import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import QualityTable from "./QualityTable.vue";

describe("QualityTable", () => {
  it("renders question, channel, status, and feedback summary", () => {
    const wrapper = mount(QualityTable, {
      props: {
        turns: [
          {
            id: "turn-1",
            question: "审批流为什么超时",
            answer: "回答",
            channel: "feishu",
            status: "completed",
            domain_id: "approval-flow",
            user_name: "张三",
            created_at: "2026-07-17T00:00:00Z",
            feedback: [{ rating: "negative" }],
          },
        ] as never,
        loading: false,
      },
      global: {
        stubs: {
          ElEmpty: true,
          ElTag: { template: "<span><slot /></span>" },
          ElTooltip: { template: "<span><slot /></span>" },
          ElButton: true,
        },
        directives: { loading: () => undefined },
      },
    });

    expect(wrapper.text()).toContain("审批流为什么超时");
    expect(wrapper.text()).toContain("审批流");
  });
});
