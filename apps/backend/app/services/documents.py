"""Document processing service: save uploads, extract text, chunk, and ingest.

Files are stored under ``<UPLOAD_DIR>/<owner_id>/`` so that every user's
uploads live in a dedicated folder inside the app project.

Any file type is accepted. The upload's content type is resolved from the
extension (with a UTF-8 sniff as last resort) so source code, notebooks, config
files, markup, logs and plain text of *any* extension end up as searchable
chunks; genuine binaries (images, archives, executables) are still stored and
registered, just without extracted text.
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
from app.core.exceptions import AppError, NotFoundError, PayloadTooLargeError
from app.core.logging import logger
from app.db.models import Document, DocumentChunk

# Content types with a dedicated text extractor.
_RICH_TEXT_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}

_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# Types we can turn into searchable text beyond ``text/*``.
_TEXTUAL_APPLICATION_TYPES = {
    "application/json",
    "application/jsonl",
    "application/x-ndjson",
    "application/x-ipynb+json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/javascript",
    "application/ecmascript",
    "application/x-httpd-php",
    "application/x-sh",
    "application/toml",
    "application/sql",
    "application/graphql",
    "application/x-protobuf",
}

_BINARY_TYPES = {"application/octet-stream", "application/zip", "application/gzip"}

# Extensions we know are plain text even when the browser reports an obscure or
# empty content type. Everything else is sniffed, so this list only needs the
# common cases — it is a fast path, not a gate.
TEXT_EXTENSIONS: dict[str, str] = {
    ext: "text/plain"
    for ext in (
        # docs & data
        ".txt", ".text", ".log", ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".yaml",
        ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".properties", ".xml", ".rst",
        ".tex", ".rtf", ".ipynb", ".diff", ".patch",
        # web
        ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
        # scripting
        ".py", ".pyi", ".rb", ".pl", ".pm", ".php", ".lua", ".tcl", ".sh", ".bash",
        ".zsh", ".fish", ".ps1", ".bat", ".cmd", ".r", ".jl", ".sql", ".graphql",
        ".gql", ".proto", ".tf", ".tfvars",
        # compiled / typed
        ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".kt", ".kts", ".scala",
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".cs", ".go", ".rs",
        ".swift", ".m", ".mm", ".dart", ".groovy", ".gradle", ".ex", ".exs", ".erl",
        ".hrl", ".hs", ".clj", ".cljs", ".elm", ".zig", ".nim", ".vim", ".el", ".f90",
    )
}

_EXTENSION_TO_TYPE: dict[str, str] = (
    {ext: ctype for ctype, ext in {**_RICH_TEXT_TYPES, **_IMAGE_TYPES}.items()}
    | TEXT_EXTENSIONS
    | {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".csv": "text/csv",
        ".tsv": "text/csv",
        ".json": "application/json",
        ".ipynb": "application/x-ipynb+json",
        ".xml": "application/xml",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".cjs": "application/javascript",
        ".php": "application/x-httpd-php",
        ".sh": "application/x-sh",
        ".sql": "application/sql",
        ".graphql": "application/graphql",
    }
)


def _is_text_extractable(content_type: str) -> bool:
    """True when the file's content can be read as searchable text."""
    if content_type in _RICH_TEXT_TYPES:
        return content_type != "application/msword"  # legacy binary .doc
    return content_type.startswith("text/") or content_type in _TEXTUAL_APPLICATION_TYPES


def _sniff_content_type(raw: bytes, filename: str) -> str:
    """Resolve a content type from the extension, falling back to a UTF-8 sniff."""
    suffix = Path(filename or "").suffix.lower()
    known = _EXTENSION_TO_TYPE.get(suffix)
    if known:
        return known
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain"


def _resolve_content_type(raw: bytes, filename: str, reported: str | None) -> str:
    """Extension first (deterministic), then the browser's report, then a sniff.

    Browsers report ``text/plain`` or an empty type for most source files, so the
    extension map is what makes ``.py``/``.ts``/``.sql`` chunkable.
    """
    by_extension = _EXTENSION_TO_TYPE.get(Path(filename or "").suffix.lower())
    if by_extension:
        return by_extension
    normalized = (reported or "").split(";")[0].strip().lower()
    if normalized and normalized not in _BINARY_TYPES:
        if normalized in _IMAGE_TYPES or _is_text_extractable(normalized):
            return normalized
    return _sniff_content_type(raw, filename)



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


def _normalize_whitespace(text: str) -> str:
    """Collapse horizontal runs but keep line structure.

    Flattening every newline makes source code and diffs unreadable in the
    retrieval context, so only spaces/tabs are squeezed.
    """
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" *\n[ \n]*", "\n", text)
    return text.strip()


def _chunk_text(text: str, *, size: int | None = None, overlap: int | None = None) -> list[str]:
    text = _normalize_whitespace(text)
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
    """Best-effort text extraction. Returns '' for binary/unreadable files."""
    try:
        if content_type == "application/pdf":
            from pypdf import PdfReader

            reader = PdfReader(file_path)
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            from docx import Document as DocxDocument

            doc = DocxDocument(file_path)
            return "\n".join(p.text for p in doc.paragraphs)
        if _is_text_extractable(content_type):
            return file_path.read_text(encoding="utf-8", errors="replace")
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
    """Persist an uploaded file to disk, extract text, and create chunk rows.

    Every file type is accepted. Source code, notebooks, markup, config files and
    any other UTF-8 text become searchable chunks; binaries are stored and marked
    ready so they can still be listed and attached, just without text.
    """
    raw = await upload.read()
    if len(raw) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise PayloadTooLargeError(
            f"File exceeds the {settings.MAX_UPLOAD_MB} MB upload limit"
        )

    filename = upload.filename or "upload"
    ctype = _resolve_content_type(raw, filename, content_type or upload.content_type)

    ext = Path(filename).suffix.lower()
    file_id = uuid.uuid4()
    owner_dir = _resolve_upload_root() / str(owner_id)
    owner_dir.mkdir(parents=True, exist_ok=True)
    storage_path = owner_dir / f"{file_id}{ext}"
    storage_path.write_bytes(raw)

    try:
        checksum = hashlib.sha256(raw).hexdigest()
        document = Document(
            owner_id=owner_id,
            filename=filename,
            content_type=ctype,
            size_bytes=len(raw),
            status="processing",
            storage_path=str(storage_path),
            checksum=checksum,
        )
        session.add(document)
        await session.flush()

        chunk_count = 0
        if _is_text_extractable(ctype):
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
    if _is_text_extractable(document.content_type):
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
