import type { SourceType } from "../types";

interface Filters {
  sourceTypes: SourceType[];
  minConfidence: number;
  topK: number;
}

interface Props {
  filters: Filters;
  onChange: (filters: Filters) => void;
}

const SOURCE_OPTIONS: { value: SourceType; label: string; color: string }[] = [
  { value: "legi", label: "LEGI", color: "bg-legi-light text-legi" },
  { value: "jade", label: "JADE", color: "bg-jade-light text-jade" },
  { value: "bofip", label: "BOFiP", color: "bg-bofip-light text-bofip" },
];

export default function FilterPanel({ filters, onChange }: Props) {
  const toggleSource = (source: SourceType) => {
    const current = filters.sourceTypes;
    const next = current.includes(source)
      ? current.filter((s) => s !== source)
      : [...current, source];
    onChange({ ...filters, sourceTypes: next });
  };

  return (
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <span className="text-gray-500 font-medium">Sources:</span>
      {SOURCE_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          onClick={() => toggleSource(opt.value)}
          className={`px-3 py-1 rounded-full font-medium transition-all ${
            filters.sourceTypes.includes(opt.value)
              ? opt.color
              : "bg-gray-100 text-gray-400"
          }`}
        >
          {opt.label}
        </button>
      ))}
      <div className="ml-4 flex items-center gap-2">
        <label className="text-gray-500">Confiance min:</label>
        <input
          type="range"
          min="0"
          max="100"
          value={filters.minConfidence * 100}
          onChange={(e) =>
            onChange({ ...filters, minConfidence: Number(e.target.value) / 100 })
          }
          className="w-24"
        />
        <span className="text-gray-700 w-10">
          {(filters.minConfidence * 100).toFixed(0)}%
        </span>
      </div>
      <div className="ml-4 flex items-center gap-2">
        <label className="text-gray-500">Résultats:</label>
        <select
          value={filters.topK}
          onChange={(e) => onChange({ ...filters, topK: Number(e.target.value) })}
          className="border border-gray-300 rounded px-2 py-1"
        >
          {[5, 10, 20, 50].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

export type { Filters };
