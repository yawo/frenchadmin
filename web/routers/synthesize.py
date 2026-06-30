from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from web.auth.dependencies import get_current_user
from web.auth.security import decrypt_api_key
from web.dependencies import get_db, get_graph_optional
from web.models.schemas import SynthesisRequest
from web.services.synthesis import stream_synthesis_sse

router = APIRouter(tags=["synthesize"])


@router.post("/synthesize")
async def synthesize(
    request: SynthesisRequest,
    conn=Depends(get_db),
    graph=Depends(get_graph_optional),
    user=Depends(get_current_user),
):
    llm_config = None
    if user and user.get("llm_api_key_encrypted"):
        llm_config = {
            "model": user.get("llm_model"),
            "base_url": user.get("llm_base_url"),
            "api_key": decrypt_api_key(user["llm_api_key_encrypted"]),
        }

    return StreamingResponse(
        stream_synthesis_sse(conn, graph, request, llm_config=llm_config),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
