// Typed WebSocket events streamed from the LoCAL2 gateway.

export interface SessionMeta {
  session_id: string;
  title: string;
  message_count: number;
  started_at: number;
  last_active: number;
}


export interface ThinkingChunkEvent {
  type: "thinking_chunk";
  chunk: string;
  query_id: string;
}

export interface ToolStartEvent {
  type: "tool_start";
  tool: string;
  args: Record<string, unknown>;
  ts: string;
  query_id: string;
}

export interface RetrievalSource {
  type: "memory" | "library";
  // memory fields
  id?: string;
  score?: number;
  snippet?: string;
  query?: string;
  // library fields
  source_file?: string;
  chunk_index?: number;
  page?: number;
}

export interface ToolResultEvent {
  type: "tool_result";
  tool: string;
  result: string;
  sources: RetrievalSource[];
  ts: string;
  query_id: string;
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  result: string;
  call_ts?: string;
  result_ts?: string;
  error?: string;
}

export interface ToolTransitionEvent {
  type: "tool_transition";
  tool: string;
  from_state: string;
  action: string;
  to: string;
  error: string;
  query_id: string;
}

export interface ResponseEvent {
  type: "response";
  answer: string;
  thinking: string;
  tool_calls: ToolCall[];
  session_id: string;
  query_id: string;
  prompt_tokens: number;
  model?: string;
  capsules?: Array<{ content: string; score: number }>;
  pinned_facts?: PinnedFact[];
}

export interface CritiqueEvent {
  type: "critique";
  score: number | null;
  feedback: string;
  rubric_name: string;
  rubric_text: string;
  query_id: string;
}

export interface LibraryIngestStartedEvent {
  type: "library_ingest_started";
  filename: string;
  collection: string;
}

export interface LibraryIngestedEvent {
  type: "library_ingested";
  filename: string;
  collection: string;
  chunks: number;
  error: string;
}

export interface ContextUpdatedEvent {
  type: "context_updated";
  fact: string;
  reason: string;
}

export interface AgentStateEvent {
  type: "agent_state";
  agent: string;   // e.g. "generator_agent", "memory_agent"
  state: string;   // GeneratorState / MemoryAgentState value
  query_id: string;
}

export type GatewayEvent =
  | ThinkingChunkEvent
  | ToolStartEvent
  | ToolResultEvent
  | ToolTransitionEvent
  | ResponseEvent
  | CritiqueEvent
  | LibraryIngestStartedEvent
  | LibraryIngestedEvent
  | ContextUpdatedEvent
  | AgentStateEvent;

// A processed file attachment ready to send to the generator.
export interface Attachment {
  type: "text" | "image" | "error" | "uploading";
  name: string;
  data?: string;
  error?: string;
}

export interface PinnedFact {
  fact: string;
  reason: string;
}

export interface ContextBiscuit {
  capsules: Array<{ content: string; score: number }>;
  pinned_facts: PinnedFact[];
  persona?: string;
}

// A fully resolved message in the chat history.
export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "notice";
  content: string;
  thinking?: string;
  tool_calls?: ToolCall[];
  critique?: { score: number | null; feedback: string; rubric_name: string; rubric_text: string };
  groundedness?: "grounded" | "web" | "knowledge";
  sources?: RetrievalSource[];
  prompt_tokens?: number;
  attachments?: Attachment[];
  model?: string;
  persona?: string;
  context_biscuit?: ContextBiscuit;
  engram_id?: string;
}

// An in-progress assistant turn while streaming.
export interface StreamingTurn {
  query_id: string;
  thinking: string;
  active_tool: { tool: string; args: Record<string, unknown> } | null;
}
