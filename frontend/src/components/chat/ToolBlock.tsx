import { useState } from "react";
import type { ToolCall } from "../../types/events";
import { formatTs } from "../../utils/format";
import { Spinner } from "./Spinner";

const RESULT_LIMIT = 500;

interface Props {
  calls?: ToolCall[];
  activeTool?: { tool: string; args: Record<string, unknown> } | null;
}

export function ToolBlock({ calls, activeTool }: Props) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  if (!calls?.length && !activeTool) return null;

  if (activeTool) {
    return (
      <div className="text-xs text-gray-500 font-mono flex items-center gap-1.5">
        <Spinner />
        {activeTool.tool}
      </div>
    );
  }

  const toolNames = [...new Set(calls!.map((tc) => tc.tool))].join(" · ");

  return (
    <details className="group">
      <summary className="text-xs text-gray-600 cursor-pointer select-none hover:text-gray-400 transition-colors font-mono">
        {toolNames} ({calls!.length} call{calls!.length !== 1 ? "s" : ""})
      </summary>
      <div className="mt-1 text-xs text-gray-500 font-mono bg-surface-1 rounded-lg p-3 border border-surface-3 space-y-3">
        {calls!.map((tc, i) => {
          const isExpanded = expanded.has(i);
          const truncated = !isExpanded && tc.result.length > RESULT_LIMIT;
          return (
            <div key={i}>
              <div className="text-gray-600">[{formatTs(tc.call_ts)}]  → {tc.tool}</div>
              {Object.entries(tc.args).map(([k, v]) => (
                <div key={k} className="pl-4 text-gray-500">
                  {k}: {typeof v === "string" ? v : JSON.stringify(v)}
                </div>
              ))}
              {tc.result && (
                <>
                  <div className="text-gray-600 mt-1">[{formatTs(tc.result_ts)}]  ← result</div>
                  <div className="pl-4 text-gray-500 whitespace-pre-wrap">
                    {truncated ? tc.result.slice(0, RESULT_LIMIT) : tc.result}
                    {truncated && (
                      <button
                        onClick={() => setExpanded((prev) => new Set([...prev, i]))}
                        className="text-accent-muted hover:text-accent cursor-pointer bg-transparent border-none p-0 ml-0.5"
                      >…</button>
                    )}
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </details>
  );
}
