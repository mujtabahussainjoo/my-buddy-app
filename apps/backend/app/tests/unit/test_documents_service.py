"""Unit tests for the multi-file document upload service."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.services import documents as documents_service
from app.services.documents import BulkUploadResult, IngestionResult


class _FakeSession:
    """Minimal async-session stand-in exposing savepoints and flush."""

    def __init__(self) -> None:
        self.flush = AsyncMock()
        self.savepoints = 0

    def begin_nested(self) -> _FakeSession:
        self.savepoints += 1
        return self

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _upload(name: str) -> UploadFile:
    return UploadFile(file=SimpleNamespace(name=name), filename=name, headers=None)  # type: ignore[arg-type]


@pytest.fixture
def ingested(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the per-file ingestion with a stub that rejects oversized files."""
    accepted: list[str] = []

    async def fake_save_upload(
        session: object, *, owner_id: uuid.UUID, upload: UploadFile, content_type: str | None = None
    ) -> IngestionResult:
        name = upload.filename or "upload"
        if name.endswith(".bin"):
            raise ValidationError("File exceeds the 50 MB upload limit")
        accepted.append(name)
        return IngestionResult(
            document=SimpleNamespace(id=uuid.uuid4(), filename=name),  # type: ignore[arg-type]
            chunk_count=2,
        )

    monkeypatch.setattr(documents_service, "save_upload", fake_save_upload)
    return accepted


def test_bulk_upload_keeps_good_files_when_one_fails(ingested: list[str]) -> None:
    async def run() -> BulkUploadResult:
        return await documents_service.save_uploads(
            _FakeSession(),  # type: ignore[arg-type]
            owner_id=uuid.uuid4(),
            uploads=[_upload("a.txt"), _upload("huge.bin"), _upload("b.md")],
        )

    result = asyncio.run(run())

    assert ingested == ["a.txt", "b.md"]
    assert [entry.document.filename for entry in result.documents] == ["a.txt", "b.md"]
    assert result.failures == [("huge.bin", "File exceeds the 50 MB upload limit")]


def test_bulk_upload_caps_file_count(
    ingested: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAX_UPLOAD_FILES", 2)

    async def run() -> BulkUploadResult:
        return await documents_service.save_uploads(
            _FakeSession(),  # type: ignore[arg-type]
            owner_id=uuid.uuid4(),
            uploads=[_upload(f"file-{index}.txt") for index in range(4)],
        )

    result = asyncio.run(run())

    assert len(result.documents) == 2
    assert [name for name, _ in result.failures] == ["file-2.txt", "file-3.txt"]
    assert "At most 2 files" in result.failures[0][1]


def test_bulk_upload_survives_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(
        session: object, *, owner_id: uuid.UUID, upload: UploadFile, content_type: str | None = None
    ) -> IngestionResult:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(documents_service, "save_upload", boom)

    async def run() -> BulkUploadResult:
        return await documents_service.save_uploads(
            _FakeSession(),  # type: ignore[arg-type]
            owner_id=uuid.uuid4(),
            uploads=[_upload("a.txt")],
        )

    result = asyncio.run(run())

    assert result.documents == []
    assert "disk on fire" in result.failures[0][1]
