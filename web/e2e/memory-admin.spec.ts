import { expect, test, type Page } from "@playwright/test";


async function mockAdmin(page: Page): Promise<void> {
  await page.route("**/api/v1/admin/auth/me", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ username: "admin", csrf_token: "csrf", expires_at: "2099-01-01" }),
  }));
  await page.route("**/api/v1/knowledge/spaces", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify([{ id: "middle-platform", name: "中台", domains: [] }]),
  }));
  for (const pattern of [
    "**/api/v1/admin/sources",
    "**/api/v1/admin/jobs**",
    "**/api/v1/admin/quality/eval-cases**",
    "**/api/v1/admin/quality/eval-runs**",
  ]) {
    await page.route(pattern, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  }
  await page.route("**/api/v1/admin/quality/turns**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }),
  }));
  await page.route("**/api/v1/admin/quality/analytics**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ total_turns: 0, completed_turns: 0, citation_coverage: 0, average_tool_calls: 0, feedback_rate: 0, p50_duration_ms: null, p90_duration_ms: null, issue_counts: {} }),
  }));
  await page.route("**/api/v1/admin/quality/annotations**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ items: [], page: 1, page_size: 100, total: 0 }),
  }));
  await page.route("**/api/v1/admin/memory/candidates**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify([{
      id: "candidate-1", scope_type: "user", owner_id: "ou_123",
      memory_type: "user_preference", subject: "answer-format",
      summary: "接口回答需要包含入参与出参", source_turn_id: "turn-1",
      confidence: 0.92, status: "candidate",
    }]),
  }));
  await page.route("**/api/v1/admin/memory?**", (route) => route.fulfill({
    contentType: "application/json",
    body: "[]",
  }));
}


for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`memory review remains usable at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockAdmin(page);
    await page.goto("/admin");
    await page.getByRole("tab", { name: "长期记忆" }).click();
    await expect(page.getByText("模型只能生成候选项；审核通过前不会影响任何回答。")).toBeVisible();
    await expect(page.getByText("接口回答需要包含入参与出参")).toBeVisible();
    const layout = await page.evaluate(() => ({
      width: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width);
  });
}
