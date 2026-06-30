import { useState } from "react";

const STORAGE_KEY = "frenchadmin_intro_collapsed";

export default function IntroSection() {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(STORAGE_KEY) === "true"
  );

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(STORAGE_KEY, String(next));
  };

  return (
    <div className="rounded-xl border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-950/30 overflow-hidden">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between px-5 py-3 text-left hover:bg-blue-100/50 dark:hover:bg-blue-900/30 transition-colors"
      >
        <span className="font-medium text-blue-900 dark:text-blue-200 text-sm">
          A propos de cet outil
        </span>
        <svg
          className={`w-4 h-4 text-blue-600 dark:text-blue-400 transition-transform ${collapsed ? "" : "rotate-180"}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {!collapsed && (
        <div className="px-5 pb-4 text-sm text-gray-700 dark:text-gray-300 space-y-3 border-t border-blue-100 dark:border-blue-800 pt-3">
          <p>
            Ce système GraphRAG (Retrieval-Augmented Generation enrichi par graphe)
            est construit sur trois corpus du droit fiscal français :
          </p>
          <ul className="space-y-2 ml-1">
            <li className="flex gap-2">
              <span className="inline-block w-2 h-2 mt-1.5 rounded-full bg-legi flex-shrink-0" />
              <span>
                <strong>CGI &amp; Annexes</strong> (LEGI) — Code Général des Impôts et ses annexes,
                textes législatifs et réglementaires en vigueur
              </span>
            </li>
            <li className="flex gap-2">
              <span className="inline-block w-2 h-2 mt-1.5 rounded-full bg-bofip flex-shrink-0" />
              <span>
                <strong>Doctrine administrative</strong> (BOFiP) — Bulletin Officiel des Finances
                Publiques, instructions et commentaires de l&apos;administration fiscale
              </span>
            </li>
            <li className="flex gap-2">
              <span className="inline-block w-2 h-2 mt-1.5 rounded-full bg-jade flex-shrink-0" />
              <span>
                <strong>Jurisprudence</strong> (JADE) — Décisions de justice en matière fiscale
                (Conseil d&apos;État, cours administratives d&apos;appel, tribunaux)
              </span>
            </li>
          </ul>
          <p className="text-gray-500 dark:text-gray-400">
            Les documents sont indexés par embeddings vectoriels et reliés par un graphe de
            connaissances capturant les références croisées entre sources.
          </p>
        </div>
      )}
    </div>
  );
}
