import { describe, expect, it, vi } from "vitest";

import { createAdminGuard } from "./guard";

describe("admin route guard", () => {
  it("restores once and redirects unauthenticated users to login", async () => {
    const auth = {
      initialized: false,
      authenticated: false,
      restore: vi.fn().mockResolvedValue(false),
    };
    const guard = createAdminGuard(auth);

    const result = await guard({ fullPath: "/admin", meta: { requiresAdmin: true } });

    expect(auth.restore).toHaveBeenCalledOnce();
    expect(result).toEqual({ name: "admin-login", query: { redirect: "/admin" } });
  });

  it("allows public and authenticated routes", async () => {
    const auth = { initialized: true, authenticated: true, restore: vi.fn() };
    const guard = createAdminGuard(auth);

    expect(await guard({ fullPath: "/chat", meta: {} })).toBe(true);
    expect(await guard({ fullPath: "/admin", meta: { requiresAdmin: true } })).toBe(true);
  });
});
