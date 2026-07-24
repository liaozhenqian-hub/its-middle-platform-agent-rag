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
  await page.route("**/api/v1/admin/sources", (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route("**/api/v1/admin/jobs**", (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route("**/api/v1/admin/quality/turns**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }),
  }));
  await page.route("**/api/v1/admin/quality/analytics**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      total_turns: 79, completed_turns: 76, citation_coverage: 0.82,
      average_tool_calls: 3.2, feedback_rate: 0.08,
      p50_duration_ms: 9700, p90_duration_ms: 27800,
      issue_counts: { zero_citation: 6, duplicate_tool: 2 },
    }),
  }));
  await page.route("**/api/v1/admin/quality/annotations**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ items: [], page: 1, page_size: 100, total: 0 }),
  }));
  await page.route("**/api/v1/admin/quality/eval-cases**", (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route("**/api/v1/admin/quality/eval-runs**", (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
}


for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`quality analytics remain readable at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockAdmin(page);
    await page.goto("/admin");
    await page.getByRole("tab", { name: "问答质量" }).click();
    await expect(page.getByText("引用覆盖率")).toBeVisible();
    await expect(page.getByText("82.0%")).toBeVisible();
    const layout = await page.evaluate(() => ({
      width: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width);
    await page.screenshot({
      path: `output/playwright/quality-admin-${viewport.width}.png`,
      fullPage: true,
    });
  });
}
