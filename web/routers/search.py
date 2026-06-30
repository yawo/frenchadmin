from fastapi import APIRouter, Depends

from web.dependencies import get_db, get_graph_optional
from web.models.schemas import SearchRequest, SearchResponse
from web.services.retrieval import graphrag_search

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
def search(request: SearchRequest, conn=Depends(get_db), graph=Depends(get_graph_optional)):
    return graphrag_search(conn, graph, request)
