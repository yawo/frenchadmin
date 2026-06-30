from fastapi import APIRouter, Depends, HTTPException

from web.dependencies import get_db
from web.models.schemas import DocumentDetail, SourceType
from web.services.vector_search import get_document_by_id

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{source_type}/{doc_id}", response_model=DocumentDetail)
def get_document(source_type: SourceType, doc_id: str, conn=Depends(get_db)):
    doc = get_document_by_id(conn, source_type, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc
