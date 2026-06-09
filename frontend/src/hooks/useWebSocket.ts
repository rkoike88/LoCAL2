import { useCallback, useEffect, useRef, useState } from "react";

export type WsReadyState = "connecting" | "open" | "closed";

interface UseWebSocketResult {
  readyState: WsReadyState;
  sendJson: (data: unknown) => void;
  onMessage: (handler: (data: unknown) => void) => () => void;
}

/**
 * Manages a WebSocket connection to the given URL.
 *
 * - Connects on mount, closes on unmount.
 * - Exposes sendJson for outbound messages and onMessage for subscribing to
 *   inbound messages. Multiple onMessage subscribers are supported; each
 *   returns an unsubscribe function.
 */
export function useWebSocket(url: string): UseWebSocketResult {
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef<Set<(data: unknown) => void>>(new Set());
  const [readyState, setReadyState] = useState<WsReadyState>("connecting");

  useEffect(() => {
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setReadyState("open");
    ws.onclose = () => setReadyState("closed");
    ws.onerror = () => setReadyState("closed");

    ws.onmessage = (event: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(event.data as string);
      } catch {
        return;
      }
      handlersRef.current.forEach((h) => h(parsed));
    };

    return () => {
      ws.close();
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
