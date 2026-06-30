# FIXES_1 — Next Session Tasks

## 1. Synthesis Button Does Nothing (Frontend/Backend Integration Bug)

**Symptom:** Clicking "Synthétiser" produces no visible output. The `/api/synthesize` endpoint returns `200 OK` but the response body appears empty to the frontend.

**Root Cause Analysis:**

The backend (`web/routers/synthesize.py`) returns an `EventSourceResponse` (SSE via `sse-starlette`), streaming events from `web/services/synthesis.py:stream_synthesis()`. The generator yields dicts like:
```python
{"event": "message", "data": json.dumps({"content": content})}
```

The frontend (`web/frontend/src/api/client.ts:streamSynthesis()`) reads the response as a raw stream and parses lines starting with `data:`. However, there are two likely failure modes:

1. **Empty context → no LLM call:** If `_build_context()` in `web/services/synthesis.py` returns empty string (no vector search results), the function yields a single "no documents found" message then stops. But the SSE format from `sse-starlette` wraps events differently than what the frontend parser expects. The frontend expects `data:{"content":"..."}` but `sse-starlette` may emit `event: message\ndata: {"content":"..."}\n\n` with the event type on a separate line.

2. **SSE parsing mismatch:** The frontend parser (`client.ts` L102-115) only looks for lines starting with `data:`. But `sse-starlette`'s `EventSourceResponse` emits multi-line SSE blocks including `event:` lines, `id:` lines, and blank separator lines. The `data:` line parsing looks correct but the `JSON.parse` may fail silently on edge cases (the catch block discards errors).

3. **LLM API key not configured:** If `API_KEY` is still `"your_api_key_here"` (default in `config/config.py` L77), the OpenAI client will fail silently or the error event won't render properly.

**Fix Plan:**
- Add `console.log` or check browser DevTools Network tab to see what the SSE stream actually contains
- Verify `API_KEY` and `API_URL` are set in `.env`
- If the SSE format is the issue: either switch the backend to plain streaming response (not EventSourceResponse) since the frontend already does raw stream parsing, OR fix the frontend to use `EventSource` / proper SSE client
- Test with a known-working LLM endpoint to isolate whether the issue is retrieval (empty context) or LLM streaming

**Key Files:**
- `web/routers/synthesize.py` — route handler
- `web/services/synthesis.py` — SSE generator logic
- `web/frontend/src/api/client.ts:streamSynthesis()` — frontend SSE parser
- `web/frontend/src/hooks/useSynthesis.ts` — React state management
- `web/frontend/src/components/SynthesisPanel.tsx` — UI component

---

## 2. Search/Synthesis History with LocalStorage

**Requirement:** Users should be able to see a history of their searches, results, and syntheses. Stored in localStorage with ability to delete individual entries. Presented in an elegant right-side panel.

**Design Spec:**

### Data Model (localStorage)
```typescript
interface HistoryEntry {
  id: string;           // crypto.randomUUID()
  timestamp: number;    // Date.now()
  type: "search" | "synthesis";
  query: string;
  filters?: Filters;
  resultCount?: number;  // for search entries
  synthesisText?: string; // for synthesis entries (store full text)
  sourceTypes?: SourceType[];
}
```

Key: `frenchadmin_history` → JSON array of `HistoryEntry[]`, max ~50 entries (FIFO eviction).

### UI Design
- Right-side collapsible panel (slide-in drawer or always-visible on large screens)
- Grouped by date (Today, Yesterday, This Week, Earlier)
- Each entry shows: query text (truncated), type icon (search vs synthesis), timestamp
- Click to reload/re-display results
- Swipe-to-delete or trash icon on hover
- "Clear All" button at bottom
- Subtle animations for add/remove

### Implementation Plan
1. Create `src/hooks/useHistory.ts` — CRUD operations on localStorage
2. Create `src/components/HistoryPanel.tsx` — right sidebar component
3. Modify `SearchPage.tsx` — integrate history panel, save entries on search/synthesis
4. Modify `useSynthesis.ts` — accept callback to save completed synthesis to history

