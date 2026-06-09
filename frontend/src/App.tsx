import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SessionSidebar } from "./components/SessionSidebar";
import { TokenGauge } from "./components/TokenGauge";
import { useChatStream } from "./hooks/useChatStream";
import { useSessions } from "./hooks/useSessions";
import type { ChatMessage, ToolCall } from "./types/events";

// ---------------------------------------------------------------------------
// Small helper components
// ---------------------------------------------------------------------------

function ThinkingBlock({ text, streaming }: { text: string; streaming: boolean }) {
  if (streaming) {
    return (
      <div className="text-xs text-gray-400 bg-surface-1 rounded-lg p-3 border border-surface-3 whitespace-pre-wrap font-mono">
        {text}
      </div>
    );
  }
  return (
    <details className="group">
      <summary className="text-xs text-gray-600 cursor-pointer select-none hover:text-gray-400 transition-colors">
        thinking ({Math.round(text.length / 5)} words)
      </summary>
      <pre className="mt-1 text-xs text-gray-500 whitespace-pre-wrap bg-surface-1 rounded-lg p-3 border border-surface-3 font-mono">
        {text}
      </pre>
    </details>
  );
}

function ToolChips({ calls, activeTool }: { calls?: ToolCall[]; activeTool?: { tool: string } | null }) {
  const [expanded, setExpanded] = useState<number | null>(null);

  if (!calls?.length && !activeTool) return null;

  return (
    <div className="flex flex-wrap gap-1.5">
      {calls?.map((tc, i) => (
        <div key={i} className="relative">
          <button
            onClick={() => setExpanded(expanded === i ? null : i)}
            className="text-xs bg-surface-2 border border-surface-3 rounded px-2 py-0.5 text-accent-muted hover:border-accent-muted transition-colors"
          >
            {tc.tool}
          </button>
          {expanded === i && (
            <div className="absolute z-10 top-full left-0 mt-1 w-80 bg-surface-1 border border-surface-3 rounded-lg p-3 shadow-xl text-xs text-gray-400 space-y-2">
              {Object.keys(tc.args).length > 0 && (
                <div>
                  <p className="text-gray-600 mb-1">args</p>
                  <pre className="whitespace-pre-wrap break-all">{JSON.stringify(tc.args, null, 2)}</pre>
                </div>
              )}
              {tc.result && (
                <div>
                  <p className="text-gray-600 mb-1">result</p>
                  <pre className="whitespace-pre-wrap break-all line-clamp-6">{tc.result.slice(0, 600)}{tc.result.length > 600 ? "…" : ""}</pre>
                </div>
              )}
            </div>
          )}
        </div>
      ))}
      {activeTool && (
        <span className="text-xs bg-surface-2 border border-surface-3 rounded px-2 py-0.5 text-accent-muted flex items-center gap-1">
          <Spinner />
          {activeTool.tool}
        </span>
      )}
    </div>
  );
}

function Spinner() {
  return (
    <span className="inline-block w-3 h-3 border border-accent-muted border-t-transparent rounded-full animate-spin" />
  );
}

