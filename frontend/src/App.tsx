import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useChatStream } from "./hooks/useChatStream";

const SESSION_ID = crypto.randomUUID();

export default function App() {
  const { messages, streaming, isStreaming, sendQuery } =
    useChatStream(SESSION_ID);
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

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

  return (
    <div className="flex flex-col h-screen max-w-3xl mx-auto px-4">
      {/* Header */}
      <header className="py-4 border-b border-surface-3 flex items-center gap-2">
        <span className="text-accent font-semibold text-lg">LoCAL2</span>
        <span className="text-gray-500 text-sm ml-auto">
          {isStreaming ? "thinking…" : "ready"}
        </span>
      </header>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto py-6 space-y-6">
        {messages.length === 0 && !isStreaming && (
          <p className="text-gray-500 text-sm text-center mt-16">
            Start a conversation
          </p>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className="space-y-2">
            {msg.role === "user" ? (
              <div className="flex justify-end">
                <div className="bg-surface-2 rounded-2xl px-4 py-2 max-w-[80%] text-sm whitespace-pre-wrap">
                  {msg.content}
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                {/* Thinking block — collapsed by default */}
                {msg.thinking && (
                  <details className="group">
                    <summary className="text-xs text-gray-500 cursor-pointer select-none hover:text-gray-300">
                      thinking
                    </summary>
                    <pre className="mt-1 text-xs text-gray-400 whitespace-pre-wrap bg-surface-1 rounded-lg p-3 border border-surface-3">
                      {msg.thinking}
                    </pre>
                  </details>
                )}

                {/* Tool calls */}
                {msg.tool_calls && msg.tool_calls.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {msg.tool_calls.map((tc, i) => (
                      <span
                        key={i}
                        className="text-xs bg-surface-2 border border-surface-3 rounded px-2 py-0.5 text-accent-muted"
                      >
                        {tc.tool}
                      </span>
                    ))}
                  </div>
                )}

                {/* Answer */}
                <div className="prose prose-invert prose-sm max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </ReactMarkdown>
                </div>

                {/* Critique badge */}
                {msg.critique && msg.critique.score != null && (
                  <div className="flex items-center gap-1.5">
                    <CritiqueIndicator score={msg.critique.score} />
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {/* In-progress streaming turn */}
        {isStreaming && streaming && (
          <div className="space-y-2 opacity-70">
            {streaming.thinking && (
              <pre className="text-xs text-gray-400 whitespace-pre-wrap bg-surface-1 rounded-lg p-3 border border-surface-3">
                {streaming.thinking}
              </pre>
            )}
            {streaming.active_tool && (
              <span className="text-xs bg-surface-2 border border-surface-3 rounded px-2 py-0.5 text-accent-muted">
                {streaming.active_tool.tool}…
              </span>
            )}
            {!streaming.thinking && !streaming.active_tool && (
              <span className="text-xs text-gray-500">
                generating…
              </span>
            )}
          </div>
        )}
      </div>

      {/* Input bar */}
      <div className="py-4 border-t border-surface-3">
        <div className="flex gap-2 items-end bg-surface-1 rounded-xl border border-surface-3 px-4 py-3">
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
        <p className="text-center text-xs text-gray-600 mt-2">
          Enter to send · Shift+Enter for newline
        </p>
      </div>
    </div>
  );
}

function CritiqueIndicator({ score }: { score: number }) {
  const colors: Record<number, string> = {
    1: "bg-red-500",
    2: "bg-orange-500",
    3: "bg-yellow-500",
    4: "bg-green-400",
    5: "bg-emerald-400",
  };
  const color = colors[score] ?? "bg-gray-500";
  return (
    <span
      title={`Critic score: ${score}/5`}
      className={`inline-block w-2 h-2 rounded-full ${color}`}
    />
  );
}
