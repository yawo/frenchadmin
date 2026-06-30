import { Link } from "react-router-dom";
import type { ChunkResult } from "../types";
import ConfidenceBadge from "./ConfidenceBadge";

const SOURCE_COLORS: Record<string, string> = {
  legi: "border-l-legi bg-legi-light/30 dark:bg-legi/10",
  jade: "border-l-jade bg-jade-light/30 dark:bg-jade/10",
  bofip: "border-l-bofip bg-bofip-light/30 dark:bg-bofip/10",
};

const SOURCE_BADGES: Record<string, string> = {
  legi: "bg-legi text-white",
  jade: "bg-jade text-white",
  bofip: "bg-bofip text-white",
};

interface Props {
  results: ChunkResult[];
  onSelectDoc?: (docId: string, sourceType: string) => void;
}

export default function ResultsPanel({ results, onSelectDoc }: Props) {
  if (results.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500 dark:text-gray-400">
        Aucun résultat. Essayez une autre requête.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {results.map((result, i) => (
        <div
          key={`${result.chunk_id}-${i}`}
          className={`border-l-4 rounded-lg p-4 ${SOURCE_COLORS[result.source_type] || ""}`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span
                  className={`px-2 py-0.5 rounded text-xs font-bold ${SOURCE_BADGES[result.source_type]}`}
                >
                  {result.source_type.toUpperCase()}
                </span>
                <ConfidenceBadge value={result.similarity} showLabel={false} />
                {result.metadata?.source === "graph_augmentation" && (
                  <span className="px-2 py-0.5 rounded text-xs bg-purple-100 text-purple-700">
                    via graphe
                  </span>
                )}
              </div>
              <Link
                to={`/documents/${result.source_type}/${result.doc_id}`}
                className="font-medium text-gray-900 dark:text-gray-100 hover:text-blue-600 dark:hover:text-blue-400 line-clamp-1"
                onClick={() => onSelectDoc?.(result.doc_id, result.source_type)}
              >
                {result.title || result.doc_id}
              </Link>
              <p className="text-sm text-gray-600 dark:text-gray-400 mt-1 line-clamp-3">
                {result.chunk_text}
              </p>
            </div>
            <div className="text-right text-xs text-gray-400 dark:text-gray-500 whitespace-nowrap">
              {(result.similarity * 100).toFixed(1)}%
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
