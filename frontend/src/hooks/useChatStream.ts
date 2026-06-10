import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Attachment,
  ChatMessage,
  GatewayEvent,
  StreamingTurn,
  ToolCall,
} from "../types/events";
import { useWebSocket } from "./useWebSocket";

interface UseChatStreamResult {
  messages: ChatMessage[];
  streaming: StreamingTurn | null;
  isStreaming: boolean;
  sendQuery: (query: string, attachments?: Attachment[]) => void;
  loadHistory: (msgs: ChatMessage[]) => void;
  tokenCount: number;
}

function isGatewayEvent(v: unknown): v is GatewayEvent {
  return typeof v === "object" && v !== null && "type" in v;
}

/**
 * Manages a chat session over a single WebSocket connection.
 *
 * Processes the LoCAL2 gateway event protocol into a flat messages array
 * plus a streaming-turn object for in-progress responses. The WebSocket URL
 * embeds the session_id so the backend can correlate envelopes.
 *
 * @param sessionId - Active session; changing this reconnects the WebSocket.
 * @param onResponse - Optional callback fired after each completed response.
 */
export function useChatStream(
  sessionId: string,
  onResponse?: () => void
): UseChatStreamResult {
  const wsUrl =
    typeof window !== "undefined"
      ? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws/chat/${sessionId}`
      : `/ws/chat/${sessionId}`;

  const { sendJson, onMessage, readyState } = useWebSocket(wsUrl);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState<StreamingTurn | null>(null);
  const [tokenCount, setTokenCount] = useState(0);
  const pendingToolCallsRef = useRef<ToolCall[]>([]);
  const pendingQueryIdRef = useRef<string>("");
  const onResponseRef = useRef(onResponse);
  onResponseRef.current = onResponse;

  useEffect(() => {
    const unsub = onMessage((raw) => {
      if (!isGatewayEvent(raw)) return;
      const ev = raw;

      switch (ev.type) {
        case "thinking_chunk": {
          setStreaming((prev) =>
            prev
              ? { ...prev, thinking: prev.thinking + ev.chunk }
              : { query_id: ev.query_id, thinking: ev.chunk, active_tool: null }
          );
          break;
        }

        case "tool_start": {
          pendingQueryIdRef.current = ev.query_id;
          setStreaming((prev) =>
            prev
              ? { ...prev, active_tool: { tool: ev.tool, args: ev.args } }
              : {
                  query_id: ev.query_id,
                  thinking: "",
                  active_tool: { tool: ev.tool, args: ev.args },
                }
          );
          break;
        }

        case "tool_result": {
          // Accumulate completed tool calls; clear the active indicator.
          pendingToolCallsRef.current = [
            ...pendingToolCallsRef.current,
            { tool: ev.tool, args: {}, result: ev.result },
          ];
          setStreaming((prev) =>
            prev ? { ...prev, active_tool: null } : null
          );
          break;
        }

        case "response": {
          const msg: ChatMessage = {
            id: ev.query_id,
            role: "assistant",
            content: ev.answer,
            thinking: ev.thinking || undefined,
            tool_calls:
              ev.tool_calls.length > 0 ? ev.tool_calls : undefined,
            prompt_tokens: ev.prompt_tokens,
          };
          if (ev.prompt_tokens) setTokenCount(ev.prompt_tokens);
          setMessages((prev) => [...prev, msg]);
          setStreaming(null);
          pendingToolCallsRef.current = [];
          onResponseRef.current?.();
          break;
        }

        case "critique": {
          // Annotate the most recent assistant message with the critique score.
          setMessages((prev) => {
            const idx = prev.findLastIndex((m) => m.id === ev.query_id);
            if (idx === -1) return prev;
            const updated = [...prev];
            updated[idx] = {
              ...updated[idx],
              critique: { score: ev.score, feedback: ev.feedback },
            };
            return updated;
          });
          break;
        }
      }
    });
    return unsub;
  }, [onMessage]);

  const sendQuery = useCallback(
    (query: string, attachments?: Attachment[]) => {
      if (readyState !== "open" || !query.trim()) return;

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: query,
        attachments: attachments?.length ? attachments : undefined,
      };
      setMessages((prev) => [...prev, userMsg]);
      setStreaming({ query_id: "", thinking: "", active_tool: null });
      pendingToolCallsRef.current = [];

      const payload: Record<string, unknown> = { query };
      if (attachments?.length) payload.attachments = attachments;
      sendJson(payload);
    },
    [readyState, sendJson]
  );

  const loadHistory = useCallback((msgs: ChatMessage[]) => {
    setMessages(msgs);
    setStreaming(null);
    setTokenCount(0);
    pendingToolCallsRef.current = [];
  }, []);

  return {
    messages,
    streaming,
    isStreaming: streaming !== null,
    sendQuery,
    loadHistory,
    tokenCount,
  };
}
