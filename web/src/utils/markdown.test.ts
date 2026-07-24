import { describe, expect, it } from "vitest";

import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("renders common answer blocks while escaping raw HTML", () => {
    const html = renderMarkdown(
      "## 指标口径\n\n- **收入**\n- `metric_id`\n\n```java\nreturn 1;\n```\n\n<script>alert(1)</script>",
    );

    expect(html).toContain("<h2>指标口径</h2>");
    expect(html).toContain("<li><strong>收入</strong></li>");
    expect(html).toContain("<li><code>metric_id</code></li>");
    expect(html).toContain('<pre><code class="language-java">return 1;');
    expect(html).not.toContain("alert(1)");
    expect(html).not.toContain("<script>");
  });

  it("keeps streaming partial text readable", () => {
    expect(renderMarkdown("正在查询指标")).toContain("<p>正在查询指标</p>");
  });

  it("renders GFM tables in a scrollable shell and sanitizes unsafe links", () => {
    const html = renderMarkdown(
      [
        "| 模式 | 表现 | 是否符合预期 |",
        "| --- | --- | --- |",
        "| 会签 | 所有人同时审批 | 否 |",
        "| 顺签 | 按添加顺序审批 | 是 |",
        "",
        "[不安全链接](javascript:alert(1))",
      ].join("\n"),
    );

    expect(html).toContain('<div class="markdown-table"><table>');
    expect(html).toContain("<th>模式</th>");
    expect(html).toContain("<td>顺签</td>");
    expect(html).not.toContain('href="javascript:');
  });
});
