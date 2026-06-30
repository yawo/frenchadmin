import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getCrossRefs } from "../api/client";
import type { SourceType } from "../types";
import ConfidenceBadge from "./ConfidenceBadge";

export default function CrossRefTable() {
  const [page, setPage] = useState(1);
  const [sourceFilter, setSourceFilter] = useState<SourceType | "">("");
  const [minConfidence, setMinConfidence] = useState(0.55);

  const { data, isLoading } = useQuery({
    queryKey: ["crossrefs", sourceFilter, minConfidence, page],
    queryFn: () =>
      getCrossRefs({
        source_type: sourceFilter || undefined,
        min_confidence: minConfidence,
        page,
        page_size: 20,
      }),
  });

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 0;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">
        Références croisées
      </h1>

      <div className="flex items-center gap-4 text-sm">
        <div className="flex items-center gap-2">
          <label className="text-gray-500">Source:</label>
          <select
            value={sourceFilter}
            onChange={(e) => {
              setSourceFilter(e.target.value as SourceType | "");
              setPage(1);
            }}
            className="border border-gray-300 rounded px-2 py-1"
          >
            <option value="">Toutes</option>
            <option value="jade">JADE</option>
            <option value="bofip">BOFiP</option>
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-gray-500">Confiance min:</label>
          <input
            type="range"
            min="0"
            max="100"
            value={minConfidence * 100}
            onChange={(e) => {
              setMinConfidence(Number(e.target.value) / 100);
              setPage(1);
            }}
            className="w-24"
          />
          <span className="text-gray-700 w-10">
            {(minConfidence * 100).toFixed(0)}%
          </span>
        </div>
        {data && (
          <span className="text-gray-400 ml-auto">
            {data.total} résultat(s)
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="text-center py-8 text-gray-400">Chargement...</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left py-2 px-3 text-gray-500 font-medium">
                  Source
                </th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">
                  Cible LEGI
                </th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">
                  Relation
                </th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">
                  Confiance
                </th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">
                  Occurrences
                </th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">
                  Méthodes
                </th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((ref, i) => (
                <tr
                  key={i}
                  className="border-b border-gray-100 hover:bg-gray-50"
                >
                  <td className="py-2 px-3">
                    <span className="font-mono text-xs">
                      {ref.source_type.toUpperCase()}:{" "}
                      {ref.source_doc_id.slice(0, 16)}...
                    </span>
                  </td>
                  <td className="py-2 px-3 font-mono text-xs">
                    {ref.target_legi_doc_id.slice(0, 16)}...
                  </td>
                  <td className="py-2 px-3">
                    <span className="px-2 py-0.5 rounded text-xs bg-gray-100">
                      {ref.relation_kind}
                    </span>
                  </td>
                  <td className="py-2 px-3">
                    <ConfidenceBadge
                      value={ref.best_confidence}
                      showLabel={false}
                    />
                  </td>
                  <td className="py-2 px-3 text-center">
                    {ref.occurrence_count}
                  </td>
                  <td className="py-2 px-3 text-xs text-gray-500">
                    {ref.resolver_methods.join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1 border rounded disabled:opacity-50"
          >
            Précédent
          </button>
          <span className="text-sm text-gray-500">
            Page {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="px-3 py-1 border rounded disabled:opacity-50"
          >
            Suivant
          </button>
        </div>
      )}
    </div>
  );
}
