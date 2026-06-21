import { describe, expect, it } from "vitest";
import {
  chatStreamReducer,
  initialChatStreamState,
  type ChatStreamAction,
  type ChatStreamState,
} from "./chatStreamReducer";

function assertInvariant(state: ChatStreamState) {
  expect(state.status === "streaming").toBe(state.streaming !== null);
}

describe("chatStreamReducer", () => {
  it("status === 'streaming' iff streaming !== null across a representative action sequence", () => {
    const sequence: ChatStreamAction[] = [
      // new query arrives
      { type: "query_sent", message: { id: "q1", role: "user", content: "hello" } },
      // model starts thinking
      { type: "thinking_chunk", query_id: "q1", chunk: "Let me think…" },
      { type: "thinking_chunk", query_id: "q1", chunk: " about that." },
      // tool call
      { type: "tool_start", query_id: "q1", tool: "web_search", args: { query: "hello" }, ts: "2024-01-01T00:00:00Z" },
      { type: "tool_result", query_id: "q1", tool: "web_search", result: "some result", sources: [], ts: "2024-01-01T00:00:01Z" },
      // response finalises the turn
      { type: "response", query_id: "q1", answer: "Here is my answer", thinking: "", tool_calls: [], session_id: "s1", prompt_tokens: 100 },
      // critique arrives after response (status should still be idle)
      { type: "critique", query_id: "q1", score: 4, feedback: "Good" },
      // history load resets everything
      { type: "load_history", messages: [] },
    ];

    let state = initialChatStreamState;
    assertInvariant(state);
    for (const action of sequence) {
      state = chatStreamReducer(state, action);
      assertInvariant(state);
    }
  });
});
