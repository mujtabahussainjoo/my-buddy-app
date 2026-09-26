"""Document schemas: upload summaries and RAG retrieval items."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DocumentSummaryOut(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str | None
    size_bytes: int
    status: str  # uploaded|processing|ready|failed|deleted
    chunk_count: int = 0
    error_message: str | None = None
    created_at: datetime
    is_owner: bool = False

    model_config = {"from_attributes": True}


class DocumentListOut(BaseModel):
    items: list[DocumentSummaryOut]
    page: int = Field(default=1)
    page_size: int = Field(default=20)
    total: int
    total_pages: int


class RAGRetrievalItem(BaseModel):
    document_id: uuid.UUID
    chunk_index: int
    content: str
    score: float = Field(default=0.0)
    filename: str | None = None

    model_config = {"from_attributes": True}


class BulkUploadFailure(BaseModel):
    filename: str
    error: str


class BulkUploadResultOut(BaseModel):
    items: list[DocumentSummaryOut] = Field(default_factory=list)
    failed: list[BulkUploadFailure] = Field(default_factory=list)
