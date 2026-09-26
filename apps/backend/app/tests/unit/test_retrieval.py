"""Unit tests for multi-document RAG retrieval helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.ai.rag.retrieval import diversify_ranked, format_context
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
