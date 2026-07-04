import { useCallback, useEffect, useRef, useState } from "react";
import { AttachmentBar } from "./components/AttachmentBar";
import { MessageRow, Spinner, ThinkingBlock, ToolBlock } from "./components/chat";
import { SessionSidebar } from "./components/SessionSidebar";
import { TokenGauge } from "./components/TokenGauge";
import { useAutoScroll } from "./hooks/useAutoScroll";
import { useChatStream } from "./hooks/useChatStream";
import { useGeneratorSettings } from "./hooks/useGeneratorSettings";
import { useSessions } from "./hooks/useSessions";
import { deleteEngram } from "./api/client";
import type { Attachment } from "./types/events";
import { randomUUID } from "./utils/uuid";

const STATE_LABELS: Record<string, string> = {
  retrieving:       "retrieving context…",
  receiving:        "processing…",
  generating:       "generating…",
  dispatching_tool: "calling tool…",
  waiting_for_tool: "waiting for tool…",
  publishing:       "finishing…",
};

function generatorStateLabel(state: string): string {
  return STATE_LABELS[state] ?? "generating…";
}

export default function App() {
  const [activeSessionId, setActiveSessionId] = useState<string>(() => randomUUID());
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [selectedEngrams, setSelectedEngrams] = useState<Set<string>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const { sessions, fetchSessions, loadSession, deleteSession } = useSessions();

  const { messages, streaming, isStreaming, sendQuery, loadHistory, tokenCount, toast, clearToast, generatorState } =
    useChatStream(activeSessionId, fetchSessions);

  const { models, selectedModel, temperature, numCtx, handleModelChange, setSelectedModel } =
    useGeneratorSettings();

  // Update header model display from the most recent response's actual model.
  useEffect(() => {
    const last = [...messages].reverse().find((m) => m.role === "assistant" && m.model);
    if (last?.model) setSelectedModel(last.model);
  }, [messages, setSelectedModel]);

  // Auto-dismiss completion toasts after 5 s; loading toasts (⟳) stay until replaced.
  useEffect(() => {
    if (!toast || toast.startsWith("⟳")) return;
    const t = setTimeout(clearToast, 5000);
    return () => clearTimeout(t);
  }, [toast, clearToast]);

  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useAutoScroll([messages, streaming?.thinking, streaming?.active_tool]);

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
    fetchSessions();
  }, [loadHistory, fetchSessions]);

  async function handleDeleteSession(id: string) {
    await deleteSession(id);
    if (id === activeSessionId) handleNewChat();
  }

  function handleToggleEngram(engramId: string) {
    setSelectedEngrams((prev) => {
      const next = new Set(prev);
      if (next.has(engramId)) next.delete(engramId); else next.add(engramId);
      return next;
    });
    setConfirmingDelete(false);
  }

  async function handleConfirmDelete() {
    await Promise.all([...selectedEngrams].map(deleteEngram));
    loadHistory(messages.filter((m) => !m.engram_id || !selectedEngrams.has(m.engram_id)));
    setSelectedEngrams(new Set());
    setConfirmingDelete(false);
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

      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <header className="shrink-0 px-6 py-3 border-b border-surface-3 flex items-center gap-3">
          <span className="text-gray-400 text-sm">
            {isStreaming ? (
              <span className="flex items-center gap-1.5">
                <Spinner />
                <span>{generatorStateLabel(generatorState)}</span>
              </span>
            ) : (
              <span className="text-gray-600">ready</span>
            )}
          </span>
          <div className="ml-auto flex items-center gap-4">
            {selectedModel && (
              <span className="text-xs text-gray-600 font-mono hidden sm:flex items-center gap-3">
                <span>model: {selectedModel}</span>
                {temperature != null && <span>temp: {temperature}</span>}
                {numCtx != null && (
                  <span>
                    context:{" "}
                    {tokenCount > 0 ? `${tokenCount.toLocaleString()}/` : ""}
                    {numCtx >= 1000 ? `${Math.round(numCtx / 1000)}k` : numCtx}
                  </span>
                )}
              </span>
            )}
            <TokenGauge tokenCount={tokenCount} sessionId={activeSessionId} />
          </div>
        </header>

        {/* Selection action bar */}
        {selectedEngrams.size > 0 && (
          <div className="shrink-0 px-6 py-2 border-b border-surface-3 flex items-center gap-3 bg-surface-1">
            <span className="text-xs text-gray-400">{selectedEngrams.size} engram{selectedEngrams.size > 1 ? "s" : ""} selected</span>
            {confirmingDelete ? (
              <>
                <span className="text-xs text-red-400">Remove from memory? This cannot be undone.</span>
                <button onClick={handleConfirmDelete} className="text-xs text-red-400 hover:text-red-300 transition-colors">Confirm</button>
                <button onClick={() => setConfirmingDelete(false)} className="text-xs text-gray-500 hover:text-gray-300 transition-colors">Cancel</button>
              </>
            ) : (
              <>
                <button onClick={() => setConfirmingDelete(true)} className="text-xs text-red-500 hover:text-red-400 transition-colors">Delete selected</button>
                <button onClick={() => setSelectedEngrams(new Set())} className="text-xs text-gray-500 hover:text-gray-300 transition-colors">Clear</button>
              </>
            )}
          </div>
        )}

        {/* Message list */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
          {messages.length === 0 && !isStreaming && (
            <p className="text-gray-600 text-sm text-center mt-24">Start a conversation</p>
          )}
          {messages.map((msg) => (
            <MessageRow
              key={msg.id}
              msg={msg}
              sessionId={activeSessionId}
              selected={!!msg.engram_id && selectedEngrams.has(msg.engram_id)}
              onToggle={handleToggleEngram}
            />
          ))}

          {/* In-progress streaming turn */}
          {isStreaming && streaming && (
            <div className="space-y-2 max-w-2xl">
              {streaming.thinking && <ThinkingBlock text={streaming.thinking} streaming />}
              <ToolBlock activeTool={streaming.active_tool} />
              {!streaming.thinking && !streaming.active_tool && (
                <span className="text-xs text-gray-600 flex items-center gap-1.5">
                  <Spinner />
                  {generatorStateLabel(generatorState)}
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
            <p className="text-xs text-gray-700">Enter to send · Shift+Enter for newline</p>
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

      {toast && (
        <div
          className="fixed bottom-6 right-6 z-50 bg-surface-2 border border-surface-3 text-gray-300 text-sm rounded-lg px-4 py-3 shadow-lg max-w-sm"
          onClick={clearToast}
          role="status"
        >
          {toast}
        </div>
      )}
    </div>
  );
}
