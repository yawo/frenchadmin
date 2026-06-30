from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    legi = "legi"
    jade = "jade"
    bofip = "bofip"


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    source_types: list[SourceType] | None = None
    top_k: int = Field(default=10, ge=1, le=100)
    date_start: date | None = None
    date_end: date | None = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ChunkResult(BaseModel):
    doc_id: str
    chunk_id: str
    source_type: SourceType
    title: str | None = None
    chunk_text: str
    similarity: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphNode(BaseModel):
    id: str
    label: str
    doc_id: str | None = None
    name: str | None = None
    title: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphData(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class SearchResponse(BaseModel):
    results: list[ChunkResult]
    graph: GraphData | None = None
    total_results: int


class GraphNeighborsRequest(BaseModel):
    doc_id: str
    hops: int = Field(default=1, ge=1, le=3)
    relation_types: list[str] | None = None
    limit: int = Field(default=50, ge=1, le=200)


class CrossReference(BaseModel):
    source_type: SourceType
    source_doc_id: str
    target_legi_doc_id: str
    relation_kind: str
    best_confidence: float
    occurrence_count: int
    resolver_methods: list[str]
    source_chunk_ids: list[str]
    normalized_numbers: list[str]
    source_date: date | None = None
    explain: dict[str, Any] | None = None


class CrossRefListResponse(BaseModel):
    items: list[CrossReference]
    total: int
    page: int
    page_size: int


class DocumentDetail(BaseModel):
    doc_id: str
    source_type: SourceType
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunks: list[dict[str, Any]] = Field(default_factory=list)
    cross_references: list[CrossReference] = Field(default_factory=list)


class SynthesisRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    context_doc_ids: list[str] | None = None
    source_types: list[SourceType] | None = None
    top_k: int = Field(default=10, ge=1, le=50)
    max_context_tokens: int = Field(default=6000, ge=500, le=12000)
