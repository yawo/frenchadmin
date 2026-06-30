from fastapi import APIRouter, Depends, Query

from web.dependencies import get_db
from web.models.schemas import CrossRefListResponse, SourceType
from web.services.vector_search import get_cross_references

router = APIRouter(prefix="/crossrefs", tags=["crossrefs"])


@router.get("", response_model=CrossRefListResponse)
def list_crossrefs(
    source_type: SourceType | None = None,
    target_doc_id: str | None = None,
    source_doc_id: str | None = None,
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    conn=Depends(get_db),
):
    return get_cross_references(
        conn,
        source_type=source_type,
        target_doc_id=target_doc_id,
        source_doc_id=source_doc_id,
        min_confidence=min_confidence,
        page=page,
        page_size=page_size,
    )
