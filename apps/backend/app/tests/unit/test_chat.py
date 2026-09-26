"""Unit tests for the chat service system-prompt builder."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.prompts import system_prompt
from app.schemas.documents import RAGRetrievalItem
from app.services.chat import _build_document_context


def test_system_prompt_includes_date() -> None:
    prompt = system_prompt("chat")
    assert "2026" in prompt
    assert "MyAIBuddy" in prompt


@pytest.mark.parametrize("kind", ["chat", "rag", "web_research", "all_rounder", "coding"])
def test_known_kinds_produce_nonempty_prompts(kind: str) -> None:
    prompt = system_prompt(kind)
    assert len(prompt) > 200
    assert "MyAIBuddy" in prompt


def test_document_context_appears_in_prompt() -> None:
    prompt = system_prompt("rag", document_context="The capital of France is Paris.")
    assert "France" in prompt
    assert "Document context" in prompt


def test_document_context_is_empty_without_documents() -> None:
    context = asyncio.run(
        _build_document_context(AsyncMock(), user_id=uuid.uuid4(), document_ids=[], query="hi")
    )

    assert context == ""


def test_a_broken_document_never_breaks_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Files help the answer; a retrieval failure must not turn a message into a 500."""

    class ExplodingRepo:
        def __init__(self, _session: object) -> None:
            pass

        async def list_by_ids(self, *_args: object, **_kwargs: object) -> list[object]:
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.db.repositories.document.DocumentRepository", ExplodingRepo, raising=True
    )

    context = asyncio.run(
        _build_document_context(
            AsyncMock(),
            user_id=uuid.uuid4(),
            document_ids=[uuid.uuid4()],
            query="what does this do?",
        )
    )

    assert context == ""


def test_document_context_lists_filenames(monkeypatch: pytest.MonkeyPatch) -> None:
    doc_id = uuid.uuid4()

    class Repo:
        def __init__(self, _session: object) -> None:
            pass

        async def list_by_ids(self, *_args: object, **_kwargs: object) -> list[object]:
            return [SimpleNamespace(id=doc_id, filename="hello.py")]

    item = RAGRetrievalItem(
        document_id=doc_id, chunk_index=2, content="def hello(): ...", score=1.0, filename="hello.py"
    )
    monkeypatch.setattr("app.db.repositories.document.DocumentRepository", Repo, raising=True)
    monkeypatch.setattr(
        "app.ai.rag.retrieval.retrieve_context", AsyncMock(return_value=[item]), raising=True
    )

    context = asyncio.run(
        _build_document_context(
            AsyncMock(), user_id=uuid.uuid4(), document_ids=[doc_id], query="what does this do?"
        )
    )

    assert "Files attached: hello.py" in context
    assert "[source: hello.py | chunk 2]" in context
