import { useState } from "react";
import type { SessionMeta } from "../types/events";

function formatRelative(ts: number): string {
  const delta = Date.now() / 1000 - ts;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  if (delta < 604800) return `${Math.floor(delta / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function PanelIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 18 18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    >
      <rect x="1.5" y="1.5" width="15" height="15" rx="2" />
      <line x1="6" y1="1.5" x2="6" y2="16.5" />
    </svg>
  );
}

interface Props {
  sessions: SessionMeta[];
  activeSessionId: string;
  open: boolean;
  onToggle: () => void;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
}

export function SessionSidebar({
  sessions,
  activeSessionId,
  open,
  onToggle,
  onNewChat,
  onSelectSession,
  onDeleteSession,
}: Props) {
  const [query, setQuery] = useState("");
  const filtered = query.trim()
    ? sessions.filter((s) => s.title.toLowerCase().includes(query.toLowerCase()))
    : sessions;

  return (
    <aside
      className={`shrink-0 bg-surface-1 border-r border-surface-3 flex flex-col h-screen overflow-hidden transition-[width] duration-200 ${open ? "w-52" : "w-10"}`}
    >
      {/* Toggle + brand row */}
      <div className={`flex items-center px-2 py-3 border-b border-surface-3 ${open ? "" : "justify-center"}`}>
        {open && (
          <span className="flex-1 text-accent font-semibold text-sm whitespace-nowrap px-1">
            LoCAL2
          </span>
        )}
        <button
          onClick={onToggle}
          title={open ? "Close sidebar" : "Open sidebar"}
          className="shrink-0 text-gray-600 hover:text-gray-300 transition-colors p-1 rounded"
        >
          <PanelIcon />
        </button>
      </div>

      {/* Search + New chat — only shown when open */}
      {open && (
        <div className="px-3 py-2 border-b border-surface-3 space-y-1.5">
          <div className="relative">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search…"
              className="w-full text-xs px-3 py-1.5 rounded-lg bg-surface-2 border border-surface-3 text-gray-300 placeholder-gray-600 focus:outline-none focus:border-accent-muted"
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-600 hover:text-gray-400"
              >
                ×
              </button>
            )}
          </div>
          <button
            onClick={onNewChat}
            className="w-full text-left text-xs px-3 py-2 rounded-lg bg-surface-2 hover:bg-surface-3 text-gray-300 transition-colors"
          >
            + New chat
          </button>
        </div>
      )}

      {/* Session list — only shown when open */}
      {open && (
        <nav className="flex-1 overflow-y-auto py-2 px-1 space-y-0.5">
          {filtered.length === 0 && (
            <p className="text-gray-600 text-xs px-3 py-2">
              {query ? "No matches" : "No sessions yet"}
            </p>
          )}
          {filtered.map((s) => {
            const active = s.session_id === activeSessionId;
            return (
              <div
                key={s.session_id}
                className={`group flex items-start gap-1 px-2 py-2 rounded-lg cursor-pointer transition-colors ${
                  active
                    ? "bg-surface-3 text-white"
                    : "text-gray-400 hover:bg-surface-2 hover:text-gray-200"
                }`}
                onClick={() => onSelectSession(s.session_id)}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-xs truncate leading-snug">{s.title}</p>
                  <p className="text-[10px] text-gray-600 mt-0.5">
                    {formatRelative(s.last_active)}
                  </p>
                </div>
                <button
                  className="shrink-0 opacity-0 group-hover:opacity-100 text-gray-600 hover:text-red-400 transition-opacity text-sm leading-none mt-0.5"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(s.session_id);
                  }}
                  title="Delete session"
                >
                  ×
                </button>
              </div>
            );
          })}
        </nav>
      )}
    </aside>
  );
}
