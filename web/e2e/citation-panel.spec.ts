import { expect, test, type Page } from "@playwright/test";

const citation = {
  source_type: "code",
  source_id: "code-e2e-1",
  title: "OrderService.create",
  domain: "workflow",
  metadata: { branch: "develop" },
};
const longCode = Array.from(
  { length: 180 },
  (_, index) => `${String(index + 1).padStart(3, "0")}: public void step${index + 1}() {}`,
).join("\n");

async function mockChat(page: Page): Promise<void> {
  await page.route("**/api/v1/knowledge/spaces", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "middle-platform",
          name: "中台",
          domains: [{ id: "workflow", name: "工作流", sort_order: 1 }],
        },
      ]),
    }),
  );
  await page.route("**/api/v1/agent/chat/stream", (route) => {
    const completed = {
      status: "completed",
      conversation_id: "conversation-e2e",
      run_id: "run-e2e",
      answer: "已定位到相关代码。",
      last_agent: "Manager Agent",
      citations: [citation],
      tool_runs: [],
      approvals: [],
      trace_id: null,
    };
    route.fulfill({
      contentType: "text/event-stream",
      body: [
        'event: run.started\ndata: {"conversation_id":"conversation-e2e","run_id":"run-e2e"}\n\n',
        `event: run.completed\ndata: ${JSON.stringify(completed)}\n\n`,
      ].join(""),
    });
  });
  await page.route("**/api/v1/citations/detail?**", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ...citation,
        excerpt: longCode,
        language: "java",
        truncated: true,
        metadata: {
          branch: "develop",
          relative_path: "service/OrderService.java",
          start_line: 120,
          end_line: 180,
          gitlab_url: "https://gitlab.example/project/-/blob/commit/service/OrderService.java#L120",
        },
      }),
    }),
  );
}

async function askAndOpenCitation(page: Page): Promise<void> {
  await page.goto("/chat");
  const textarea = page.locator("textarea");
  await expect(textarea).toBeEnabled();
  await textarea.fill("测试环境 trace ID trace-e2e-123456");
  await textarea.press("Enter");
  const citationButton = page.locator(".message__citations button");
  await expect(citationButton).toBeVisible();
  await citationButton.click();
}

test("desktop citation sidebar loads bounded code in an independent scroller", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockChat(page);
  await askAndOpenCitation(page);

  const panel = page.locator(".desktop-citations");
  const excerpt = panel.locator(".citation-panel__excerpt");
  await expect(excerpt).toContainText("public void step180");
  const metrics = await excerpt.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
  const box = await panel.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x + box!.width).toBeLessThanOrEqual(1440);
  expect(box!.y + box!.height).toBeLessThanOrEqual(900);
});

test("mobile citation drawer keeps long code inside the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockChat(page);
  await askAndOpenCitation(page);

  const drawer = page.locator(".el-drawer");
  await expect(drawer).toBeVisible();
  const excerpt = drawer.locator(".citation-panel__excerpt");
  await expect(excerpt).toContainText("public void step180");
  const metrics = await excerpt.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
  const box = await drawer.boundingBox();
  expect(box).not.toBeNull();
  await expect.poll(async () => {
    const settled = await drawer.boundingBox();
    return settled ? settled.x + settled.width : Number.POSITIVE_INFINITY;
  }).toBeLessThanOrEqual(390);
  const settled = await drawer.boundingBox();
  expect(settled).not.toBeNull();
  expect(settled!.x).toBeGreaterThanOrEqual(0);
  expect(settled!.y + settled!.height).toBeLessThanOrEqual(844);
});
