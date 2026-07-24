import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api";
import CitationPanel from "./CitationPanel.vue";


describe("CitationPanel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("loads and renders a bounded source excerpt on selection", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      source_type: "product_document",
      source_id: "doc-1",
      title: "业务功能配置",
      domain: "指标平台",
      excerpt: "这是命中的产品文档上下文。",
      language: null,
      truncated: false,
      metadata: { relative_path: "docs/metric.md", source_version: "v2" },
    });
    const wrapper = mount(CitationPanel, {
      props: {
        citation: {
          source_type: "product_document",
          source_id: "doc-1",
          title: "业务功能配置",
          domain: "指标平台",
          metadata: {},
        },
      },
      global: { stubs: { "el-icon": true } },
    });

    await flushPromises();

    expect(wrapper.text()).toContain("命中章节");
    expect(wrapper.text()).toContain("这是命中的产品文档上下文。");
    expect(wrapper.text()).toContain("docs/metric.md");
  });

  it("renders a document section and loads full text only on demand", async () => {
    const get = vi.spyOn(api, "get")
      .mockResolvedValueOnce({
        source_type: "product_document",
        source_id: "doc-full-1",
        title: "接入步骤",
        domain: "审批流",
        excerpt: "## 接入步骤\n\n**第一步**：申请权限。",
        language: "markdown",
        truncated: false,
        content_scope: "section",
        full_text_available: true,
        document_url: "/api/v1/citations/document?source_id=doc-full-1",
        metadata: { relative_path: "docs/approval.md" },
      })
      .mockResolvedValueOnce({
        source_type: "product_document",
        source_id: "doc-full-1",
        title: "接入步骤",
        domain: "审批流",
        excerpt: "# 审批流手册\n\n全文内容。",
        language: "markdown",
        truncated: false,
        content_scope: "full",
        full_text_available: true,
        document_url: "/api/v1/citations/document?source_id=doc-full-1",
        metadata: { relative_path: "docs/approval.md" },
      });
    const wrapper = mount(CitationPanel, {
      props: {
        citation: {
          source_type: "product_document",
          source_id: "doc-full-1",
          title: "接入步骤",
          domain: "审批流",
          metadata: {},
        },
      },
      global: { stubs: { "el-icon": true } },
    });
    await flushPromises();

    expect(wrapper.find(".citation-panel__document-body strong").text()).toBe("第一步");
    expect(wrapper.get("a[title='打开产品文档原文件']").attributes("href")).toBe(
      "/api/v1/citations/document?source_id=doc-full-1",
    );
    expect(get).toHaveBeenCalledTimes(1);

    await wrapper.get("button[title='查看完整产品文档']").trigger("click");
    await flushPromises();

    expect(get).toHaveBeenCalledWith(
      "/v1/citations/detail?source_type=product_document&source_id=doc-full-1&view=full",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(wrapper.text()).toContain("全文内容");
    expect(wrapper.text()).toContain("收起全文");
  });

  it("does not request raw detail for log citations", async () => {
    const get = vi.spyOn(api, "get");
    const wrapper = mount(CitationPanel, {
      props: {
        citation: {
          source_type: "log_trace",
          source_id: "trace-1",
          title: "test trace",
          domain: "中台",
          metadata: {
            environment: "test",
            log_count: 2,
            exception_types: ["NullPointerException"],
          },
        },
      },
      global: { stubs: { "el-icon": true } },
    });

    await flushPromises();

    expect(get).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("NullPointerException");
    expect(wrapper.text()).not.toContain("命中内容");
  });

  it("aborts stale detail requests when the selected citation changes", async () => {
    const pending: Array<{
      signal: AbortSignal;
      resolve: (value: unknown) => void;
    }> = [];
    vi.spyOn(api, "get").mockImplementation((_path, init) =>
      new Promise((resolve) => {
        pending.push({ signal: init?.signal as AbortSignal, resolve });
      }),
    );
    const citation = (source_id: string) => ({
      source_type: "product_document" as const,
      source_id,
      title: source_id,
      domain: "workflow",
      metadata: {},
    });
    const wrapper = mount(CitationPanel, {
      props: { citation: citation("doc-switch-1") },
      global: { stubs: { "el-icon": true } },
    });
    await flushPromises();

    await wrapper.setProps({ citation: citation("doc-switch-2") });
    await flushPromises();

    expect(pending[0].signal.aborted).toBe(true);
    pending[1].resolve({
      ...citation("doc-switch-2"),
      excerpt: "second excerpt",
      language: null,
      truncated: false,
    });
    await flushPromises();
    expect(wrapper.text()).toContain("second excerpt");
  });

  it("shows empty and error states without breaking the panel", async () => {
    const empty = mount(CitationPanel, {
      props: { citation: null },
      global: { stubs: { "el-icon": true } },
    });
    expect(empty.text()).toContain("选择回答中的引用查看详情");

    vi.spyOn(api, "get").mockRejectedValue(new Error("详情不可用"));
    const failed = mount(CitationPanel, {
      props: {
        citation: {
          source_type: "code",
          source_id: "code-error-1",
          title: "FailedCode",
          domain: "workflow",
          metadata: {},
        },
      },
      global: { stubs: { "el-icon": true } },
    });
    await flushPromises();

    expect(failed.text()).toContain("详情不可用");
  });

  it("copies only the bounded excerpt", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    vi.spyOn(api, "get").mockResolvedValue({
      source_type: "code",
      source_id: "code-copy-1",
      title: "OrderService.create",
      domain: "workflow",
      excerpt: "public void create() {}",
      language: "java",
      truncated: true,
      metadata: {},
    });
    const wrapper = mount(CitationPanel, {
      props: {
        citation: {
          source_type: "code",
          source_id: "code-copy-1",
          title: "OrderService.create",
          domain: "workflow",
          metadata: {},
        },
      },
      global: { stubs: { "el-icon": true } },
    });
    await flushPromises();

    await wrapper.get("button[title='复制命中内容']").trigger("click");

    expect(writeText).toHaveBeenCalledWith("public void create() {}");
    expect(wrapper.text()).toContain("已截取命中位置附近片段");
  });

  it("does not render source_id when citation title is missing", async () => {
    vi.spyOn(api, "get").mockRejectedValue(new Error("详情不可用"));
    const wrapper = mount(CitationPanel, {
      props: {
        citation: {
          source_type: "code",
          source_id: "code-889c460d7d7d4e46a824",
          title: "",
          domain: "审批流",
          metadata: { relative_path: "approval/TransferService.java" },
        },
      },
      global: { stubs: { "el-icon": true } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("代码：TransferService.java");
    expect(wrapper.text()).not.toContain("code-889c460d7d7d4e46a824");
  });
});
