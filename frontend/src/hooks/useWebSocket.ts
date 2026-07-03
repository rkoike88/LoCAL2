import { useCallback, useEffect, useRef, useState } from "react";

export type WsReadyState = "connecting" | "open" | "closed";

interface UseWebSocketResult {
  readyState: WsReadyState;
  sendJson: (data: unknown) => void;
  onMessage: (handler: (data: unknown) => void) => () => void;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS  = 30_000;

/**
 * Manages a WebSocket connection to the given URL.
 *
 * - Connects on mount, closes on unmount.
 * - Auto-reconnects with exponential backoff when the connection closes
 *   unexpectedly (e.g. a long generation outlasts an idle timeout).
 * - Exposes sendJson for outbound messages and onMessage for subscribing to
 *   inbound messages. Multiple onMessage subscribers are supported; each
 *   returns an unsubscribe function.
 */
export function useWebSocket(url: string): UseWebSocketResult {
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef<Set<(data: unknown) => void>>(new Set());
  const [readyState, setReadyState] = useState<WsReadyState>("connecting");
  // Keep intentional-close flag and reconnect timer stable across renders.
  const closedIntentionallyRef = useRef(false);
  const reconnectTimerRef       = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef    = useRef(0);

  useEffect(() => {
    closedIntentionallyRef.current = false;
    reconnectAttemptsRef.current = 0;

    function connect() {
      setReadyState("connecting");
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
        setReadyState("open");
      };

      ws.onclose = () => {
        wsRef.current = null;
        setReadyState("closed");
        if (closedIntentionallyRef.current) return;
        // Exponential backoff: 1s, 2s, 4s, 8s … capped at 30s.
        const delay = Math.min(
          RECONNECT_BASE_MS * 2 ** reconnectAttemptsRef.current,
          RECONNECT_MAX_MS,
        );
        reconnectAttemptsRef.current += 1;
        reconnectTimerRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        // onclose fires right after onerror, so reconnect logic is there.
      };

      ws.onmessage = (event: MessageEvent) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(event.data as string);
        } catch {
          return;
        }
        handlersRef.current.forEach((h) => h(parsed));
      };
    }

    connect();

    return () => {
      closedIntentionallyRef.current = true;
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      wsRef.current?.close();
      wsRef.current = null;
      setReadyState("closed");
    };
  }, [url]);

  const sendJson = useCallback((data: unknown) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }, []);

  const onMessage = useCallback((handler: (data: unknown) => void) => {
    handlersRef.current.add(handler);
    return () => {
      handlersRef.current.delete(handler);
    };
  }, []);

  return { readyState, sendJson, onMessage };
}
