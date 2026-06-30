from fastapi import APIRouter, Depends, HTTPException

from web.dependencies import get_graph
from web.models.schemas import GraphData, GraphNeighborsRequest
from web.services.graph_search import get_document_context, get_neighbors, get_subgraph

router = APIRouter(prefix="/graph", tags=["graph"])


@router.post("/neighbors", response_model=GraphData)
def neighbors(request: GraphNeighborsRequest, graph=Depends(get_graph)):
    return get_neighbors(graph, request.doc_id, request.hops, request.relation_types, request.limit)


@router.get("/subgraph", response_model=GraphData)
def subgraph(doc_ids: str, graph=Depends(get_graph)):
    ids = [d.strip() for d in doc_ids.split(",") if d.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="doc_ids parameter required")
    return get_subgraph(graph, ids)


@router.get("/context/{doc_id}", response_model=GraphData)
def document_graph_context(doc_id: str, graph=Depends(get_graph)):
    return get_document_context(graph, doc_id)
