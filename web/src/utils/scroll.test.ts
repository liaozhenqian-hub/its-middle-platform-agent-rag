import { describe, expect, it } from "vitest";

import { isNearScrollBottom } from "./scroll";

describe("isNearScrollBottom", () => {
  it("keeps following only while the reader remains near the bottom", () => {
    expect(isNearScrollBottom({ scrollHeight: 1000, scrollTop: 620, clientHeight: 320 })).toBe(true);
    expect(isNearScrollBottom({ scrollHeight: 1000, scrollTop: 240, clientHeight: 320 })).toBe(false);
  });
});
