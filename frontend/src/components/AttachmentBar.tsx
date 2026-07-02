import { useRef } from "react";
import { flushSync } from "react-dom";
import { uploadAttachment } from "../api/client";
import type { Attachment } from "../types/events";
import { Spinner } from "./chat/Spinner";

const ACCEPTED = ".jpg,.jpeg,.png,.gif,.webp,.pdf,.txt,.md,.py,.js,.ts,.yaml,.yml,.json,.csv";

interface Props {
  attachments: Attachment[];
  onChange: (attachments: Attachment[]) => void;
  disabled?: boolean;
}

export function AttachmentBar({ attachments, onChange, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const fileArray = Array.from(files);
    console.log("[AttachmentBar] handleFiles:", fileArray.map(f => `${f.name} (${(f.size / 1024).toFixed(0)}KB)`));

    const before = attachments;
    flushSync(() => {
      onChange([...before, ...fileArray.map(f => ({ type: "uploading" as const, name: f.name }))]);
    });

    const results: Attachment[] = [];
    for (const file of fileArray) {
      console.log("[AttachmentBar] uploading:", file.name);
      try {
        results.push(await uploadAttachment(file));
        console.log("[AttachmentBar] done:", file.name);
      } catch {
        results.push({ type: "error", name: file.name, error: "Upload failed" });
      }
    }
    onChange([...before, ...results]);
    if (inputRef.current) inputRef.current.value = "";
  }

  function remove(idx: number) {
    onChange(attachments.filter((_, i) => i !== idx));
  }

  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-surface-text-muted hover:text-accent hover:bg-surface-3 transition-colors disabled:opacity-40"
        title="Attach file"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
        </svg>
      </button>

      {attachments.map((att, i) => (
        <span
          key={i}
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-mono ${
            att.type === "error"
              ? "bg-red-900/40 text-red-400 border border-red-700/40"
              : att.type === "uploading"
              ? "bg-yellow-900/40 text-yellow-300 border border-yellow-700/40"
              : "bg-surface-3 text-accent border border-surface-3"
          }`}
        >
          {att.type === "uploading" ? <Spinner /> : att.type === "error" ? "⚠" : "📎"} {att.name}
          {att.type !== "uploading" && (
            <button
              type="button"
              onClick={() => remove(i)}
              className="ml-0.5 hover:text-white transition-colors"
              aria-label={`Remove ${att.name}`}
            >
              ✕
            </button>
          )}
        </span>
      ))}

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        multiple
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />
    </div>
  );
}
