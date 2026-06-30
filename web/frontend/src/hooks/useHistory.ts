import { useCallback, useSyncExternalStore } from "react";
import type { HistoryEntry, SourceType } from "../types";

const STORAGE_KEY = "frenchadmin_history";
const MAX_ENTRIES = 50;

let listeners: Array<() => void> = [];
let cachedEntries: HistoryEntry[] | null = null;

function emitChange() {
  cachedEntries = null;
  for (const listener of listeners) listener();
}

function getEntries(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveEntries(entries: HistoryEntry[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)));
  emitChange();
}

function subscribe(listener: () => void) {
  listeners = [...listeners, listener];
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}

function getSnapshot(): HistoryEntry[] {
  if (cachedEntries === null) {
    cachedEntries = getEntries();
  }
  return cachedEntries;
}

export function useHistory() {
  const entries = useSyncExternalStore(subscribe, getSnapshot);

  const addSearch = useCallback(
    (query: string, resultCount: number, sourceTypes?: SourceType[]) => {
      const entry: HistoryEntry = {
        id: crypto.randomUUID(),
        timestamp: Date.now(),
        type: "search",
        query,
        resultCount,
        sourceTypes,
      };
      saveEntries([entry, ...getEntries()]);
    },
    []
  );

  const addSynthesis = useCallback(
    (query: string, synthesisText: string, sourceTypes?: SourceType[]) => {
      const entry: HistoryEntry = {
        id: crypto.randomUUID(),
        timestamp: Date.now(),
        type: "synthesis",
        query,
        synthesisText,
        sourceTypes,
      };
      saveEntries([entry, ...getEntries()]);
    },
    []
  );

  const remove = useCallback((id: string) => {
    saveEntries(getEntries().filter((e) => e.id !== id));
  }, []);

  const clearAll = useCallback(() => {
    saveEntries([]);
  }, []);

  return { entries, addSearch, addSynthesis, remove, clearAll };
}
