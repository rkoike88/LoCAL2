import type { Attachment, SessionMeta } from "../types/events";

export interface GeneratorSettings {
  model?: string;
  models?: { default: string; vision?: string; quality?: string };
  temperature: number | null;
  num_ctx: number | null;
}

export interface RawSessionMessage {
  role: string;
  content: string;
  groundedness?: string;
  critic_score?: number | null;
  critic_feedback?: string;
  thinking?: string;
  tool_calls?: Array<{ tool: string; args: Record<string, unknown>; result: string }> | null;
}

export interface SessionDetail {
  messages: RawSessionMessage[];
}

export async function getModels(): Promise<string[]> {
  const r = await fetch("/api/models");
  const d = await r.json();
  return d.models ?? [];
}

export async function getGeneratorSettings(): Promise<GeneratorSettings> {
  const r = await fetch("/api/settings/generator");
  return r.json();
}

export async function updateGeneratorSettings(patch: Partial<GeneratorSettings>): Promise<void> {
  const current = await getGeneratorSettings();
  await fetch("/api/settings/generator", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...current, ...patch }),
  });
}

export async function postFeedback(
  queryId: string,
  sessionId: string,
  sentiment: "positive" | "negative"
): Promise<void> {
  await fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query_id: queryId, session_id: sessionId, sentiment }),
  });
}

export async function getSessions(): Promise<SessionMeta[]> {
  const r = await fetch("/api/sessions");
  if (!r.ok) return [];
  return r.json();
}

export async function getSession(id: string): Promise<SessionDetail> {
  const r = await fetch(`/api/sessions/${id}`);
  if (!r.ok) throw new Error(`session ${id} not found`);
  return r.json();
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`/api/sessions/${id}`, { method: "DELETE" });
}

export async function compactSession(sessionId: string): Promise<void> {
  await fetch(`/api/sessions/${sessionId}/compact`, { method: "POST" });
}

export async function uploadAttachment(file: File): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch("/api/attachments", { method: "POST", body: form });
  return r.json();
}
