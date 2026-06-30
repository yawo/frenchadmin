export type SourceType = "legi" | "jade" | "bofip";

export interface ChunkResult {
  doc_id: string;
  chunk_id: string;
  source_type: SourceType;
  title: string | null;
  chunk_text: string;
  similarity: number;
  metadata: Record<string, unknown>;
}

export interface GraphNode {
  id: string;
  label: string;
  doc_id: string | null;
  name: string | null;
  title: string | null;
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  properties: Record<string, unknown>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface SearchResponse {
  results: ChunkResult[];
  graph: GraphData | null;
  total_results: number;
}

export interface CrossReference {
  source_type: SourceType;
  source_doc_id: string;
  target_legi_doc_id: string;
  relation_kind: string;
  best_confidence: number;
  occurrence_count: number;
  resolver_methods: string[];
  source_chunk_ids: string[];
  normalized_numbers: string[];
  source_date: string | null;
  explain: Record<string, unknown> | null;
}

export interface CrossRefListResponse {
  items: CrossReference[];
  total: number;
  page: number;
  page_size: number;
}

export interface DocumentDetail {
  doc_id: string;
  source_type: SourceType;
  title: string | null;
  metadata: Record<string, unknown>;
  chunks: Array<{ chunk_id: string; chunk_index: number; chunk_text: string }>;
  cross_references: CrossReference[];
}

export interface SearchRequest {
  query: string;
  source_types?: SourceType[];
  top_k?: number;
  date_start?: string;
  date_end?: string;
  min_confidence?: number;
}

export interface SynthesisRequest {
  query: string;
  source_types?: SourceType[];
  top_k?: number;
  max_context_tokens?: number;
}
