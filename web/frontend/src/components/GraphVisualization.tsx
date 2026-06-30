import { useCallback, useEffect, useRef, useState } from "react";
import type { GraphData, GraphNode } from "../types";
import { getGraphNeighbors } from "../api/client";

const LABEL_COLORS: Record<string, string> = {
  LegalText: "#2563eb",
  JudicialDecision: "#ea580c",
  TaxGuidance: "#16a34a",
  LegalCode: "#7c3aed",
  Ministry: "#6b7280",
  Court: "#dc2626",
  TaxCode: "#0d9488",
};

interface ForceGraphNode {
  id: string;
  label: string;
  name: string;
  color: string;
  x?: number;
  y?: number;
}

interface ForceGraphLink {
  source: string;
  target: string;
  relation: string;
}

export default function GraphVisualization() {
  const [docId, setDocId] = useState("");
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<unknown>(null);

  const loadGraph = async () => {
    if (!docId.trim()) return;
    setLoading(true);
    try {
      const data = await getGraphNeighbors(docId.trim(), 2);
      setGraphData(data);
    } catch {
      setGraphData(null);
    } finally {
      setLoading(false);
    }
  };

  const nodes: ForceGraphNode[] = (graphData?.nodes || []).map((n) => ({
    id: n.id,
    label: n.label,
    name: n.title || n.name || n.doc_id || n.id,
    color: LABEL_COLORS[n.label] || "#6b7280",
  }));

  const links: ForceGraphLink[] = (graphData?.edges || []).map((e) => ({
    source: e.source,
    target: e.target,
    relation: e.relation,
  }));

  const handleNodeClick = useCallback(
    (node: ForceGraphNode) => {
      const original = graphData?.nodes.find((n) => n.id === node.id);
      setSelectedNode(original || null);
    },
    [graphData]
  );

  const [ForceGraph, setForceGraph] = useState<React.ComponentType<Record<string, unknown>> | null>(null);

  useEffect(() => {
    import("react-force-graph-2d").then((mod) => {
      setForceGraph(() => mod.default);
    });
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <input
          type="text"
          value={docId}
          onChange={(e) => setDocId(e.target.value)}
          placeholder="Entrez un doc_id pour explorer le graphe..."
          className="flex-1 px-4 py-2 rounded-lg border border-gray-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-200 outline-none"
          onKeyDown={(e) => e.key === "Enter" && loadGraph()}
        />
        <button
          onClick={loadGraph}
          disabled={loading}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg disabled:opacity-50 hover:bg-blue-700"
        >
          {loading ? "..." : "Explorer"}
        </button>
      </div>

      <div className="flex gap-2 flex-wrap">
        {Object.entries(LABEL_COLORS).map(([label, color]) => (
          <span
            key={label}
            className="flex items-center gap-1 text-xs text-gray-600"
          >
            <span
              className="w-3 h-3 rounded-full inline-block"
              style={{ backgroundColor: color }}
            />
            {label}
          </span>
        ))}
      </div>

      <div
        ref={containerRef}
        className="bg-white rounded-xl border border-gray-200 overflow-hidden"
        style={{ height: "500px" }}
      >
        {ForceGraph && nodes.length > 0 ? (
          <ForceGraph
            ref={graphRef}
            graphData={{ nodes, links }}
            nodeLabel="name"
            nodeColor="color"
            nodeRelSize={6}
            linkDirectionalArrowLength={4}
            linkLabel="relation"
            linkColor={() => "#d1d5db"}
            onNodeClick={handleNodeClick}
            width={containerRef.current?.clientWidth || 800}
            height={500}
          />
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400">
            {graphData && nodes.length === 0
              ? "Aucun noeud trouvé pour ce document."
              : "Entrez un doc_id et cliquez Explorer pour visualiser le graphe."}
          </div>
        )}
      </div>

      {selectedNode && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="font-medium text-gray-900 mb-2">
            {selectedNode.title || selectedNode.name || selectedNode.id}
          </h3>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <dt className="text-gray-500">Type</dt>
            <dd>{selectedNode.label}</dd>
            {selectedNode.doc_id && (
              <>
                <dt className="text-gray-500">doc_id</dt>
                <dd className="font-mono text-xs">{selectedNode.doc_id}</dd>
              </>
            )}
            {Object.entries(selectedNode.properties)
              .filter(([k]) => !["doc_id", "title", "name"].includes(k))
              .slice(0, 8)
              .map(([key, value]) => (
                <div key={key} className="contents">
                  <dt className="text-gray-500">{key}</dt>
                  <dd className="truncate">{String(value)}</dd>
                </div>
              ))}
          </dl>
        </div>
      )}
    </div>
  );
}
