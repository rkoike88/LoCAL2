import { useCallback, useEffect, useState } from "react";
import type { ChatMessage, SessionMeta } from "../types/events";
import { derivePersona } from "../utils/groundedness";
import { randomUUID } from "../utils/uuid";
import {
  deleteSession as apiDeleteSession,
  getSessions,
  getSession,
} from "../api/client";

export interface UseSessionsResult {
  sessions: SessionMeta[];
  fetchSessions: () => Promise<void>;
  loadSession: (id: string) => Promise<ChatMessage[]>;
  deleteSession: (id: string) => Promise<void>;
}

export function useSessions(): UseSessionsResult {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);

  const fetchSessions = useCallback(async () => {
    try {
      setSessions(await getSessions());
    } catch {
      // ignore network errors
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const loadSession = useCallback(async (id: string): Promise<ChatMessage[]> => {
    try {
      const data = await getSession(id);
      const contextLog: Record<string, unknown> = data.context_log ?? {};
      return (data.messages ?? [])
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => {
          // Biscuits are keyed by query_id which isn't in raw messages, so we
          // try to match positionally from the context_log values in insertion order.
          const queryId = m.query_id as string | undefined;
          const biscuit = queryId ? (contextLog[queryId] as import("../types/events").ContextBiscuit | undefined) : undefined;
          return {
            id: randomUUID(),
            role: m.role as "user" | "assistant",
            content: m.content,
            groundedness: m.groundedness as ChatMessage["groundedness"],
            thinking: m.thinking || undefined,
            tool_calls: m.tool_calls?.length ? m.tool_calls : undefined,
            persona: derivePersona(m.tool_calls ?? undefined) || undefined,
            critique:
              m.critic_score != null
                ? { score: m.critic_score, feedback: m.critic_feedback ?? "" }
                : undefined,
            context_biscuit: biscuit,
            engram_id: m.engram_id ?? undefined,
          };
        });
    } catch {
      return [];
    }
  }, []);

  const deleteSession = useCallback(
    async (id: string) => {
      await apiDeleteSession(id);
      fetchSessions();
    },
    [fetchSessions]
  );

  return { sessions, fetchSessions, loadSession, deleteSession };
}
