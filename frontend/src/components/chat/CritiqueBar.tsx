import { useState } from "react";
import { postFeedback } from "../../api/client";
import type { ContextBiscuit } from "../../types/events";

const GROUND_CONFIG: Record<string, { label: string; className: string; title: string }> = {
  grounded: { label: "⊙ grounded", className: "text-teal-400",  title: "Answer drew on retrieved memory or library sources" },
  web:      { label: "◉ web",       className: "text-blue-400",  title: "Answer drew on live web search or fetched page content" },
  knowledge:{ label: "○ knowledge", className: "text-gray-600",  title: "Answer came from the model's training knowledge — no retrieval tools used" },
};

const SCORE_COLOR: Record<number, string> = {
  1: "text-red-500",
  2: "text-orange-500",
  3: "text-yellow-500",
  4: "text-green-400",
  5: "text-emerald-400",
};

interface Props {
  score: number | null;
  feedback?: string;
  rubricName?: string;
  rubricText?: string;
  groundedness?: string;
  model?: string;
  persona?: string;
  personaRole?: string;
  queryId: string;
  sessionId: string;
  contextBiscuit?: ContextBiscuit;
}

export function CritiqueBar({ score, feedback, rubricName, rubricText, groundedness, model, persona, personaRole, queryId, sessionId, contextBiscuit }: Props) {
  const [sentiment, setSentiment] = useState<"positive" | "negative" | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [biscuitOpen, setBiscuitOpen] = useState(false);

  const hasBiscuit = contextBiscuit && (
    contextBiscuit.capsules.length > 0 ||
    contextBiscuit.candidates.length > 0 ||
    contextBiscuit.pinned_facts.length > 0
  );

  async function sendFeedback(s: "positive" | "negative") {
    setSentiment(s);
    await postFeedback(queryId, sessionId, s);
  }

  const ground = groundedness ? GROUND_CONFIG[groundedness] : null;
  const hasFeedback = Boolean(feedback);
  const showRubric = Boolean(rubricName);

  return (
    <div className="pt-1">
      <div className="flex items-center gap-3">
        {model && (
          <span className="text-xs text-surface-text-muted font-mono" title="Model used for this response">
            {model}
          </span>
        )}
        {persona && (
          <span className="text-xs text-purple-400 font-mono" title="Cognitive persona active for this response">
            ◈ {persona}{personaRole ? ` · ${personaRole}` : ""}
          </span>
        )}
        {ground && (
          <span className={`text-xs ${ground.className}`} title={ground.title}>
            {ground.label}
          </span>
        )}
        {score != null ? (
          <button
            className={`text-xs ${SCORE_COLOR[score] ?? "text-gray-500"} ${hasFeedback ? "hover:opacity-70 cursor-pointer" : "cursor-default"} bg-transparent border-none p-0`}
            onClick={() => hasFeedback && setFeedbackOpen((v) => !v)}
            title={hasFeedback ? "Toggle Prometheus feedback" : undefined}
          >
            ● {score}/5{showRubric ? `  ◈ ${rubricName}` : ""}{hasFeedback ? (feedbackOpen ? "  ◈ feedback ▼" : "  ◈ feedback ▶") : ""}
          </button>
        ) : null}
        {hasBiscuit && (
          <button
            className="text-xs text-indigo-400 hover:opacity-70 bg-transparent border-none p-0 cursor-pointer"
            onClick={() => setBiscuitOpen((v) => !v)}
            title="Show context that conditioned this response"
          >
            ◈ context {biscuitOpen ? "▼" : "▶"}
          </button>
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
      {feedbackOpen && (rubricText || feedback) && (
        <div className="mt-2 text-xs font-mono border-l-2 border-surface-3 pl-3 space-y-2">
          {rubricText && (
            <div className="text-gray-600 whitespace-pre-wrap">{rubricText}</div>
          )}
          {rubricText && feedback && (
            <div className="border-t border-surface-3" />
          )}
          {feedback && (
            <div className="text-gray-400 whitespace-pre-wrap">{feedback}</div>
          )}
        </div>
      )}
      {biscuitOpen && contextBiscuit && (
        <div className="mt-2 text-xs font-mono border-l-2 border-indigo-900 pl-3 space-y-2">
          {contextBiscuit.pinned_facts.length > 0 && (
            <div>
              <div className="text-indigo-400 mb-1">pinned facts</div>
              {contextBiscuit.pinned_facts.map((f, i) => (
                <div key={i} className="text-gray-400">— {f.fact}</div>
              ))}
            </div>
          )}
          {contextBiscuit.capsules.length > 0 && (
            <div>
              <div className="text-indigo-400 mb-1">retrieved sessions</div>
              {contextBiscuit.capsules.map((c, i) => (
                <div key={i} className="text-gray-500">
                  <span className="text-indigo-700">↑ [{c.score.toFixed(2)}]</span> {c.content}
                </div>
              ))}
            </div>
          )}
          {contextBiscuit.candidates.length > 0 && (
            <div>
              <div className="text-gray-700 mb-1">below threshold</div>
              {contextBiscuit.candidates.map((c, i) => (
                <div key={i} className="text-gray-700">
                  <span className="text-gray-600">↓ [{c.score.toFixed(2)}]</span> {c.content}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
