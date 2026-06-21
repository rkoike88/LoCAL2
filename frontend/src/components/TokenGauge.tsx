import { useState } from "react";
import { compactSession } from "../api/client";

interface Props {
  tokenCount: number;
  sessionId: string;
}

export function TokenGauge({ tokenCount, sessionId }: Props) {
  const [compacting, setCompacting] = useState(false);
  const [compacted, setCompacted] = useState(false);

  async function compact() {
    setCompacting(true);
    setCompacted(false);
    try {
      await compactSession(sessionId);
      setCompacted(true);
    } finally {
      setCompacting(false);
    }
  }

  if (!tokenCount) return null;

  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <button
        onClick={compact}
        disabled={compacting}
        className="text-gray-600 hover:text-accent disabled:opacity-40 transition-colors"
        title="Summarise context to free space"
      >
        {compacting ? "⟳" : compacted ? "✓ compacted" : "compact"}
      </button>
    </div>
  );
}
