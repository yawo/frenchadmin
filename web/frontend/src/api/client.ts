import type {
  CrossRefListResponse,
  DocumentDetail,
  GraphData,
  SearchRequest,
  SearchResponse,
  SourceType,
  SynthesisRequest,
} from "../types";

const BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

export function search(params: SearchRequest): Promise<SearchResponse> {
  return request("/search", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function getGraphNeighbors(
  docId: string,
  hops = 1,
  relationTypes?: string[]
): Promise<GraphData> {
  return request("/graph/neighbors", {
    method: "POST",
    body: JSON.stringify({
      doc_id: docId,
      hops,
      relation_types: relationTypes,
    }),
  });
}

export function getGraphContext(docId: string): Promise<GraphData> {
  return request(`/graph/context/${docId}`);
}

export function getDocument(
  sourceType: SourceType,
  docId: string
): Promise<DocumentDetail> {
  return request(`/documents/${sourceType}/${docId}`);
}

export function getCrossRefs(params: {
  source_type?: SourceType;
  target_doc_id?: string;
  source_doc_id?: string;
  min_confidence?: number;
  page?: number;
  page_size?: number;
}): Promise<CrossRefListResponse> {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      searchParams.set(key, String(value));
    }
  }
  return request(`/crossrefs?${searchParams.toString()}`);
}

export async function* streamSynthesis(
  params: SynthesisRequest
): AsyncGenerator<string> {
  const res = await fetch(`${BASE}/synthesize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });

  if (!res.ok) {
    throw new Error(`Synthesis error ${res.status}`);
  }

  const reader = res.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data:")) {
        const data = line.slice(5).trim();
        if (!data) continue;
        try {
          const parsed = JSON.parse(data);
          if (parsed.content) {
            yield parsed.content;
          }
        } catch {
          // skip malformed SSE
        }
      }
    }
  }
}
