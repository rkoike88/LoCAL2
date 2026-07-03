import { useState } from "react";
import { postFeedback } from "../../api/client";

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
  groundedness?: string;
  model?: string;
  persona?: string;
  queryId: string;
  sessionId: string;
}

export function CritiqueBar({ score, feedback, groundedness, model, persona, queryId, sessionId }: Props) {
  const [sentiment, setSentiment] = useState<"positive" | "negative" | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  async function sendFeedback(s: "positive" | "negative") {
    setSentiment(s);
    await postFeedback(queryId, sessionId, s);
  }

  const ground = groundedness ? GROUND_CONFIG[groundedness] : null;
  const hasFeedback = Boolean(feedback);

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
            ◈ {persona}
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
