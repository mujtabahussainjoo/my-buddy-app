"""Document processing service: save uploads, extract text, chunk, and ingest.

Files are stored under ``<UPLOAD_DIR>/<owner_id>/`` so that every user's
uploads live in a dedicated folder inside the app project.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError, PayloadTooLargeError, ValidationError
from app.core.logging import logger
from app.db.models import Document, DocumentChunk

# content types accepted for upload. ``.doc`` (legacy binary) is accepted for
# storage even though text extraction may not be possible; ``.docx`` extracts fine.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# Types we can turn into searchable text chunks.
TEXT_EXTRACTABLE = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
}

_EXTENSION_TO_TYPE = {ext: ctype for ctype, ext in ALLOWED_CONTENT_TYPES.items() if ctype in TEXT_EXTRACTABLE}


@dataclass
class IngestionResult:
    document: Document
    chunk_count: int


@dataclass
class BulkUploadResult:
    """Outcome of a multi-file upload: successes plus per-file failures."""

    documents: list[IngestionResult]
    failures: list[tuple[str, str]]


def _resolve_upload_root() -> Path:
    return (Path(settings.UPLOAD_DIR)).resolve()


def _chunk_text(text: str, *, size: int | None = None, overlap: int | None = None) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    size = size or settings.CHUNK_SIZE
    overlap = overlap if overlap is not None else settings.CHUNK_OVERLAP
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _extract_text(content_type: str, file_path: Path) -> str:
    """Best-effort text extraction. Returns '' for unsupported/unreadable files."""
    try:
        if content_type == "application/pdf":
            from pypdf import PdfReader

            reader = PdfReader(file_path)
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if content_type == "application/msword":
            # Legacy binary .doc cannot be parsed by python-docx; treat as empty.
            return ""
        if content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            from docx import Document as DocxDocument

            doc = DocxDocument(file_path)
            return "\n".join(p.text for p in doc.paragraphs)
        if content_type in {"text/plain", "text/markdown", "text/csv", "application/json"}:
            return file_path.read_text(errors="replace")
    except Exception:
        logger.warning(
            "document_extract_failed",
            extra={"extra_fields": {"content_type": content_type, "path": str(file_path)}},
        )
        return ""
    return ""


async def save_upload(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    upload: UploadFile,
    content_type: str | None = None,
) -> IngestionResult:
    """Persist an uploaded file to disk, extract text, and create chunk rows."""
    ctype = content_type or upload.content_type or ""
    if ctype not in ALLOWED_CONTENT_TYPES:
        filename = (upload.filename or "").lower()
        for ext, mapped in _EXTENSION_TO_TYPE.items():
            if filename.endswith(ext):
                ctype = mapped
                break
        else:
            raise ValidationError(
                f"Unsupported file type: {ctype or upload.filename or 'unknown'}. "
                "Allowed: " + ", ".join(sorted(ALLOWED_CONTENT_TYPES))
            )

    raw = await upload.read()
    if len(raw) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise PayloadTooLargeError(
            f"File exceeds the {settings.MAX_UPLOAD_MB} MB upload limit"
        )

    ext = Path(upload.filename or "upload").suffix.lower()
    file_id = uuid.uuid4()
    owner_dir = _resolve_upload_root() / str(owner_id)
    owner_dir.mkdir(parents=True, exist_ok=True)
    storage_path = owner_dir / f"{file_id}{ext}"
    storage_path.write_bytes(raw)

    try:
        checksum = hashlib.sha256(raw).hexdigest()
        document = Document(
            owner_id=owner_id,
            filename=upload.filename or f"upload{ext}",
            content_type=ctype,
            size_bytes=len(raw),
            status="processing",
            storage_path=str(storage_path),
            checksum=checksum,
        )
        session.add(document)
        await session.flush()

        chunk_count = 0
        if ctype in TEXT_EXTRACTABLE:
            text = _extract_text(ctype, storage_path)
            chunks = _chunk_text(text)
            for index, chunk in enumerate(chunks):
                session.add(
                    DocumentChunk(
                        document_id=document.id,
                        chunk_index=index,
                        content=chunk,
                        token_count=max(1, len(chunk) // 4),
                    )
                )
                chunk_count += 1
            document.status = "ready"
        else:
            # Images and binaries: stored/indexed by filename + metadata only.
            document.status = "ready"

        await session.flush()
    except Exception:
        await asyncio.to_thread(storage_path.unlink, missing_ok=True)
        raise
    return IngestionResult(document=document, chunk_count=chunk_count)


async def save_uploads(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    uploads: Sequence[UploadFile],
) -> BulkUploadResult:
    """Ingest several files in one request.

    Each file is ingested inside its own savepoint so one bad file (wrong type,
    too large, unreadable) never discards the files already stored next to it.
    """
    limit = settings.MAX_UPLOAD_FILES
    accepted = list(uploads)
    documents: list[IngestionResult] = []
    failures: list[tuple[str, str]] = []

    for upload in accepted[:limit]:
        name = upload.filename or "upload"
        try:
            async with session.begin_nested():
                documents.append(await save_upload(session, owner_id=owner_id, upload=upload))
        except AppError as exc:
            failures.append((name, exc.message))
        except Exception as exc:  # noqa: BLE001 - one bad file must not fail the batch
            logger.warning(
                "document_upload_failed",
                extra={"extra_fields": {"filename": name, "error": str(exc)}},
            )
            failures.append((name, f"Upload failed: {exc}"))

    for upload in accepted[limit:]:
        failures.append(
            (upload.filename or "upload", f"At most {limit} files can be uploaded at once")
        )
    return BulkUploadResult(documents=documents, failures=failures)


async def reingest_document(session: AsyncSession, document: Document) -> IngestionResult:
    """Re-run chunking for an existing document from its file on disk.

    Used by the standalone ingestion script to rebuild missing/outdated chunks.
    """
    from app.db.repositories.document import DocumentChunkRepository

    await DocumentChunkRepository(session).delete_for_document(document.id)
    chunk_count = 0
    document.status = "processing"
    await session.flush()
    if document.content_type in TEXT_EXTRACTABLE:
        storage = Path(document.storage_path)
        text = _extract_text(document.content_type, storage) if storage.exists() else ""
        chunks = _chunk_text(text)
        for index, chunk in enumerate(chunks):
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    chunk_index=index,
                    content=chunk,
                    token_count=max(1, len(chunk) // 4),
                )
            )
            chunk_count += 1
    document.status = "ready"
    await session.flush()
    return IngestionResult(document=document, chunk_count=chunk_count)


async def delete_document(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    owner_id: uuid.UUID | None = None,
) -> None:
    """Soft-delete a document row and remove its file from disk.

    ``owner_id`` restricts the deletion to the uploader; pass ``None`` (admins)
    to allow deleting any document.
    """
    from app.db.repositories.document import DocumentChunkRepository, DocumentRepository

    repo = DocumentRepository(session)
    document = await repo.get(document_id)
    if document is None or document.deleted_at is not None:
        raise NotFoundError("Document not found")
    if owner_id is not None and document.owner_id != owner_id:
        raise NotFoundError("Document not found")
    await repo.soft_delete(document)
    await DocumentChunkRepository(session).delete_for_document(document.id)

    storage = Path(document.storage_path)
    try:
        if storage.exists() and storage.is_file():
            storage.unlink()
    except OSError:
        logger.warning(
            "document_delete_file_failed",
            extra={"extra_fields": {"path": document.storage_path, "document_id": str(document.id)}},
        )
    await session.commit()
