interface Props {
  text: string;
  streaming: boolean;
}

export function ThinkingBlock({ text, streaming }: Props) {
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
