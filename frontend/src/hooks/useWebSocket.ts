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
  // openUrlRef tracks which URL the currently-open socket belongs to.
  // sendJson checks this so a stale open socket can't send to the wrong session.
  const openUrlRef              = useRef<string | null>(null);
  const closedIntentionallyRef  = useRef(false);
  const reconnectTimerRef       = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef    = useRef(0);

  useEffect(() => {
    closedIntentionallyRef.current = false;
    reconnectAttemptsRef.current = 0;
    openUrlRef.current = null;

    function connect() {
      setReadyState("connecting");
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
        openUrlRef.current = url;
        setReadyState("open");
      };

      ws.onclose = () => {
        wsRef.current = null;
        openUrlRef.current = null;
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
    // Guard: only send if this socket is open AND it was opened for the current URL.
    // Prevents a stale open socket (old session) from sending on behalf of a new session.
    if (ws?.readyState === WebSocket.OPEN && openUrlRef.current === url) {
      ws.send(JSON.stringify(data));
    }
  }, [url]);

  const onMessage = useCallback((handler: (data: unknown) => void) => {
    handlersRef.current.add(handler);
    return () => {
      handlersRef.current.delete(handler);
    };
  }, []);

  return { readyState, sendJson, onMessage };
}
