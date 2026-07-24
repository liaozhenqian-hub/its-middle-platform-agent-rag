import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChatEvents } from "./chat";

describe("chat stream request", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the selected knowledge scope and yields SSE events", async () => {
    const encoder = new TextEncoder();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              encoder.encode('event: run.started\ndata: {"conversation_id":"c1","run_id":"r1"}\n\n'),
            );
            controller.close();
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const events = [];
    for await (const event of streamChatEvents({
      message: "指标口径",
      conversation_id: null,
      knowledge_space_id: "middle-platform",
      domain_id: "metric-platform",
    })) {
      events.push(event);
    }

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/agent/chat/stream",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: expect.objectContaining({ "X-Client-Channel": "web" }),
      }),
    );
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      message: "指标口径",
      conversation_id: null,
      knowledge_space_id: "middle-platform",
      domain_id: "metric-platform",
    });
    expect(events[0].event).toBe("run.started");
  });
});
