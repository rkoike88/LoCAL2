import type { ChatMessage, ToolCall } from "../types/events";

const WEB_TOOLS = new Set(["web_search", "web_fetch"]);
const GROUNDED_TOOLS = new Set(["search_memory", "search_library", "search_papers", "consult_librarian"]);

export function deriveGroundedness(toolNames: Set<string>): ChatMessage["groundedness"] {
  if (toolNames.size === 0) return "knowledge";
  if ([...toolNames].some((n) => WEB_TOOLS.has(n))) return "web";
  if ([...toolNames].some((n) => GROUNDED_TOOLS.has(n))) return "grounded";
  return "knowledge";
}

export function derivePersona(toolCalls: ToolCall[] | undefined): string | undefined {
  const call = toolCalls?.find((tc) => tc.tool === "persona");
  return call?.args?.name as string | undefined;
}
