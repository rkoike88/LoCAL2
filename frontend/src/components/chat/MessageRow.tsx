import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../../types/events";
import { CritiqueBar } from "./CritiqueBar";
import { SourcesStrip } from "./SourcesStrip";
import { ThinkingBlock } from "./ThinkingBlock";
import { ToolBlock } from "./ToolBlock";

interface Props {
  msg: ChatMessage;
  sessionId: string;
}

export function MessageRow({ msg, sessionId }: Props) {
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
      <ToolBlock calls={msg.tool_calls} />
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
