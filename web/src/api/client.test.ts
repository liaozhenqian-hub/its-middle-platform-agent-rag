import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, createApiClient } from "./client";

describe("API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses same-origin credentials and applies CSRF only to writes", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = createApiClient(() => "csrf-123");

    await client.get("/v1/admin/sources");
    await client.post("/v1/admin/jobs/job-1/retry");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/sources");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
    expect(new Headers(fetchMock.mock.calls[0][1].headers).has("X-CSRF-Token")).toBe(false);
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get("X-CSRF-Token")).toBe(
      "csrf-123",
    );
  });

  it("normalizes FastAPI detail errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "session expired" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(createApiClient(() => null).get("/v1/admin/auth/me")).rejects.toEqual(
      expect.objectContaining<ApiError>({ status: 401, message: "session expired" }),
    );
  });
});