**Key Files to Create/Modify:**
- `web/frontend/src/hooks/useHistory.ts` (new)
- `web/frontend/src/components/HistoryPanel.tsx` (new)
- `web/frontend/src/components/SearchPage.tsx` (modify layout)
- `web/frontend/src/types/index.ts` (add HistoryEntry type)

---

## 3. Modern UI/UX Overhaul with Light/Dark Theme

**Current State:** Basic Tailwind styling, white backgrounds, minimal visual hierarchy. No dark mode.

**Target:** Modern, elegant, professional legal-tech aesthetic. Clean lines, good typography, subtle depth.

### Design Direction
- **Color palette (light):** White/gray base, blue-600 primary accent, subtle borders
- **Color palette (dark):** Slate-900 background, slate-800 cards, blue-400 accent
- **Typography:** Inter or system font stack, clear hierarchy (text-3xl title, text-lg subtitle, text-sm meta)
- **Cards:** Rounded-xl, subtle shadows (`shadow-sm`), border on light / no border on dark
- **Animations:** Smooth transitions on hover/focus, panel slide-ins, skeleton loading states

### Implementation Plan
1. **Theme system:** Add CSS custom properties (`:root` and `[data-theme="dark"]`) or use Tailwind's `dark:` variant with a class strategy
2. **Theme toggle:** Sun/Moon icon button in the header/nav, persist preference in localStorage
3. **Layout refactor:**
   - Add a proper navigation bar with logo, theme toggle, and history panel toggle
   - Refine spacing, card design, button styles
   - Add skeleton loaders for search results
   - Improve filter panel (collapsible, pill-style source type toggles)
4. **Component restyling:** Update every component to use consistent design tokens

### Tailwind Config Changes
```js
// tailwind.config.js
module.exports = {
  darkMode: 'class', // enable class-based dark mode
  theme: {
    extend: {
      // custom colors, fonts, etc.
    }
  }
}
```

**Key Files to Modify:**
- `web/frontend/tailwind.config.js` — dark mode config
- `web/frontend/src/components/Layout.tsx` — add nav bar, theme toggle
- `web/frontend/src/App.tsx` — theme provider context
- All component files — dark: variants, refined styling
- `web/frontend/src/hooks/useTheme.ts` (new) — theme state + localStorage

---

## 4. Introduction Section (French Tax Law GraphRAG Context)

**Requirement:** Add a concise, informative introduction explaining what this tool is and what data it's built on.

### Content (French)

```
Ce système GraphRAG (Retrieval-Augmented Generation enrichi par graphe) 
est construit sur trois corpus du droit fiscal français :

• **CGI & Annexes** (LEGI) — Code Général des Impôts et ses annexes, 
  textes législatifs et réglementaires en vigueur

• **Doctrine administrative** (BOFiP) — Bulletin Officiel des Finances 
  Publiques, instructions et commentaires de l'administration fiscale

• **Jurisprudence** (JADE) — Décisions de justice en matière fiscale 
  (Conseil d'État, cours administratives d'appel, tribunaux)

Les documents sont indexés par embeddings vectoriels et reliés par un 
graphe de connaissances capturant les références croisées entre sources.
```

### Placement
- Above the search bar on the main page
- Collapsible after first visit (remember in localStorage)
- Subtle card with a left border accent or a light background

**Key Files to Modify:**
- `web/frontend/src/components/SearchPage.tsx` — add intro section above search
- Or create `web/frontend/src/components/IntroSection.tsx` (new)

---

## 5. Local CPU Reranking Model (HuggingFace)

**Requirement:** Add a high-accuracy reranking step to the retrieval pipeline using a local cross-encoder model.

### Model Selection

**Recommended: `cross-encoder/ms-marco-MiniLM-L-12-v2`** 
- Best accuracy/speed tradeoff for CPU
- 33M params, runs well on CPU
- Strong NDCG@10 on MS MARCO

