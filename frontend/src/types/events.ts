// Typed WebSocket events streamed from the LoCAL2 gateway.

export interface ThinkingChunkEvent {
  type: "thinking_chunk";
  chunk: string;
  query_id: string;
}

export interface ToolStartEvent {
  type: "tool_start";
  tool: string;
  args: Record<string, unknown>;
  query_id: string;
}

export interface ToolResultEvent {
  type: "tool_result";
  tool: string;
  result: string;
  query_id: string;
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  result: string;
}

export interface ResponseEvent {
  type: "response";
  answer: string;
  thinking: string;
  tool_calls: ToolCall[];
  session_id: string;
  query_id: string;
  prompt_tokens: number;
}

export interface CritiqueEvent {
  type: "critique";
  score: number | null;
  feedback: string;
  query_id: string;
}

export type GatewayEvent =
  | ThinkingChunkEvent
  | ToolStartEvent
  | ToolResultEvent
  | ResponseEvent
  | CritiqueEvent;

// A processed file attachment ready to send to the generator.
export interface Attachment {
  type: "text" | "image" | "error";
  name: string;
  data?: string;
  error?: string;
}

// A fully resolved message in the chat history.
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  tool_calls?: ToolCall[];
  critique?: { score: number | null; feedback: string };
  prompt_tokens?: number;
  attachments?: Attachment[];
}

// An in-progress assistant turn while streaming.
export interface StreamingTurn {
  query_id: string;
  thinking: string;
  active_tool: { tool: string; args: Record<string, unknown> } | null;
}
