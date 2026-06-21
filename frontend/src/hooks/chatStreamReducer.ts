import { deriveGroundedness } from "../utils/groundedness";
import type {
  ChatMessage,
  GatewayEvent,
  RetrievalSource,
  StreamingTurn,
  ToolCall,
} from "../types/events";

// Invariant: status === "streaming"  iff  streaming !== null

export type ChatStreamStatus = "idle" | "streaming";

interface PendingToolStart {
  tool: string;
  args: Record<string, unknown>;
  ts: string;
}

export interface ChatStreamState {
  status: ChatStreamStatus;
  messages: ChatMessage[];
  streaming: StreamingTurn | null;
  tokenCount: number;
  pendingToolCalls: ToolCall[];
  pendingToolStart: PendingToolStart | null;
  pendingSources: Record<string, RetrievalSource[]>;
}

export const initialChatStreamState: ChatStreamState = {
  status: "idle",
  messages: [],
  streaming: null,
  tokenCount: 0,
  pendingToolCalls: [],
  pendingToolStart: null,
  pendingSources: {},
};

export type ChatStreamAction =
  | GatewayEvent
  | { type: "load_history"; messages: ChatMessage[] }
  | { type: "query_sent"; message: ChatMessage };

export function chatStreamReducer(
  state: ChatStreamState,
  action: ChatStreamAction
): ChatStreamState {
  switch (action.type) {
    // idle | streaming -> streaming
    case "query_sent":
      return {
        ...state,
        status: "streaming",
        messages: [...state.messages, action.message],
        streaming: { query_id: "", thinking: "", active_tool: null },
        pendingToolCalls: [],
        pendingToolStart: null,
        pendingSources: {},
      };

    // idle | streaming -> streaming
    case "thinking_chunk":
      return {
        ...state,
        status: "streaming",
        streaming: state.streaming
          ? { ...state.streaming, thinking: state.streaming.thinking + action.chunk }
          : { query_id: action.query_id, thinking: action.chunk, active_tool: null },
      };

    // idle | streaming -> streaming
    case "tool_start":
      return {
        ...state,
        status: "streaming",
        pendingToolStart: { tool: action.tool, args: action.args, ts: action.ts },
        streaming: state.streaming
          ? { ...state.streaming, active_tool: { tool: action.tool, args: action.args } }
          : { query_id: action.query_id, thinking: "", active_tool: { tool: action.tool, args: action.args } },
      };

    // streaming -> streaming  (preserve status — streaming field stays non-null)
    case "tool_result": {
      const start = state.pendingToolStart;
      const newCall: ToolCall = {
        tool: action.tool,
        args: start?.tool === action.tool ? start.args : {},
        result: action.result,
        call_ts: start?.tool === action.tool ? start.ts : undefined,
        result_ts: action.ts,
      };
      const pendingSources = action.sources?.length
        ? {
            ...state.pendingSources,
            [action.query_id]: [
              ...(state.pendingSources[action.query_id] ?? []),
              ...action.sources,
            ],
          }
        : state.pendingSources;
      return {
        ...state,
        pendingToolCalls: [...state.pendingToolCalls, newCall],
        pendingToolStart: null,
        pendingSources,
        streaming: state.streaming ? { ...state.streaming, active_tool: null } : null,
      };
    }

    // streaming -> idle
    case "response": {
      const sources = state.pendingSources[action.query_id] ?? [];
      const pendingSources = { ...state.pendingSources };
      delete pendingSources[action.query_id];
      const toolNames = new Set(action.tool_calls.map((tc) => tc.tool));
      const msg: ChatMessage = {
        id: action.query_id,
        role: "assistant",
        content: action.answer,
        thinking: action.thinking || undefined,
        tool_calls: action.tool_calls.length > 0 ? action.tool_calls : undefined,
        groundedness: deriveGroundedness(toolNames),
        sources: sources.length > 0 ? sources : undefined,
        prompt_tokens: action.prompt_tokens,
      };
      return {
        ...state,
        status: "idle",
        messages: [...state.messages, msg],
        streaming: null,
        tokenCount: action.prompt_tokens ?? state.tokenCount,
        pendingToolCalls: [],
        pendingToolStart: null,
        pendingSources,
      };
    }

    // any -> preserve status  (critique arrives after response; status is already idle)
    case "critique":
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === action.query_id
            ? { ...m, critique: { score: action.score, feedback: action.feedback } }
            : m
        ),
      };

    // any -> idle
    case "load_history":
      return { ...initialChatStreamState, messages: action.messages };

    default:
      return state;
  }
}