function CritiqueBar({
  score,
  feedback,
  queryId,
  sessionId,
}: {
  score: number | null;
  feedback: string;
  queryId: string;
  sessionId: string;
}) {
  const [sentiment, setSentiment] = useState<"positive" | "negative" | null>(null);

  async function sendFeedback(s: "positive" | "negative") {
    setSentiment(s);
    await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query_id: queryId, session_id: sessionId, sentiment: s }),
    });
  }

  const scoreColor: Record<number, string> = {
    1: "bg-red-500",
    2: "bg-orange-500",
    3: "bg-yellow-500",
    4: "bg-green-400",
    5: "bg-emerald-400",
  };

  return (
    <div className="flex items-center gap-3 pt-1">
      {score != null && (
        <div className="group relative flex items-center gap-1.5 cursor-default">
          <span className={`inline-block w-2 h-2 rounded-full ${scoreColor[score] ?? "bg-gray-500"}`} />
          <span className="text-xs text-gray-600">{score}/5</span>
          {feedback && (
            <div className="pointer-events-none absolute bottom-full left-0 mb-1.5 w-64 bg-surface-1 border border-surface-3 rounded-lg p-2 text-xs text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity shadow-xl z-10">
              {feedback}
            </div>
          )}
        </div>
      )}
      <div className="flex gap-1 ml-auto">
        <button
          onClick={() => sendFeedback("positive")}
          title="Good response"
          className={`text-sm transition-colors ${sentiment === "positive" ? "text-green-400" : "text-gray-700 hover:text-gray-400"}`}
        >
          ↑
        </button>
        <button
          onClick={() => sendFeedback("negative")}
          title="Poor response"
          className={`text-sm transition-colors ${sentiment === "negative" ? "text-red-400" : "text-gray-700 hover:text-gray-400"}`}
        >
          ↓
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------

export default function App() {
  const [activeSessionId, setActiveSessionId] = useState<string>(() => crypto.randomUUID());
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const { sessions, fetchSessions, loadSession, deleteSession } = useSessions();

  const { messages, streaming, isStreaming, sendQuery, loadHistory, tokenCount } =
    useChatStream(activeSessionId, fetchSessions);

  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as messages / streaming content change.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming?.thinking, streaming?.active_tool]);

  function handleSubmit() {
    const q = input.trim();
    if (!q || isStreaming) return;
    sendQuery(q);
    setInput("");
    inputRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  async function handleSelectSession(id: string) {
    if (id === activeSessionId) return;
    const msgs = await loadSession(id);
    setActiveSessionId(id);
    loadHistory(msgs);
  }

  const handleNewChat = useCallback(() => {
    const id = crypto.randomUUID();
    setActiveSessionId(id);
    loadHistory([]);
  }, [loadHistory]);

  async function handleDeleteSession(id: string) {
    await deleteSession(id);
    if (id === activeSessionId) {
      handleNewChat();
    }
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <SessionSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        open={sidebarOpen}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
      />

      {/* Chat panel */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <header className="shrink-0 px-4 py-3 border-b border-surface-3 flex items-center gap-3">
          <button
            onClick={() => setSidebarOpen((o) => !o)}
            title={sidebarOpen ? "Close sidebar" : "Open sidebar"}
            className="text-gray-600 hover:text-gray-300 transition-colors p-1 rounded"
          >
            <SidebarIcon />
          </button>
          <span className="text-gray-400 text-sm">
            {isStreaming ? (
              <span className="flex items-center gap-1.5">
                <Spinner />
                <span>generating…</span>
              </span>
            ) : (
              <span className="text-gray-600">ready</span>
            )}
          </span>
          <div className="ml-auto">
            <TokenGauge tokenCount={tokenCount} sessionId={activeSessionId} />
          </div>
        </header>

        {/* Message list */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
          {messages.length === 0 && !isStreaming && (
            <p className="text-gray-600 text-sm text-center mt-24">
              Start a conversation
            </p>
          )}

          {messages.map((msg) => (
            <MessageRow key={msg.id} msg={msg} sessionId={activeSessionId} />
          ))}

          {/* In-progress streaming turn */}
          {isStreaming && streaming && (
            <div className="space-y-2 max-w-2xl">
              {streaming.thinking && (
                <ThinkingBlock text={streaming.thinking} streaming />
              )}
              <ToolChips activeTool={streaming.active_tool} />
              {!streaming.thinking && !streaming.active_tool && (
                <span className="text-xs text-gray-600 flex items-center gap-1.5">
                  <Spinner />
                  generating…
                </span>
              )}
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input bar */}
        <div className="shrink-0 px-6 py-4 border-t border-surface-3">
          <div className="flex gap-2 items-end bg-surface-1 rounded-xl border border-surface-3 px-4 py-3 max-w-3xl">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Message LoCAL2…"
              rows={1}
              disabled={isStreaming}
              className="flex-1 bg-transparent resize-none outline-none text-sm text-gray-100 placeholder-gray-600 max-h-40 disabled:opacity-50"
              style={{ height: "auto" }}
              onInput={(e) => {
                const t = e.currentTarget;
                t.style.height = "auto";
                t.style.height = `${t.scrollHeight}px`;
              }}
            />
            <button
              onClick={handleSubmit}
              disabled={!input.trim() || isStreaming}
              className="text-accent disabled:text-gray-600 text-sm font-medium transition-colors"
            >
              Send
            </button>
          </div>
          <p className="text-xs text-gray-700 mt-2 max-w-3xl text-center">
            Enter to send · Shift+Enter for newline
          </p>
        </div>
      </div>
    </div>
  );
}

function SidebarIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <rect x="1.5" y="1.5" width="15" height="15" rx="2" />
      <line x1="6" y1="1.5" x2="6" y2="16.5" />
    </svg>
  );
}

function MessageRow({ msg, sessionId }: { msg: ChatMessage; sessionId: string }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="bg-surface-2 rounded-2xl px-4 py-2 max-w-[70%] text-sm whitespace-pre-wrap">
          {msg.content}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2 max-w-2xl">
      {msg.thinking && <ThinkingBlock text={msg.thinking} streaming={false} />}
      <ToolChips calls={msg.tool_calls} />
      <div className="prose prose-invert prose-sm max-w-none">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
      </div>
      <CritiqueBar
        score={msg.critique?.score ?? null}
        feedback={msg.critique?.feedback ?? ""}
        queryId={msg.id}
        sessionId={sessionId}
      />
    </div>
  );
}
