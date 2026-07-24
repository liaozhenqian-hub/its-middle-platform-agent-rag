import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  setCsrfToken: vi.fn(),
}));

vi.mock("@/api", () => ({
  api: { get: mocks.get, post: mocks.post },
  setCsrfToken: mocks.setCsrfToken,
}));

import { ApiError } from "@/api/client";
import { useAuthStore } from "./auth";

describe("auth store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("logs in and makes the returned CSRF token available to writes", async () => {
    mocks.post.mockResolvedValue({
      username: "admin",
      csrf_token: "csrf-1",
      expires_at: "2026-07-16T00:00:00Z",
    });
    const store = useAuthStore();

    await store.login("admin", "correct");

    expect(mocks.post).toHaveBeenCalledWith("/v1/admin/auth/login", {
      username: "admin",
      password: "correct",
    });
    expect(mocks.setCsrfToken).toHaveBeenCalledWith("csrf-1");
    expect(store.authenticated).toBe(true);
  });

  it("treats a missing cookie as a completed unauthenticated restore", async () => {
    mocks.get.mockRejectedValue(new ApiError(401, "expired"));
    const store = useAuthStore();

    await expect(store.restore()).resolves.toBe(false);

    expect(store.initialized).toBe(true);
    expect(store.error).toBe("");
  });

  it("logs out through the protected endpoint and clears identity", async () => {
    mocks.post.mockResolvedValue(undefined);
    const store = useAuthStore();
    store.identity = {
      username: "admin",
      csrf_token: "csrf-1",
      expires_at: "2026-07-16T00:00:00Z",
    };

    await store.logout();

    expect(mocks.post).toHaveBeenCalledWith("/v1/admin/auth/logout");
    expect(store.identity).toBeNull();
    expect(mocks.setCsrfToken).toHaveBeenLastCalledWith(null);
  });
});
