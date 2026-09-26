"""Document upload and management endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user, require_user
from app.core.config import settings
from app.core.exceptions import ForbiddenError
from app.core.limits import limiter
from app.db.models import User
from app.db.repositories.document import DocumentChunkRepository, DocumentRepository
from app.db.session import get_db
from app.schemas.common import envelope
from app.schemas.documents import (
    BulkUploadFailure,
    BulkUploadResultOut,
    DocumentSummaryOut,
)
from app.services import documents as documents_service

router = APIRouter(prefix="/documents", tags=["documents"])


def _summary(document: Any, chunk_count: int, *, is_owner: bool = True) -> DocumentSummaryOut:
    return DocumentSummaryOut(
        id=document.id,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        status=document.status,
        chunk_count=chunk_count,
        error_message=document.error_message,
        created_at=document.created_at,
        is_owner=is_owner,
    )


def _is_admin(user: User) -> bool:
    return any(role.name == "admin" for role in user.roles)


@router.get("", response_model=dict[str, Any])
async def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    docs, total = await DocumentRepository(db).list_all(page=page, page_size=page_size)
    chunk_repo = DocumentChunkRepository(db)
    items = [
        _summary(doc, await chunk_repo.count_for_document(doc.id), is_owner=doc.owner_id == current_user.id)
        for doc in docs
    ]
    return envelope(
        {
            "items": [item.model_dump(mode="json") for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if page_size else 0,
        }
    )


@router.post("", response_model=dict[str, Any])
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await documents_service.save_upload(
        db,
        owner_id=current_user.id,
        upload=file,
    )
    row = _summary(result.document, result.chunk_count)
    await db.commit()
    await db.refresh(result.document)
    return envelope(row.model_dump(mode="json"))


@router.post("/bulk", response_model=dict[str, Any])
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def upload_documents_bulk(
    request: Request,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ingest many documents in one request (the chat composer drop zone).

    Reports per-file outcomes so a single unsupported file does not discard the
    rest of the batch.
    """
    result = await documents_service.save_uploads(db, owner_id=current_user.id, uploads=files)
    payload = BulkUploadResultOut(
        items=[_summary(entry.document, entry.chunk_count) for entry in result.documents],
        failed=[BulkUploadFailure(filename=name, error=message) for name, message in result.failures],
    )
    await db.commit()
    return envelope(payload.model_dump(mode="json"))


@router.delete("/{document_id}", response_model=dict[str, Any])
async def delete_document(
    document_id: str,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    document_uuid = uuid.UUID(document_id)
    owner_id: uuid.UUID | None = None
    if not _is_admin(current_user):
        owner = await DocumentRepository(db).get(document_uuid)
        if owner is not None and owner.owner_id != current_user.id:
            raise ForbiddenError("You can only delete documents you uploaded")
        owner_id = current_user.id
    await documents_service.delete_document(db, document_id=document_uuid, owner_id=owner_id)
    return envelope({"status": "ok"})
