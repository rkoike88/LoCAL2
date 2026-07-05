import { useCallback, useEffect, useReducer, useRef } from "react";
import { randomUUID } from "../utils/uuid";
import type { Attachment, ChatMessage, GatewayEvent, StreamingTurn } from "../types/events";
import { useWebSocket } from "./useWebSocket";
import { chatStreamReducer, initialChatStreamState } from "./chatStreamReducer";

export interface UseChatStreamResult {
  messages: ChatMessage[];
  streaming: StreamingTurn | null;
  isStreaming: boolean;
  sendQuery: (query: string, attachments?: Attachment[]) => void;
  loadHistory: (msgs: ChatMessage[]) => void;
  tokenCount: number;
  toast: string | null;
  clearToast: () => void;
  generatorState: string;
}

function isGatewayEvent(v: unknown): v is GatewayEvent {
  return typeof v === "object" && v !== null && "type" in v;
}

/**
 * Manages a chat session over a single WebSocket connection.
 *
 * Processes the LoCAL2 gateway event protocol via chatStreamReducer into a
 * flat messages array plus a streaming-turn object for in-progress responses.
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
  const [state, dispatch] = useReducer(chatStreamReducer, initialChatStreamState);
  const onResponseRef = useRef(onResponse);
  onResponseRef.current = onResponse;

  useEffect(() => {
    return onMessage((raw) => {
      if (!isGatewayEvent(raw)) return;
      dispatch(raw);
      if (raw.type === "response") onResponseRef.current?.();
    });
  }, [onMessage]);

  const sendQuery = useCallback(
    (query: string, attachments?: Attachment[]) => {
      if (readyState !== "open" || !query.trim()) return;
      const message: ChatMessage = {
        id: randomUUID(),
        role: "user",
        content: query,
        attachments: attachments?.length ? attachments : undefined,
      };
      dispatch({ type: "query_sent", message });
      const payload: Record<string, unknown> = { query };
      if (attachments?.length) payload.attachments = attachments;
      sendJson(payload);
    },
    [readyState, sendJson, sessionId, wsUrl]
  );

  const loadHistory = useCallback((msgs: ChatMessage[]) => {
    dispatch({ type: "load_history", messages: msgs });
  }, []);

  const clearToast = useCallback(() => dispatch({ type: "clear_toast" }), []);

  return {
    messages: state.messages,
    streaming: state.streaming,
    isStreaming: state.status === "streaming",
    sendQuery,
    loadHistory,
    tokenCount: state.tokenCount,
    toast: state.toast,
    clearToast,
    generatorState: state.generatorState,
  };
}
