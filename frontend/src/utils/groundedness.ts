import type { ChatMessage } from "../types/events";

const WEB_TOOLS = new Set(["web_search", "web_fetch"]);
const GROUNDED_TOOLS = new Set(["search_memory", "search_library", "search_papers"]);

export function deriveGroundedness(toolNames: Set<string>): ChatMessage["groundedness"] {
  if (toolNames.size === 0) return "knowledge";
  if ([...toolNames].some((n) => WEB_TOOLS.has(n))) return "web";
  if ([...toolNames].some((n) => GROUNDED_TOOLS.has(n))) return "grounded";
  return "knowledge";
}
