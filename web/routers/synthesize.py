from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from web.dependencies import get_db, get_graph_optional
from web.models.schemas import SynthesisRequest
from web.services.synthesis import stream_synthesis

router = APIRouter(tags=["synthesize"])


@router.post("/synthesize")
async def synthesize(request: SynthesisRequest, conn=Depends(get_db), graph=Depends(get_graph_optional)):
    return EventSourceResponse(stream_synthesis(conn, graph, request))
