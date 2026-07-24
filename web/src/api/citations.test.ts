import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./index";
import { fetchCitationDetail } from "./citations";

describe("citation detail api", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("encodes citation identity and forwards abort signal", async () => {
    const controller = new AbortController();
    const get = vi.spyOn(api, "get").mockResolvedValue({
      source_type: "code",
      source_id: "code:1",
      title: "Title",
      domain: "workflow",
      excerpt: "code",
      language: "java",
      truncated: false,
      metadata: {},
    });

    await fetchCitationDetail(
      {
        source_type: "code",
        source_id: "code:1",
        title: "Title",
        domain: "workflow",
        metadata: {},
      },
      controller.signal,
    );

    expect(get).toHaveBeenCalledWith(
      "/v1/citations/detail?source_type=code&source_id=code%3A1",
      { signal: controller.signal },
    );
  });
});
