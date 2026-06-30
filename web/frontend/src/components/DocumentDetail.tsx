import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { getDocument } from "../api/client";
import type { SourceType } from "../types";
import ConfidenceBadge from "./ConfidenceBadge";

export default function DocumentDetail() {
  const { sourceType, docId } = useParams<{
    sourceType: string;
    docId: string;
  }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["document", sourceType, docId],
    queryFn: () => getDocument(sourceType as SourceType, docId!),
    enabled: !!sourceType && !!docId,
  });

  if (isLoading) {
    return <div className="text-center py-12 text-gray-500">Chargement...</div>;
  }

  if (error || !data) {
    return (
      <div className="text-center py-12 text-red-500">
        Document non trouvé.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-3 mb-2">
          <span className="px-3 py-1 rounded-full text-sm font-bold bg-blue-100 text-blue-800">
            {data.source_type.toUpperCase()}
          </span>
          <span className="font-mono text-xs text-gray-400">{data.doc_id}</span>
        </div>
        <h1 className="text-2xl font-bold text-gray-900">
          {data.title || data.doc_id}
        </h1>
      </div>

      {Object.keys(data.metadata).length > 0 && (
        <div className="bg-gray-50 rounded-lg p-4">
          <h2 className="text-sm font-medium text-gray-700 mb-2">Métadonnées</h2>
          <dl className="grid grid-cols-2 md:grid-cols-3 gap-2 text-sm">
            {Object.entries(data.metadata).map(([key, value]) => (
              <div key={key}>
                <dt className="text-gray-500">{key}</dt>
                <dd className="text-gray-900">{String(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {data.cross_references.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-gray-900 mb-3">
            Références croisées ({data.cross_references.length})
          </h2>
          <div className="space-y-2">
            {data.cross_references.map((ref, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-3 bg-white border border-gray-200 rounded-lg"
              >
                <div>
                  <span className="text-sm font-medium text-gray-900">
                    {ref.relation_kind === "applies_to"
                      ? "S'applique à"
                      : "Interprète"}{" "}
                  </span>
                  <span className="font-mono text-xs text-gray-600">
                    {ref.source_doc_id === data.doc_id
                      ? ref.target_legi_doc_id
                      : ref.source_doc_id}
                  </span>
                  <div className="text-xs text-gray-400 mt-0.5">
                    {ref.resolver_methods.join(", ")} | {ref.occurrence_count}{" "}
                    occurrence(s)
                  </div>
                </div>
                <ConfidenceBadge value={ref.best_confidence} />
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <h2 className="text-lg font-semibold text-gray-900 mb-3">
          Contenu ({data.chunks.length} chunks)
        </h2>
        <div className="space-y-4">
          {data.chunks.map((chunk, i) => (
            <div
              key={chunk.chunk_id}
              className="p-4 bg-white border border-gray-200 rounded-lg"
            >
              <div className="text-xs text-gray-400 mb-2">
                Chunk {i + 1} &middot; {chunk.chunk_id}
              </div>
              <p className="text-sm text-gray-700 whitespace-pre-wrap">
                {chunk.chunk_text}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
