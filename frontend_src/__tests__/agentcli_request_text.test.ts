import { afterEach, describe, expect, it, vi } from "vitest";
import { agentCliChat, setHass } from "../api";

describe("the chat request body", () => {
  afterEach(() => setHass(null));

  async function sent(text: string): Promise<string> {
    const fetchWithAuth = vi.fn(async (_path: string, _init: RequestInit) => new Response("event: done\ndata: {}\n\n", { status: 200 }));
    setHass({ fetchWithAuth, user: { id: "admin" }, language: "en" });
    await agentCliChat({ messages: [{ role: "assistant", content: text }] } as never, () => undefined);
    return String(fetchWithAuth.mock.calls[0][1].body);
  }

  it("replaces half a surrogate pair, which the server would refuse on every later turn", async () => {
    const body = await sent("before \ud83d after \ude00 end");
    expect(body).not.toMatch(/\\ud8|\\ude/i);
    expect(JSON.parse(body).messages[0].content).toBe("before � after � end");
  });

  it("keeps a whole pair", async () => {
    expect(JSON.parse(await sent("smile 😀")).messages[0].content).toBe("smile 😀");
  });
});
