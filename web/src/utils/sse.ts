export interface SseEvent<T = unknown> {
  event: string;
  data: T;
}

export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const lines = block.split("\n");
        const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim() ?? "message";
        const dataText = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (dataText) yield { event, data: JSON.parse(dataText) };
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // The stream may already be closed when the terminal event is received.
    } finally {
      reader.releaseLock();
    }
  }
}
