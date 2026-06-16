import { useCallback, useEffect, useState } from "react";
import type { ChatMessage } from "../types/events";
import { randomUUID } from "../utils/uuid";

export interface SessionMeta {
  session_id: string;
  title: string;
  message_count: number;
  started_at: number;
  last_active: number;
}

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
      const res = await fetch("/api/sessions");
      if (res.ok) setSessions(await res.json());
    } catch {
      // ignore network errors
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const loadSession = useCallback(async (id: string): Promise<ChatMessage[]> => {
    try {
      const res = await fetch(`/api/sessions/${id}`);
      if (!res.ok) return [];
      const data: {
        messages: Array<{
          role: string;
          content: string;
          groundedness?: string;
          critic_score?: number | null;
          critic_feedback?: string;
        }>;
      } = await res.json();
      return (data.messages ?? [])
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({
          id: randomUUID(),
          role: m.role as "user" | "assistant",
          content: m.content,
          groundedness: m.groundedness as ChatMessage["groundedness"],
          critique:
            m.critic_score != null
              ? { score: m.critic_score, feedback: m.critic_feedback ?? "" }
              : undefined,
        }));
    } catch {
      return [];
    }
  }, []);

  const deleteSession = useCallback(
    async (id: string) => {
      await fetch(`/api/sessions/${id}`, { method: "DELETE" });
      fetchSessions();
    },
    [fetchSessions]
  );

  return { sessions, fetchSessions, loadSession, deleteSession };
}
