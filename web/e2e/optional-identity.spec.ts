import { expect, test } from "@playwright/test";

const anonymousIdentity = {
  owner_id: "anon:test",
  identity_kind: "anonymous",
  authenticated: false,
  display_name: "当前设备",
  csrf_token: null,
  scopes: ["agent:query", "memory:read"],
  merge_available: false,
};

const feishuIdentity = {
  owner_id: "ou_test",
  identity_kind: "feishu",
  authenticated: true,
  display_name: "测试用户",
  csrf_token: "csrf",
  scopes: ["agent:query", "memory:read"],
  merge_available: false,
};

test("anonymous chat remains usable with optional Feishu login", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({ json: anonymousIdentity }),
  );
  await page.route("**/api/v1/knowledge/spaces", (route) =>
    route.fulfill({
      json: [
        {
          id: "middle-platform",
          name: "中台",
          domains: [],
        },
      ],
    }),
  );
  await page.goto("/chat");

  await expect(page.getByText("当前设备")).toBeVisible();
  await expect(page.getByRole("button", { name: /飞书登录/ })).toBeVisible();
  await expect(page.getByRole("textbox")).toBeEnabled();
});

test("personal token account layout works on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({ json: feishuIdentity }),
  );
  await page.route("**/api/v1/account/tokens", (route) =>
    route.fulfill({
      json: [
        {
          id: "token-1",
          name: "办公电脑 Codex",
          display_prefix: "kpat_abcd123",
          scopes: ["agent:query", "memory:read"],
          created_at: "2026-07-23T00:00:00Z",
          last_used_at: null,
          revoked_at: null,
        },
      ],
    }),
  );
  await page.goto("/account");

  await expect(page.getByRole("heading", { name: "个人 Token" })).toBeVisible();
  await expect(page.getByText("办公电脑 Codex")).toBeVisible();
  await expect(page.getByRole("button", { name: /新建 Token/ })).toBeVisible();
  const bodyWidth = await page.locator("body").evaluate((element) => element.scrollWidth);
  expect(bodyWidth).toBeLessThanOrEqual(390);
});
