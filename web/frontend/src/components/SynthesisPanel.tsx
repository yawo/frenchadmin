import { useEffect, useRef } from "react";
import { useSynthesis } from "../hooks/useSynthesis";
import type { SourceType } from "../types";

interface Props {
  query: string;
  sourceTypes?: SourceType[];
  onComplete?: (text: string) => void;
}

export default function SynthesisPanel({ query, sourceTypes, onComplete }: Props) {
  const { text, isStreaming, synthesize, abort } = useSynthesis();
  const prevStreamingRef = useRef(false);

  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming && text && onComplete) {
      onComplete(text);
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, text, onComplete]);

  const handleSynthesize = () => {
    synthesize({
      query,
      source_types: sourceTypes?.length ? sourceTypes : undefined,
      top_k: 10,
    });
  };

  return (
    <div className="bg-white dark:bg-slate-800 rounded-xl border border-gray-200 dark:border-slate-700 p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-medium text-gray-900 dark:text-white">Synthèse IA</h3>
        <div className="flex gap-2">
          {isStreaming && (
            <button
              onClick={abort}
              className="px-3 py-1 text-sm text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/30"
            >
              Arrêter
            </button>
          )}
          <button
            onClick={handleSynthesize}
            disabled={isStreaming || !query}
            className="px-3 py-1 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50 hover:bg-blue-700 transition-colors"
          >
            {isStreaming ? "En cours..." : "Synthétiser"}
          </button>
        </div>
      </div>
      {text ? (
        <div className="prose prose-sm max-w-none text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
          {text}
          {isStreaming && <span className="animate-pulse">|</span>}
        </div>
      ) : (
        <p className="text-sm text-gray-400 dark:text-gray-500">
          Cliquez sur &quot;Synthétiser&quot; pour obtenir une réponse basée sur les
          documents retrouvés.
        </p>
      )}
    </div>
  );
}
