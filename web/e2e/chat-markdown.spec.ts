import { expect, test, type Page } from "@playwright/test";

const answer = `明白了，您是在**同一个审批节点**中添加了两组审批人来源。

## 三种模式下的表现

| 模式 | 表现 | 是否符合您的预期 |
| --- | --- | --- |
| 会签（mode=1） | 员工1、员工2、提交人本人同时出现在当前审批节点，所有人必须审批通过 | 否，非顺序 |
| 或签（mode=2） | 三人同时出现，任意一人审批即可通过 | 否，非顺序 |
| 顺签（mode=3） | 先按第一组顺序审批，最后由提交人本人依次审批 | 是，严格按添加顺序 |

## 结论

- 只有 \`multipleMode = 3\` 时，页面才会按照添加顺序展示并依次流转。
- 会签和或签不存在先后顺序。`;

async function mockChat(page: Page): Promise<void> {
  await page.route("**/api/v1/knowledge/spaces", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "middle-platform",
          name: "中台",
          domains: [{ id: "approval-flow", name: "审批流", sort_order: 1 }],
        },
      ]),
    }),
  );
  await page.route("**/api/v1/agent/chat/stream", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: [
        'event: run.started\ndata: {"conversation_id":"markdown-e2e","run_id":"run-e2e"}\n\n',
        `event: run.completed\ndata: ${JSON.stringify({
          status: "completed",
          conversation_id: "markdown-e2e",
          run_id: "run-e2e",
          answer,
          last_agent: "Manager Agent",
          citations: [],
          tool_runs: [],
          approvals: [],
          trace_id: null,
        })}\n\n`,
      ].join(""),
    }),
  );
}

async function ask(page: Page): Promise<void> {
  await page.goto("/chat");
  const textarea = page.locator("textarea");
  await expect(textarea).toBeEnabled();
  await textarea.fill("同一个节点下两组审批人如何按顺序审批？");
  await textarea.press("Enter");
  await expect(page.locator(".message--assistant table")).toBeVisible();
}

test("desktop renders comparison tables without page overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockChat(page);
  await ask(page);

  await expect(page.locator(".message--assistant tbody tr")).toHaveCount(3);
  await expect(page.locator(".message--assistant h2")).toContainText([
    "三种模式下的表现",
    "结论",
  ]);
  const viewport = await page.evaluate(() => ({
    width: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(viewport.scrollWidth).toBeLessThanOrEqual(viewport.width);
  await page.screenshot({
    path: "output/playwright/chat-markdown-desktop.png",
    fullPage: true,
  });
});

test("mobile keeps wide tables inside an independent scroller", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockChat(page);
  await ask(page);

  const shell = page.locator(".message--assistant .markdown-table");
  const metrics = await shell.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    right: element.getBoundingClientRect().right,
  }));
  expect(metrics.scrollWidth).toBeGreaterThan(metrics.clientWidth);
  expect(metrics.right).toBeLessThanOrEqual(390);
  const viewport = await page.evaluate(() => ({
    width: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(viewport.scrollWidth).toBeLessThanOrEqual(viewport.width);
  await page.screenshot({
    path: "output/playwright/chat-markdown-mobile.png",
    fullPage: true,
  });
});
