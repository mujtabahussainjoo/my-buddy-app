"""Document repositories: CRUD for uploaded files and their chunks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import NotFoundError
from app.db.models import Document, DocumentChunk

from .base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    async def get_owned(self, owner_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        document = await self.get(document_id)
        if document is None or document.owner_id != owner_id or document.deleted_at is not None:
            raise NotFoundError("Document not found")
        return document

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Document], int]:
        filters = [Document.owner_id == owner_id, Document.deleted_at.is_(None)]
        items, total = await self.paginate(
            filters=filters,
            sort_by="created_at",
            sort_order="desc",
            page=page,
            page_size=page_size,
        )
        return items, total

    async def list_all(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Document], int]:
        """Shared knowledge-base listing: every non-deleted document, newest first."""
        filters = [Document.deleted_at.is_(None)]
        items, total = await self.paginate(
            filters=filters,
            sort_by="created_at",
            sort_order="desc",
            page=page,
            page_size=page_size,
        )
        return items, total

    async def list_by_ids(
        self,
        document_ids: list[uuid.UUID],
        *,
        only_ready: bool = False,
    ) -> list[Document]:
        """Fetch specific non-deleted documents, e.g. the ids attached to a chat message."""
        if not document_ids:
            return []
        filters: list[ColumnElement[bool]] = [
            Document.id.in_(document_ids),
            Document.deleted_at.is_(None),
        ]
        if only_ready:
            filters.append(Document.status == "ready")
        stmt = select(Document).where(*filters)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(self, document: Document) -> None:
        document.deleted_at = datetime.now(UTC)
        document.status = "deleted"
        await self.session.flush()


class DocumentChunkRepository(BaseRepository[DocumentChunk]):
    model = DocumentChunk

    async def count_for_document(self, document_id: uuid.UUID) -> int:
        stmt = select(DocumentChunk).where(DocumentChunk.document_id == document_id)
        return await self._count(stmt)

    async def all_for_document(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def chunks_for_documents(self, document_ids: list[uuid.UUID]) -> list[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id.in_(document_ids))
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def delete_for_document(self, document_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
