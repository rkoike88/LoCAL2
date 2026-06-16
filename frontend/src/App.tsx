import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AttachmentBar } from "./components/AttachmentBar";
import { SessionSidebar } from "./components/SessionSidebar";
import { TokenGauge } from "./components/TokenGauge";
import { useChatStream } from "./hooks/useChatStream";
import { useSessions } from "./hooks/useSessions";
import type { Attachment, ChatMessage, RetrievalSource, ToolCall } from "./types/events";
import { randomUUID } from "./utils/uuid";

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

function SourcesStrip({ sources }: { sources: RetrievalSource[] }) {
  const [open, setOpen] = useState(false);
  if (!sources.length) return null;

  return (
    <div className="text-xs text-gray-500 font-mono">
      <button
        onClick={() => setOpen((v) => !v)}
        className="hover:text-gray-400 transition-colors bg-transparent border-none p-0 cursor-pointer"
      >
        {open ? "▼" : "▶"} {sources.length} source{sources.length !== 1 ? "s" : ""}
      </button>
      {open && (
        <div className="mt-1 space-y-1 pl-3 border-l border-surface-3">
          {sources.map((s, i) => {
            const prefix = i === 0 ? "┌" : i === sources.length - 1 ? "└" : "├";
            if (s.type === "memory") {
              const scoreStr = s.score != null ? ` · ${s.score.toFixed(2)}` : "";
              const queryStr = s.query ? `"${s.query.slice(0, 50)}${s.query.length > 50 ? "…" : ""}"` : s.id ?? "";
              return (
                <div key={i} className="text-gray-500" title={s.snippet}>
                  {prefix} memory: {queryStr}{scoreStr}
                </div>
              );
            } else {
              const pageStr = s.page != null ? ` p.${s.page}` : "";
              const chunkStr = s.chunk_index != null ? ` chunk ${s.chunk_index}` : "";
              return (
                <div key={i} className="text-gray-500" title={s.snippet}>
                  {prefix} {s.source_file}{pageStr}{chunkStr}
                </div>
              );
            }
          })}
        </div>
      )}
    </div>
  );
}

const GROUND_CONFIG: Record<string, { label: string; className: string; title: string }> = {
  grounded:  { label: "⊙ grounded",  className: "text-teal-400",  title: "Answer drew on retrieved memory or library sources" },
  web:       { label: "◉ web",        className: "text-blue-400",  title: "Answer drew on live web search or fetched page content" },
  knowledge: { label: "○ knowledge",  className: "text-gray-600",  title: "Answer came from the model's training knowledge — no retrieval tools used" },
};

function CritiqueBar({
  score,
  feedback,
  groundedness,
  queryId,
  sessionId,
}: {
  score: number | null;
  feedback?: string;
  groundedness?: string;
  queryId: string;
  sessionId: string;
}) {
  const [sentiment, setSentiment] = useState<"positive" | "negative" | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  async function sendFeedback(s: "positive" | "negative") {
    setSentiment(s);
    await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query_id: queryId, session_id: sessionId, sentiment: s }),
    });
  }

  const scoreColor: Record<number, string> = {
    1: "text-red-500",
    2: "text-orange-500",
    3: "text-yellow-500",
    4: "text-green-400",
    5: "text-emerald-400",
  };

  const ground = groundedness ? GROUND_CONFIG[groundedness] : null;
  const hasFeedback = Boolean(feedback);

  return (
    <div className="pt-1">
      <div className="flex items-center gap-3">
        {ground && (
          <span className={`text-xs ${ground.className}`} title={ground.title}>
            {ground.label}
          </span>
        )}
        {score != null ? (
          <button
            className={`text-xs ${scoreColor[score] ?? "text-gray-500"} ${hasFeedback ? "hover:opacity-70 cursor-pointer" : "cursor-default"} bg-transparent border-none p-0`}
            onClick={() => hasFeedback && setFeedbackOpen((v) => !v)}
            title={hasFeedback ? "Toggle Prometheus feedback" : undefined}
          >
            ● {score}/5{hasFeedback ? (feedbackOpen ? "  ◈ feedback ▼" : "  ◈ feedback ▶") : ""}
          </button>
        ) : null}
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
      {feedbackOpen && feedback && (
        <div className="mt-2 text-xs text-gray-400 font-mono whitespace-pre-wrap border-l-2 border-surface-3 pl-3">
          {feedback}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------

export default function App() {
  const [activeSessionId, setActiveSessionId] = useState<string>(() => randomUUID());
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const { sessions, fetchSessions, loadSession, deleteSession } = useSessions();

  const { messages, streaming, isStreaming, sendQuery, loadHistory, tokenCount } =
    useChatStream(activeSessionId, fetchSessions);

  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");

  useEffect(() => {
    fetch("/api/models")
      .then((r) => r.json())
      .then((d) => setModels(d.models ?? []))
      .catch(() => {});
    fetch("/api/settings/generator")
      .then((r) => r.json())
      .then((d) => { if (d.model) setSelectedModel(d.model); })
      .catch(() => {});
  }, []);

  async function handleModelChange(model: string) {
    setSelectedModel(model);
    try {
      const current = await fetch("/api/settings/generator").then((r) => r.json());
      await fetch("/api/settings/generator", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...current, model }),
      });
    } catch {
      // Non-fatal — model selection is best-effort
    }
  }

  // Auto-scroll to bottom as messages / streaming content change.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming?.thinking, streaming?.active_tool]);

  function handleSubmit() {
    const q = input.trim();
    if (!q || isStreaming) return;
    sendQuery(q, attachments.length ? attachments : undefined);
    setInput("");
    setAttachments([]);
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
    const id = randomUUID();
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
        onToggle={() => setSidebarOpen((o) => !o)}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
      />

      {/* Chat panel */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <header className="shrink-0 px-6 py-3 border-b border-surface-3 flex items-center gap-3">
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
          <div className="flex flex-col bg-surface-1 rounded-xl border border-surface-3 px-4 py-3 max-w-3xl">
            <div className="flex gap-2 items-end">
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
            <div className="mt-2">
              <AttachmentBar
                attachments={attachments}
                onChange={setAttachments}
                disabled={isStreaming}
              />
            </div>
          </div>
          <div className="flex items-center justify-between mt-2 max-w-3xl">
            <p className="text-xs text-gray-700">
              Enter to send · Shift+Enter for newline
            </p>
            {models.length > 0 && (
              <select
                value={selectedModel}
                onChange={(e) => handleModelChange(e.target.value)}
                className="text-xs bg-transparent text-gray-600 border-none outline-none cursor-pointer hover:text-gray-400 transition-colors"
              >
                {models.map((m) => (
                  <option key={m} value={m} className="bg-surface-1 text-gray-300">
                    {m}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
      </div>
    </div>
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
      {msg.sources && <SourcesStrip sources={msg.sources} />}
      <div className="prose prose-invert prose-sm max-w-none">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => (
              <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent underline hover:opacity-80">
                {children}
              </a>
            ),
          }}
        >
          {msg.content}
        </ReactMarkdown>
      </div>
      <CritiqueBar
        score={msg.critique?.score ?? null}
        feedback={msg.critique?.feedback}
        groundedness={msg.groundedness}
        queryId={msg.id}
        sessionId={sessionId}
      />
    </div>
  );
}
