import { useState } from "react";
import type { SourceType } from "../types";
import { useSearch } from "../hooks/useSearch";
import FilterPanel, { type Filters } from "./FilterPanel";
import ResultsPanel from "./ResultsPanel";
import SearchBar from "./SearchBar";
import SynthesisPanel from "./SynthesisPanel";

export default function SearchPage() {
  const [lastQuery, setLastQuery] = useState("");
  const [filters, setFilters] = useState<Filters>({
    sourceTypes: ["legi", "jade", "bofip"] as SourceType[],
    minConfidence: 0,
    topK: 10,
  });

  const searchMutation = useSearch();

  const handleSearch = (query: string) => {
    setLastQuery(query);
    searchMutation.mutate({
      query,
      source_types: filters.sourceTypes.length === 3 ? undefined : filters.sourceTypes,
      top_k: filters.topK,
      min_confidence: filters.minConfidence || undefined,
    });
  };

  return (
    <div className="space-y-6">
      <div className="text-center space-y-4">
        <h1 className="text-2xl font-bold text-gray-900">
          Recherche GraphRAG
        </h1>
        <p className="text-gray-500">
          Interrogez les textes législatifs, décisions judiciaires et
          documentation fiscale
        </p>
      </div>

      <SearchBar onSearch={handleSearch} isLoading={searchMutation.isPending} />
      <FilterPanel filters={filters} onChange={setFilters} />

      {searchMutation.isError && (
        <div className="p-4 bg-red-50 text-red-700 rounded-lg">
          Erreur: {String(searchMutation.error)}
        </div>
      )}

      {searchMutation.data && (
        <>
          <div className="text-sm text-gray-500">
            {searchMutation.data.total_results} résultat(s) trouvé(s)
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2">
              <ResultsPanel results={searchMutation.data.results} />
            </div>
            <div>
              <SynthesisPanel query={lastQuery} sourceTypes={filters.sourceTypes} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
