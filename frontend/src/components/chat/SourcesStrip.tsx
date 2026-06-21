import { useState } from "react";
import type { RetrievalSource } from "../../types/events";

interface Props {
  sources: RetrievalSource[];
}

export function SourcesStrip({ sources }: Props) {
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
              const queryStr = s.query
                ? `"${s.query.slice(0, 50)}${s.query.length > 50 ? "…" : ""}"`
                : (s.id ?? "");
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
