import type { SessionMeta } from "../hooks/useSessions";

function formatRelative(ts: number): string {
  const delta = (Date.now() / 1000 - ts);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  if (delta < 604800) return `${Math.floor(delta / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

interface Props {
  sessions: SessionMeta[];
  activeSessionId: string;
  open: boolean;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
}

export function SessionSidebar({
  sessions,
  activeSessionId,
  open,
  onNewChat,
  onSelectSession,
  onDeleteSession,
}: Props) {
  return (
    <aside
      className={`shrink-0 bg-surface-1 border-r border-surface-3 flex flex-col h-screen overflow-hidden transition-[width] duration-200 ${open ? "w-52" : "w-0"}`}
    >
      {/* Brand + new chat */}
      <div className="p-3 border-b border-surface-3 space-y-2">
        <span className="block text-accent font-semibold text-sm px-1">LoCAL2</span>
        <button
          onClick={onNewChat}
          className="w-full text-left text-xs px-3 py-2 rounded-lg bg-surface-2 hover:bg-surface-3 text-gray-300 transition-colors"
        >
          + New chat
        </button>
      </div>

      {/* Session list */}
      <nav className="flex-1 overflow-y-auto py-2 px-1 space-y-0.5">
        {sessions.length === 0 && (
          <p className="text-gray-600 text-xs px-3 py-2">No sessions yet</p>
        )}
        {sessions.map((s) => {
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
    </aside>
  );
}
