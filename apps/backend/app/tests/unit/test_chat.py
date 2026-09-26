"""Unit tests for the chat service system-prompt builder."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.ai.prompts import system_prompt
from app.db.models import Conversation
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


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_error_logs_carry_the_reason_not_just_a_name() -> None:
    """`app_error` alone tells you nothing; the log must say what went wrong."""
    import logging as std_logging

    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.core.exceptions import NotFoundError, register_exception_handlers
    from app.core.logging import recent_logs, ring_buffer, setup_logging

    setup_logging("WARNING")
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise NotFoundError("Resource not found")

    try:
        response = TestClient(app, raise_server_exceptions=False).get("/boom")
        assert response.status_code == 404

        records = [r for r in recent_logs(min_level="WARNING", limit=20) if "boom" in str(r)]
        assert records, "the failing path must be logged"
        latest = records[-1]
        assert latest["message"].startswith("app_error: ")
        assert latest["detail"] == "Resource not found"
        assert latest["status"] == 404
        assert latest["code"] == "not_found"
        assert latest["path"] == "/boom"
    finally:
        ring_buffer._buffer.clear()
        std_logging.getLogger().handlers = [
            h for h in std_logging.getLogger().handlers if h is not ring_buffer
        ]


def test_a_very_long_message_is_accepted() -> None:
    """Users paste whole documents; the API must not reject them."""
    from app.schemas.chat import ChatRequest

    huge = "x" * 500_000
    request = ChatRequest(content=huge)

    assert len(request.content) == 500_000


def test_short_text_is_left_alone() -> None:
    from app.services.chat import _fit_text

    text, trimmed = _fit_text("hello world", 100)

    assert text == "hello world"
    assert trimmed is False


def test_long_text_keeps_both_ends_and_says_what_was_dropped() -> None:
    from app.services.chat import _fit_text

    text, trimmed = _fit_text("START" + ("y" * 5000) + "QUESTION", 100)

    assert trimmed is True
    assert text.startswith("START")
    assert text.endswith("QUESTION")
    assert "characters omitted" in text
    assert len(text) < 200


def test_history_is_dropped_before_the_current_message_is_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    """The question you just asked matters more than old turns."""
    from types import SimpleNamespace

    from app.core.config import settings
    from app.services import chat as chat_service

    monkeypatch.setattr(settings, "LLM_MAX_INPUT_CHARS", 2_000)
    old = SimpleNamespace(role="user", content="o" * 1_500)
    newer = SimpleNamespace(role="assistant", content="a" * 100)

    class HistoryRepo:
        def __init__(self, _session: object) -> None:
            pass

        async def history(self, *_args: object, **_kwargs: object) -> list[object]:
            return [old, newer]  # oldest first

    monkeypatch.setattr(chat_service, "MessageRepository", HistoryRepo)
    conversation = SimpleNamespace(id=uuid.uuid4(), agent_kind="chat")

    msgs = asyncio.run(
        chat_service._build_messages(
            AsyncMock(), cast(Conversation, conversation), "NEWEST", include_memory=True
        )
    )

    roles = [m.role for m in msgs]
    assert msgs[-1].content == "NEWEST", "the current message must survive intact"
    assert "a" * 100 in [m.content for m in msgs], "the recent turn is kept"
    assert "o" * 1_500 not in [m.content for m in msgs], "the oversized old turn is dropped"
    assert roles[0] == "system"


def test_include_memory_false_sends_only_the_new_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.services import chat as chat_service

    class HistoryRepo:
        def __init__(self, _session: object) -> None:
            pass

        async def history(self, *_args: object, **_kwargs: object) -> list[object]:
            raise AssertionError("history must not be read when memory is off")

    monkeypatch.setattr(chat_service, "MessageRepository", HistoryRepo)
    conversation = SimpleNamespace(id=uuid.uuid4(), agent_kind="chat")

    msgs = asyncio.run(
        chat_service._build_messages(
            AsyncMock(), cast(Conversation, conversation), "hello", include_memory=False
        )
    )

    assert [m.role for m in msgs] == ["system", "user"]
    assert msgs[-1].content == "hello"
