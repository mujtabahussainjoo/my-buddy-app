"""Unit tests for file-type resolution and text extraction on upload."""

from __future__ import annotations

import asyncio
import uuid
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.config import settings
from app.db.models.document import DocumentChunk
from app.services.documents import (
    IngestionResult,
    _is_text_extractable,
    _resolve_content_type,
    save_upload,
)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_calls = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_calls += 1

    def chunks(self) -> list[DocumentChunk]:
        return [obj for obj in self.added if isinstance(obj, DocumentChunk)]


def _upload(name: str, data: bytes, content_type: str = "") -> UploadFile:
    return UploadFile(
        file=BytesIO(data),
        filename=name,
        headers=Headers({"content-type": content_type} if content_type else {}),
    )


@pytest.fixture(autouse=True)
def _temp_upload_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


def _save(
    name: str, data: bytes, content_type: str = ""
) -> tuple[_FakeSession, IngestionResult]:
    session = _FakeSession()
    result = asyncio.run(
        save_upload(
            session,  # type: ignore[arg-type]
            owner_id=uuid.uuid4(),
            upload=_upload(name, data, content_type),
        )
    )
    return session, result


@pytest.mark.parametrize(
    ("filename", "reported", "expected"),
    [
        ("script.py", "text/plain", "text/plain"),
        ("app.tsx", "", "text/plain"),
        ("app.js", "", "application/javascript"),
        ("query.sql", "application/octet-stream", "application/sql"),
        ("notes.md", "text/markdown", "text/markdown"),
        ("main.go", "text/plain", "text/plain"),
        ("book.pdf", "application/pdf", "application/pdf"),
        ("photo.png", "image/png", "image/png"),
        ("weird.zzz", "text/plain", "text/plain"),
    ],
)
def test_resolve_content_type_prefers_extension(
    filename: str, reported: str, expected: str
) -> None:
    assert _resolve_content_type(b"print('hi')", filename, reported) == expected


def test_unknown_extension_with_text_sniffs_to_plain_text() -> None:
    assert _resolve_content_type("héllo wörld".encode(), "notes.unknownext", "") == "text/plain"


def test_unknown_extension_with_binary_sniffs_to_octet_stream() -> None:
    assert _resolve_content_type(b"\x00\x01\x02\xff\xfe", "blob.unknownext", "") == (
        "application/octet-stream"
    )


def test_binary_reported_type_is_re_sniffed() -> None:
    assert _resolve_content_type(b"print(1)", "setup", "application/zip") == "text/plain"


@pytest.mark.parametrize(
    ("content_type", "extractable"),
    [
        ("text/plain", True),
        ("text/markdown", True),
        ("application/javascript", True),
        ("application/sql", True),
        ("application/x-ipynb+json", True),
        ("application/pdf", True),
        ("application/octet-stream", False),
        ("image/png", False),
        ("application/zip", False),
        ("application/msword", False),
    ],
)
def test_is_text_extractable(content_type: str, extractable: bool) -> None:
    assert _is_text_extractable(content_type) is extractable


def test_python_file_is_chunked() -> None:
    source = b"def hello():\n    return 'world'\n" * 40
    session, result = _save("1-hello-anthropic-api.py", source, "text/plain")

    assert result.document.content_type == "text/plain"
    assert result.document.status == "ready"
    assert result.chunk_count > 1
    assert len(session.chunks()) == result.chunk_count
    first = session.chunks()[0].content
    assert "def hello" in first
    assert "\n" in first, "line structure must survive so code stays readable"


def test_extensionless_text_file_is_chunked() -> None:
    session, result = _save("Dockerfile", b"FROM python:3.12\nRUN pip install fastapi\n")

    assert result.document.content_type == "text/plain"
    assert result.chunk_count == 1
    assert "pip install fastapi" in session.chunks()[0].content


def test_binary_file_is_stored_without_chunks() -> None:
    session, result = _save("icon.zzz", b"\x89PNG\r\n\x1a\n\x00\xff\x00", "application/octet-stream")

    assert result.document.content_type == "application/octet-stream"
    assert result.document.status == "ready"
    assert result.chunk_count == 0
    assert session.chunks() == []


def test_uploaded_file_is_written_under_owner_directory(tmp_path: Path) -> None:
    _session, result = _save("notes.txt", b"hello")
    stored = Path(result.document.storage_path)

    assert stored.read_bytes() == b"hello"
    assert stored.parent == tmp_path / str(result.document.owner_id)
    assert stored.name.endswith(".txt")
