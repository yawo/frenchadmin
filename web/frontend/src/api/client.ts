import type {
  CrossRefListResponse,
  DocumentDetail,
  GraphData,
  LLMSettings,
  SearchRequest,
  SearchResponse,
  SourceType,
  SynthesisRequest,
  TokenResponse,
} from "../types";

const BASE = "/api";
const TOKEN_KEY = "frenchadmin_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, options?: RequestInit & { skipAuthRedirect?: boolean }): Promise<T> {
  const { skipAuthRedirect, ...fetchOptions } = options || {};
  const res = await fetch(`${BASE}${path}`, {
    ...fetchOptions,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...fetchOptions?.headers },
  });
  if (res.status === 401 && !skipAuthRedirect) {
    clearToken();
    window.location.href = "/login";
    throw new Error("Session expired");
  }
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
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(params),
  });

  if (res.status === 401) {
    clearToken();
    window.location.href = "/login";
    throw new Error("Session expired");
  }
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
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const data = trimmed.slice(5).trim();
      if (!data || data === "[DONE]") continue;
      try {
        const parsed = JSON.parse(data);
        if (parsed.error) {
          throw new Error(parsed.error);
        }
        if (parsed.content) {
          yield parsed.content;
        }
      } catch (e) {
        if (e instanceof SyntaxError) continue;
        throw e;
      }
    }
  }
}

export function authRegister(username: string, password: string): Promise<TokenResponse> {
  return request("/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password }),
    skipAuthRedirect: true,
  });
}

export function authLogin(username: string, password: string): Promise<TokenResponse> {
  return request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
    skipAuthRedirect: true,
  });
}

export function authMe(): Promise<{ id: string; username: string }> {
  return request("/auth/me", { skipAuthRedirect: true });
}

export function getLLMSettings(): Promise<LLMSettings> {
  return request("/settings/llm");
}

export function updateLLMSettings(data: {
  llm_model?: string | null;
  llm_base_url?: string | null;
  llm_api_key?: string | null;
}): Promise<LLMSettings> {
  return request("/settings/llm", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}
