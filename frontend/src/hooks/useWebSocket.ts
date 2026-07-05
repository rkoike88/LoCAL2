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
 * - sendJson queues the message if the socket is still connecting and flushes
 *   it on open, so a query sent immediately after New Chat is not lost.
 */
export function useWebSocket(url: string): UseWebSocketResult {
  const handlersRef    = useRef<Set<(data: unknown) => void>>(new Set());
  const [readyState, setReadyState] = useState<WsReadyState>("connecting");
  // openSocketRef holds the socket only while it is verified open.
  // sendJson uses this as the single source of truth — avoids races between
  // wsRef and openUrlRef getting out of sync during URL transitions.
  const openSocketRef  = useRef<WebSocket | null>(null);
  const pendingRef     = useRef<unknown[]>([]);
  const reconnectTimerRef    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);

  useEffect(() => {
    let intentionallyClosed = false;
    reconnectAttemptsRef.current = 0;
    openSocketRef.current = null;
    pendingRef.current = [];

    function connect() {
      setReadyState("connecting");
      const ws = new WebSocket(url);

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
        openSocketRef.current = ws;
        setReadyState("open");
        for (const msg of pendingRef.current) {
          ws.send(JSON.stringify(msg));
        }
        pendingRef.current = [];
      };

      ws.onclose = () => {
        openSocketRef.current = null;
        setReadyState("closed");
        if (intentionallyClosed) return;
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
      intentionallyClosed = true;
      openSocketRef.current = null;
      pendingRef.current = [];
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      setReadyState("closed");
    };
  }, [url]);

  const sendJson = useCallback((data: unknown) => {
    const ws = openSocketRef.current;
    if (ws) {
      ws.send(JSON.stringify(data));
    } else {
      pendingRef.current.push(data);
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
