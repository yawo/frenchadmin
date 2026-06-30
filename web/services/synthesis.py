from __future__ import annotations

import json
from typing import AsyncGenerator

from openai import OpenAI

from config import API_KEY, API_URL, LLM_MODEL
from web.models.schemas import SearchRequest, SourceType, SynthesisRequest
from web.services.retrieval import graphrag_search

SYSTEM_PROMPT = """Tu es un assistant juridique spécialisé en droit fiscal français.
Tu réponds aux questions en te basant UNIQUEMENT sur le contexte fourni.
Cite toujours tes sources en référençant les identifiants des documents (doc_id) et les numéros d'articles.
Si le contexte ne contient pas suffisamment d'informations pour répondre, dis-le clairement.
Réponds en français."""


def _get_client() -> OpenAI:
    return OpenAI(base_url=API_URL, api_key=API_KEY)


def _build_context(conn, graph, request: SynthesisRequest) -> str:
    """Retrieve and format context for LLM synthesis."""
    search_req = SearchRequest(
        query=request.query,
        source_types=request.source_types,
        top_k=request.top_k,
    )
    response = graphrag_search(conn, graph, search_req)

    context_parts = []
    token_estimate = 0
    max_tokens = request.max_context_tokens

    for result in response.results:
        chunk_text = result.chunk_text
        entry = f"[{result.source_type.value.upper()}] doc_id={result.doc_id}"
        if result.title:
            entry += f" | {result.title}"
        entry += f" (similarité: {result.similarity:.2f})\n{chunk_text}\n"

        entry_tokens = len(entry) // 4
        if token_estimate + entry_tokens > max_tokens:
            break
        context_parts.append(entry)
        token_estimate += entry_tokens

    return "\n---\n".join(context_parts)


async def stream_synthesis(conn, graph, request: SynthesisRequest) -> AsyncGenerator[dict, None]:
    """Stream LLM synthesis response as SSE events."""
    context = _build_context(conn, graph, request)

    if not context.strip():
        yield {"event": "message", "data": json.dumps({"content": "Aucun document pertinent trouvé pour cette requête."})}
        yield {"event": "done", "data": ""}
        return

    user_message = f"Contexte:\n{context}\n\nQuestion: {request.query}"

    client = _get_client()
    try:
        stream = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            stream=True,
        )

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                yield {"event": "message", "data": json.dumps({"content": content})}

        yield {"event": "done", "data": ""}
    except Exception as e:
        yield {"event": "error", "data": json.dumps({"error": str(e)})}
