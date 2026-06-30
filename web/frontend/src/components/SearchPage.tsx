import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import type { ChunkResult, HistoryEntry, SourceType } from "../types";
import { useHistory } from "../hooks/useHistory";
import { useSearch } from "../hooks/useSearch";
import FilterPanel, { type Filters } from "./FilterPanel";
import IntroSection from "./IntroSection";
import ResultsPanel from "./ResultsPanel";
import SearchBar from "./SearchBar";
import SynthesisPanel from "./SynthesisPanel";

function exportMarkdown(query: string, results: ChunkResult[], synthesis: string | null) {
  const lines: string[] = [];
  lines.push(`# Recherche : ${query}\n`);
  lines.push(`*Exporté le ${new Date().toLocaleDateString("fr-FR")} à ${new Date().toLocaleTimeString("fr-FR")}*\n`);

  if (synthesis) {
    lines.push(`## Synthèse\n`);
    lines.push(synthesis);
    lines.push("");
  }

  lines.push(`## Résultats (${results.length})\n`);
  for (const r of results) {
    lines.push(`### ${r.title || r.doc_id}`);
    lines.push(`- **Source** : ${r.source_type.toUpperCase()}`);
    lines.push(`- **Confiance** : ${(r.similarity * 100).toFixed(1)}%`);
    lines.push(`- **Doc ID** : ${r.doc_id}\n`);
    lines.push(`> ${r.chunk_text.replace(/\n/g, "\n> ")}\n`);
  }

  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `recherche-${query.slice(0, 40).replace(/[^a-zA-Z0-9àâäéèêëîïôùûüç -]/g, "").trim().replace(/\s+/g, "-")}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function SearchPage() {
  const location = useLocation();
  const [lastQuery, setLastQuery] = useState("");
  const [savedSynthesis, setSavedSynthesis] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({
    sourceTypes: ["legi", "jade", "bofip"] as SourceType[],
    minConfidence: 0,
    topK: 10,
  });

  const { addSearch, addSynthesis } = useHistory();
  const searchMutation = useSearch();

  useEffect(() => {
    const state = location.state as { historyEntry?: HistoryEntry } | null;
    if (!state?.historyEntry) return;
    const entry = state.historyEntry;

    if (entry.sourceTypes?.length) {
      setFilters((f) => ({ ...f, sourceTypes: entry.sourceTypes! }));
    }
    setLastQuery(entry.query);
    setSavedSynthesis(entry.synthesisText || null);

    const sourceTypes = entry.sourceTypes?.length ? entry.sourceTypes : undefined;
    searchMutation.mutate(
      {
        query: entry.query,
        source_types: sourceTypes,
        top_k: filters.topK,
        min_confidence: filters.minConfidence || undefined,
      },
      {
        onSuccess: (data) => {
          addSearch(entry.query, data.total_results, entry.sourceTypes);
        },
      }
    );

    window.history.replaceState({}, "");
  }, [location.state]);

  const handleSearch = (query: string) => {
    setLastQuery(query);
    setSavedSynthesis(null);
    searchMutation.mutate(
      {
        query,
        source_types: filters.sourceTypes.length === 3 ? undefined : filters.sourceTypes,
        top_k: filters.topK,
        min_confidence: filters.minConfidence || undefined,
      },
      {
        onSuccess: (data) => {
          addSearch(query, data.total_results, filters.sourceTypes);
        },
      }
    );
  };

  return (
    <div className="space-y-6">
      <div className="text-center space-y-4">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
          Recherche GraphRAG
        </h1>
        <p className="text-gray-500 dark:text-gray-400">
          Interrogez les textes législatifs, décisions judiciaires et
          documentation fiscale
        </p>
      </div>

      <IntroSection />

      <SearchBar onSearch={handleSearch} isLoading={searchMutation.isPending} initialQuery={lastQuery} />
      <FilterPanel filters={filters} onChange={setFilters} />

      {searchMutation.isError && (
        <div className="p-4 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-xl border border-red-200 dark:border-red-800">
          Erreur: {String(searchMutation.error)}
        </div>
      )}

      {searchMutation.data && (
        <>
          <div className="flex items-center justify-between">
            <div className="text-sm text-gray-500 dark:text-gray-400">
              {searchMutation.data.total_results} résultat(s) trouvé(s)
            </div>
            <button
              onClick={() => exportMarkdown(lastQuery, searchMutation.data!.results, savedSynthesis)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-slate-600 rounded-lg hover:bg-gray-50 dark:hover:bg-slate-700 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              Exporter .md
            </button>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2">
              <ResultsPanel results={searchMutation.data.results} />
            </div>
            <div>
              {savedSynthesis ? (
                <div className="bg-white dark:bg-slate-800 rounded-xl border border-gray-200 dark:border-slate-700 p-4 shadow-sm">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="font-medium text-gray-900 dark:text-white">Synthèse IA</h3>
                    <button
                      onClick={() => setSavedSynthesis(null)}
                      className="px-3 py-1 text-sm text-gray-500 dark:text-gray-400 border border-gray-200 dark:border-slate-600 rounded-lg hover:bg-gray-50 dark:hover:bg-slate-700 transition-colors"
                    >
                      Relancer
                    </button>
                  </div>
                  <div className="prose prose-sm max-w-none text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
                    {savedSynthesis}
                  </div>
                </div>
              ) : (
                <SynthesisPanel
                  query={lastQuery}
                  sourceTypes={filters.sourceTypes}
                  onComplete={(text) => {
                    setSavedSynthesis(text);
                    addSynthesis(lastQuery, text, filters.sourceTypes);
                  }}
                />
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
