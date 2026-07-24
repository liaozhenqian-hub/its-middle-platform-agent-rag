import { describe, expect, it } from "vitest";

import { parseSseStream } from "./sse";

describe("SSE parser", () => {
  it("parses events split across byte chunks", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: text.delta\ndata: {"delta":"你'));
        controller.enqueue(encoder.encode('好"}\n\nevent: run.completed\ndata: {"answer":"你好"}\n\n'));
        controller.close();
      },
    });

    const events = [];
    for await (const event of parseSseStream(stream)) events.push(event);

    expect(events).toEqual([
      { event: "text.delta", data: { delta: "你好" } },
      { event: "run.completed", data: { answer: "你好" } },
    ]);
  });
});
