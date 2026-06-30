import { useSynthesis } from "../hooks/useSynthesis";
import type { SourceType } from "../types";

interface Props {
  query: string;
  sourceTypes?: SourceType[];
}

export default function SynthesisPanel({ query, sourceTypes }: Props) {
  const { text, isStreaming, synthesize, abort } = useSynthesis();

  const handleSynthesize = () => {
    synthesize({
      query,
      source_types: sourceTypes?.length ? sourceTypes : undefined,
      top_k: 10,
    });
  };

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-medium text-gray-900">Synthèse IA</h3>
        <div className="flex gap-2">
          {isStreaming && (
            <button
              onClick={abort}
              className="px-3 py-1 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50"
            >
              Arrêter
            </button>
          )}
          <button
            onClick={handleSynthesize}
            disabled={isStreaming || !query}
            className="px-3 py-1 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50 hover:bg-blue-700"
          >
            {isStreaming ? "En cours..." : "Synthétiser"}
          </button>
        </div>
      </div>
      {text ? (
        <div className="prose prose-sm max-w-none text-gray-700 whitespace-pre-wrap">
          {text}
          {isStreaming && <span className="animate-pulse">|</span>}
        </div>
      ) : (
        <p className="text-sm text-gray-400">
          Cliquez sur "Synthétiser" pour obtenir une réponse basée sur les
          documents retrouvés.
        </p>
      )}
    </div>
  );
}
