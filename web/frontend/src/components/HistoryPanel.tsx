import { useHistory } from "../hooks/useHistory";
import type { HistoryEntry } from "../types";

interface Props {
  open: boolean;
  onClose: () => void;
  onSelect: (entry: HistoryEntry) => void;
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp);
  return date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function groupByDate(entries: HistoryEntry[]) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const yesterday = today - 86400000;
  const weekAgo = today - 604800000;

  const groups: { label: string; entries: HistoryEntry[] }[] = [
    { label: "Aujourd'hui", entries: [] },
    { label: "Hier", entries: [] },
    { label: "Cette semaine", entries: [] },
    { label: "Plus ancien", entries: [] },
  ];

  for (const entry of entries) {
    if (entry.timestamp >= today) groups[0].entries.push(entry);
    else if (entry.timestamp >= yesterday) groups[1].entries.push(entry);
    else if (entry.timestamp >= weekAgo) groups[2].entries.push(entry);
    else groups[3].entries.push(entry);
  }

  return groups.filter((g) => g.entries.length > 0);
}

export default function HistoryPanel({ open, onClose, onSelect }: Props) {
  const { entries, remove, clearAll } = useHistory();

  if (!open) return null;

  const groups = groupByDate(entries);

  return (
    <>
      <div className="fixed inset-0 bg-black/20 dark:bg-black/40 z-40" onClick={onClose} />
      <aside className="fixed right-0 top-0 h-full w-80 bg-white dark:bg-slate-800 shadow-xl z-50 flex flex-col border-l border-gray-200 dark:border-slate-700 animate-slide-in">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-slate-700">
          <h2 className="font-semibold text-gray-900 dark:text-white">Historique</h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-100 dark:hover:bg-slate-700 text-gray-500 dark:text-gray-400"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-2">
          {entries.length === 0 ? (
            <p className="text-sm text-gray-400 dark:text-gray-500 text-center mt-8">
              Aucun historique pour le moment.
            </p>
          ) : (
            groups.map((group) => (
              <div key={group.label} className="mb-4">
                <h3 className="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wider px-1 mb-1">
                  {group.label}
                </h3>
                <div className="space-y-1">
                  {group.entries.map((entry) => (
                    <div
                      key={entry.id}
                      className="group flex items-start gap-2 px-2 py-2 rounded-lg hover:bg-gray-50 dark:hover:bg-slate-700/50 cursor-pointer transition-colors"
                      onClick={() => onSelect(entry)}
                    >
                      <span className="mt-0.5 flex-shrink-0">
                        {entry.type === "search" ? (
                          <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                          </svg>
                        ) : (
                          <svg className="w-4 h-4 text-purple-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                          </svg>
                        )}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-gray-800 dark:text-gray-200 truncate">
                          {entry.query}
                        </p>
                        <p className="text-xs text-gray-400 dark:text-gray-500">
                          {formatTime(entry.timestamp)}
                          {entry.resultCount !== undefined && ` • ${entry.resultCount} résultats`}
                        </p>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          remove(entry.id);
                        }}
                        className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-50 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-500 transition-all"
                      >
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>

        {entries.length > 0 && (
          <div className="px-4 py-3 border-t border-gray-200 dark:border-slate-700">
            <button
              onClick={clearAll}
              className="w-full text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 py-2 rounded-lg transition-colors"
            >
              Effacer tout l&apos;historique
            </button>
          </div>
        )}
      </aside>
    </>
  );
}
