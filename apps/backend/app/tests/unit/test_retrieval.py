"""Unit tests for multi-document RAG retrieval helpers."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from app.ai.rag import retrieval
from app.ai.rag.retrieval import _tokenize, diversify_ranked, format_context, retrieve_context
from app.schemas.documents import RAGRetrievalItem


@dataclass
class FakeChunk:
    document_id: uuid.UUID
    chunk_index: int
    content: str


DOC_A = uuid.uuid4()
DOC_B = uuid.uuid4()
DOC_C = uuid.uuid4()


def _chunk(doc_id: uuid.UUID, index: int, content: str = "text") -> FakeChunk:
    return FakeChunk(document_id=doc_id, chunk_index=index, content=content)


def test_diversify_gives_every_document_a_slot() -> None:
    ranked = [
        (_chunk(DOC_A, 0), 9.0),
        (_chunk(DOC_A, 1), 8.5),
        (_chunk(DOC_A, 2), 8.0),
        (_chunk(DOC_B, 0), 1.0),
    ]

    selected = diversify_ranked(ranked, top_k=3, chunks_per_doc=2)

    assert [chunk.document_id for chunk, _ in selected] == [DOC_A, DOC_B, DOC_A]


def test_diversify_respects_per_document_cap() -> None:
    ranked = [(_chunk(DOC_A, index), 9.0 - index / 10) for index in range(5)]

    selected = diversify_ranked(ranked, top_k=5, chunks_per_doc=2)

    assert len(selected) == 2
    assert [chunk.chunk_index for chunk, _ in selected] == [0, 1]


def test_diversify_handles_single_document() -> None:
    ranked = [(_chunk(DOC_A, index), float(index)) for index in range(3)]

    selected = diversify_ranked(ranked, top_k=3, chunks_per_doc=2)

    assert [chunk.chunk_index for chunk, _ in selected] == [0, 1]


def test_diversify_ignores_non_positive_budget() -> None:
    assert diversify_ranked([(_chunk(DOC_A, 0), 1.0)], top_k=0, chunks_per_doc=1) == []


def test_format_context_labels_each_source_with_filename() -> None:
    items = [
        RAGRetrievalItem(
            document_id=DOC_A,
            chunk_index=0,
            content="Paris is the capital of France.",
            score=1.0,
            filename="france.md",
        ),
        RAGRetrievalItem(
            document_id=DOC_B,
            chunk_index=3,
            content="Berlin is the capital of Germany.",
            score=0.5,
            filename="germany.md",
        ),
    ]

    context = format_context(items)

    assert "[source: france.md | chunk 0]" in context
    assert "[source: germany.md | chunk 3]" in context
    assert "---" in context


def test_format_context_falls_back_to_id_without_filename() -> None:
    items = [RAGRetrievalItem(document_id=DOC_C, chunk_index=1, content="hello", score=0.2)]

    context = format_context(items)

    assert f"[source: {DOC_C} | chunk 1]" in context


def test_format_context_respects_char_budget() -> None:
    items = [
        RAGRetrievalItem(
            document_id=DOC_A,
            chunk_index=index,
            content="x" * 100,
            score=1.0,
            filename="big.md",
        )
        for index in range(5)
    ]

    context = format_context(items, max_chars=250)

    assert len(context) <= 250


def test_format_context_empty_items() -> None:
    assert format_context([]) == ""


def test_tokenize_keeps_non_latin_scripts() -> None:
    assert _tokenize("Hello 世界") == ["hello", "世界"]
    assert _tokenize("مرحبا") == ["مرحبا"]


def _retrieve(
    monkeypatch: pytest.MonkeyPatch, chunks: list[FakeChunk], query: str = "hello"
) -> list[RAGRetrievalItem]:
    repo = AsyncMock()
    repo.chunks_for_documents = AsyncMock(return_value=chunks)
    monkeypatch.setattr(retrieval, "DocumentChunkRepository", lambda _session: repo)
    return asyncio.run(retrieve_context(AsyncMock(), document_ids=[DOC_A], query=query, filenames={}))


def test_retrieve_survives_a_file_with_no_searchable_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file made only of symbols must not break the chat message.

    BM25 divides by the number of known words, so a corpus with no words at all
    used to raise ZeroDivisionError and turn every message into a 500.
    """
    chunks = [_chunk(DOC_A, 0, "\U0001f389 \U0001f389 \U0001f389"), _chunk(DOC_A, 1, "????? ...")]

    assert _retrieve(monkeypatch, chunks, query="hello") == []


def test_retrieve_handles_non_latin_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """Urdu/Arabic/accented text is searchable because the tokenizer is Unicode-aware."""
    chunks = [
        _chunk(DOC_A, 0, "\u06cc\u06c1 \u0627\u06cc\u06a9 \u0639\u0627\u0645 \u062e\u0637 \u06c1\u06d2"),
        _chunk(DOC_A, 1, "\u06a9\u06be\u0627\u0646\u0627 \u067e\u0627\u06a9\u0633\u062a\u0627\u0646\u06cc \u06c1\u06d2"),
        _chunk(DOC_A, 2, "\u067e\u06cc\u0631\u0633 \u0641\u0631\u0627\u0646\u0633 \u06a9\u0627 \u062f\u0627\u0631\u0627\u0644\u062d\u06a9\u0648\u0645\u062a"),
    ]

    items = _retrieve(monkeypatch, chunks, query="\u067e\u06cc\u0631\u0633")

    assert [item.chunk_index for item in items] == [2]
    assert items[0].content == chunks[2].content


def test_retrieve_always_returns_something_for_an_attached_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Files are attached on purpose, so never return nothing for a real file.

    CJK has no spaces, so a query word never equals a corpus word; retrieval falls
    back to the leading chunks instead of dropping the file.
    """
    chunks = [_chunk(DOC_A, 0, "\u7b2c\u4e00\u6bb5\u5185\u5bb9"), _chunk(DOC_A, 1, "\u5df4\u9ece\u662f\u6cd5\u56fd\u9996\u90fd")]

    assert _retrieve(monkeypatch, chunks, query="\u5df4\u9ece")