**Alternative (higher accuracy, slower): `BAAI/bge-reranker-v2-m3`**
- Multilingual — excellent for French legal text
- Better semantic understanding across languages
- ~560M params, slower on CPU but very accurate
- **Recommended for this use case** given the French legal domain and accuracy requirement

**Best pick: `BAAI/bge-reranker-v2-m3`** — multilingual cross-encoder, strong on non-English text, high accuracy on legal/domain-specific queries.

### Architecture

```
Query → Vector Search (top_k=30-50) → Reranker (cross-encoder) → top_k=10 final results
```

The current pipeline in `web/services/retrieval.py:graphrag_search()` does:
1. Vector search (pgvector cosine similarity)
2. Graph augmentation (FalkorDB neighbors)
3. Naive merge+sort by similarity score

**New pipeline:**
1. Vector search with **expanded top_k** (3-5x the final desired count) — cast a wider net
2. Graph augmentation (same as before)
3. **Cross-encoder reranking** — score each (query, chunk_text) pair with the cross-encoder
4. Return top_k by reranker score

### Implementation Plan

1. **Create `web/services/reranker.py`:**
```python
from sentence_transformers import CrossEncoder

_model = None

def get_reranker():
    global _model
    if _model is None:
        _model = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512)
    return _model

def rerank(query: str, documents: list[str], top_k: int = 10) -> list[tuple[int, float]]:
    """Returns list of (original_index, score) sorted by score descending."""
    model = get_reranker()
    pairs = [[query, doc] for doc in documents]
    scores = model.predict(pairs)
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]
```

2. **Modify `web/services/retrieval.py`:**
   - Change initial `top_k` for vector search to `request.top_k * 4` (over-retrieve)
   - After graph augmentation merge, call `rerank(query, [r.chunk_text for r in combined])`
   - Map reranker scores back to ChunkResult objects
   - Replace naive similarity sort with reranker ordering

3. **Modify `web/services/synthesis.py`:**
   - The synthesis context builder uses `graphrag_search()` which will now return reranked results — no changes needed

4. **Dependencies:**
   - Already have `sentence-transformers` installed (used for embeddings)
   - May need `pip install --upgrade sentence-transformers` for CrossEncoder support
   - Model downloads ~2.2GB on first use (cache in `~/.cache/huggingface/`)

5. **Performance tuning:**
   - Lazy-load model at first request (cold start ~5-10s)
   - Consider `max_length=512` to limit input tokens (legal chunks can be long — truncate to 512 tokens for reranking)
   - Batch prediction: `model.predict(pairs, batch_size=16)` for throughput
   - Consider ONNX runtime export for 2-3x CPU speedup:
     ```python
     model = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512, 
                          automodel_args={"torch_dtype": "float32"})
     ```

6. **Config additions (`config/config.py`):**
```python
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANKER_MAX_LENGTH = int(os.getenv("RERANKER_MAX_LENGTH", "512"))
RERANKER_BATCH_SIZE = int(os.getenv("RERANKER_BATCH_SIZE", "16"))
RETRIEVAL_OVERSAMPLING_FACTOR = int(os.getenv("RETRIEVAL_OVERSAMPLING_FACTOR", "4"))
```

**Key Files to Create/Modify:**
- `web/services/reranker.py` (new)
- `web/services/retrieval.py` (modify `_rerank` function and `graphrag_search`)
- `config/config.py` (add reranker config)
- `requirements.txt` or `pyproject.toml` (ensure sentence-transformers version supports CrossEncoder)

---

## Priority Order

1. **Fix #1 (Synthesis bug)** — critical, users expect the core feature to work
2. **Fix #5 (Reranker)** — high-impact on result quality, the core value proposition
3. **Fix #4 (Intro section)** — quick win, helps users understand the tool
4. **Fix #3 (UI/UX overhaul)** — significant effort, do after core functionality works
5. **Fix #2 (History)** — nice-to-have, builds on the UI work
