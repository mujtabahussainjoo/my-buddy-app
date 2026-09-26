"""Integration tests for the document upload endpoints (no database required)."""

from __future__ import annotations

import asyncio
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.exceptions import FastAPIDeprecationWarning
from httpx import ASGITransport, AsyncClient

from app.api.v1.deps.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.services import documents as documents_service
from app.services.documents import BulkUploadResult, IngestionResult


@contextmanager
def _allow_orjson_response() -> Iterator[None]:
    """The app sets ORJSONResponse globally, which newer FastAPI deprecates."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FastAPIDeprecationWarning)
        yield


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, instance: Any) -> None:
        return None


def _document(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        filename=name,
        content_type="text/plain",
        size_bytes=12,
        status="ready",
        error_message=None,
        created_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    session = _FakeSession()
    user = SimpleNamespace(id=uuid.uuid4(), roles=[SimpleNamespace(name="user")])

    async def fake_get_db() -> Any:
        yield session

    async def fake_get_current_user() -> Any:
        return user

    app.dependency_overrides[get_db] = fake_get_db
    app.dependency_overrides[get_current_user] = fake_get_current_user
    try:
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    finally:
        app.dependency_overrides.clear()


def test_bulk_upload_returns_items_and_failures(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_save_uploads(session: Any, *, owner_id: uuid.UUID, uploads: Any) -> BulkUploadResult:
        return BulkUploadResult(
            documents=[IngestionResult(document=_document("a.txt"), chunk_count=3)],  # type: ignore[arg-type]
            failures=[("huge.bin", "File exceeds the 50 MB upload limit")],
        )

    monkeypatch.setattr(documents_service, "save_uploads", fake_save_uploads)

    async def run() -> Any:
        async with client as http:
            return await http.post(
                "/api/v1/documents/bulk",
                files=[
                    ("files", ("a.txt", b"hello world")),
                    ("files", ("huge.bin", b"\x00" * 8)),
                ],
            )

    with _allow_orjson_response():
        response = asyncio.run(run())

    assert response.status_code == 200
    payload = response.json()["data"]
    assert [item["filename"] for item in payload["items"]] == ["a.txt"]
    assert payload["items"][0]["chunk_count"] == 3
    assert payload["items"][0]["is_owner"] is True
    assert payload["failed"] == [
        {"filename": "huge.bin", "error": "File exceeds the 50 MB upload limit"}
    ]


def test_single_upload_accepted_for_regular_user(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_save_upload(session: Any, *, owner_id: uuid.UUID, upload: Any, content_type: str | None = None) -> IngestionResult:
        return IngestionResult(document=_document(upload.filename or "upload"), chunk_count=1)  # type: ignore[arg-type]

    monkeypatch.setattr(documents_service, "save_upload", fake_save_upload)

    async def run() -> Any:
        async with client as http:
            return await http.post(
                "/api/v1/documents",
                files={"file": ("notes.md", b"# notes")},
            )

    with _allow_orjson_response():
        response = asyncio.run(run())

    assert response.status_code == 200
    assert response.json()["data"]["filename"] == "notes.md"


def test_non_owner_cannot_delete_document(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    document_id = uuid.uuid4()
    called = False

    async def fake_delete(session: Any, *, document_id: uuid.UUID, owner_id: uuid.UUID | None = None) -> None:
        nonlocal called
        called = True

    async def fake_get(self: Any, object_id: uuid.UUID) -> Any:
        return SimpleNamespace(id=object_id, owner_id=uuid.uuid4(), deleted_at=None)

    monkeypatch.setattr(documents_service, "delete_document", fake_delete)
    monkeypatch.setattr("app.db.repositories.document.DocumentRepository.get", fake_get)

    async def run() -> Any:
        async with client as http:
            return await http.delete(f"/api/v1/documents/{document_id}")

    with _allow_orjson_response():
        response = asyncio.run(run())

    assert response.status_code == 403
    assert called is False
