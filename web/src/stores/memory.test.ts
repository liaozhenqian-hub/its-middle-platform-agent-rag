import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  delete: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("@/api", () => ({ api: mocks }));

import { useMemoryStore } from "./memory";

describe("memory store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("loads candidates and confirmed memories, then approves a candidate", async () => {
    const candidate = { id: "candidate-1", status: "candidate" };
    const confirmed = { id: "candidate-1", status: "confirmed" };
    mocks.get.mockResolvedValueOnce([candidate]).mockResolvedValueOnce([]).mockResolvedValueOnce({
      candidate: { user_preference: 2 }, confirmed: {}, rejected: {}, deleted: {},
    }).mockResolvedValueOnce([]);
    mocks.post.mockResolvedValueOnce(confirmed);
    const store = useMemoryStore();

    await store.load();
    await store.approve("candidate-1");

    expect(mocks.get).toHaveBeenNthCalledWith(
      1,
      "/v1/admin/memory/candidates?status=candidate&limit=200",
    );
    expect(mocks.post).toHaveBeenCalledWith(
      "/v1/admin/memory/candidates/candidate-1/approve",
      {},
    );
    expect(store.candidates).toEqual([]);
    expect(store.memories).toEqual([confirmed]);
    expect(store.personalStatistics?.candidate.user_preference).toBe(2);
  });
});
