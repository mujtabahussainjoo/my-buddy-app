"""RAG retrieval over stored document chunks.

Keyword-first retrieval (BM25) so that RAG works fully offline out of the box,
matching the default ``EMBEDDING_PROVIDER=keyword`` configuration.

When several documents are attached to a single message the selection is made
*diversified*: every attached file contributes its best chunk before any file is
allowed to contribute a second one, so one long document cannot crowd the others
out of the prompt.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from rank_bm25 import BM25Okapi
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.repositories.document import DocumentChunkRepository
from app.schemas.documents import RAGRetrievalItem


class _ChunkLike(Protocol):
    document_id: uuid.UUID
    chunk_index: int
    content: str


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def diversify_ranked(
    ranked: Sequence[tuple[_ChunkLike, float]],
    *,
    top_k: int,
    chunks_per_doc: int,
) -> list[tuple[_ChunkLike, float]]:
    """Round-robin the ranked chunks so every document is represented.

    ``ranked`` must be sorted by descending score. At most ``chunks_per_doc``
    chunks are taken from any single document, and at most ``top_k`` in total.
    """
    if top_k <= 0:
        return []
    buckets: dict[uuid.UUID, list[tuple[_ChunkLike, float]]] = {}
    for chunk, score in ranked:
        buckets.setdefault(chunk.document_id, []).append((chunk, score))
    trimmed = [items[: max(1, chunks_per_doc)] for items in buckets.values()]

    selected: list[tuple[_ChunkLike, float]] = []
    depth = 0
    while len(selected) < top_k and any(len(items) > depth for items in trimmed):
        for items in trimmed:
            if len(items) > depth and len(selected) < top_k:
                selected.append(items[depth])
        depth += 1
    return selected


async def _resolve_filenames(
    session: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    from app.db.repositories.document import DocumentRepository  # noqa: PLC0415

    docs: list[Any] = await DocumentRepository(session).list_by_ids(document_ids)
    return {doc.id: doc.filename for doc in docs}


async def retrieve_context(
    session: AsyncSession,
    *,
    document_ids: list[uuid.UUID],
    query: str,
    top_k: int | None = None,
    min_score: float | None = None,
    chunks_per_doc: int | None = None,
    filenames: Mapping[uuid.UUID, str] | None = None,
) -> list[RAGRetrievalItem]:
    """Return the most relevant chunks for ``query`` across ``document_ids``."""
    if not document_ids or not query.strip():
        return []
    chunks = await DocumentChunkRepository(session).chunks_for_documents(document_ids)
    if not chunks:
        return []
    per_doc = chunks_per_doc or settings.RETRIEVAL_CHUNKS_PER_DOC
    # Grow the budget with the number of attached files so a bigger pile of
    # documents still reaches the model.
    top_k = top_k or max(settings.RETRIEVAL_TOP_K, len(document_ids) * per_doc)
    threshold = min_score if min_score is not None else settings.RETRIEVAL_MIN_SCORE

    corpus = [_tokenize(chunk.content) for chunk in chunks]
    query_tokens = _tokenize(query)
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)
    ranked = sorted(zip(chunks, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    above = [(chunk, score) for chunk, score in ranked if score >= threshold]
    if not above and ranked:
        # BM25 IDF is negative for tiny corpora (single/few documents); the user
        # explicitly selected these documents, so always surface the best chunks.
        above = ranked[: max(1, top_k)]
    selected = diversify_ranked(above, top_k=top_k, chunks_per_doc=per_doc)
    if filenames is None:
        filenames = await _resolve_filenames(session, document_ids)
    return [
        RAGRetrievalItem(
            document_id=chunk.document_id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            score=max(0.0, round(score, 4)),
            filename=filenames.get(chunk.document_id),
        )
        for chunk, score in selected
    ]


def format_context(
    items: list[RAGRetrievalItem], *, max_chars: int | None = None
) -> str:
    """Build the prompt-ready document context block, labelled by filename."""
    if not items:
        return ""
    budget = max_chars or settings.RETRIEVAL_MAX_CONTEXT_CHARS
    sections: list[str] = []
    used = 0
    for item in items:
        label = item.filename or str(item.document_id)
        section = f"[source: {label} | chunk {item.chunk_index}]\n{item.content}"
        if used + len(section) > budget:
            section = section[: max(0, budget - used)]
            if section.strip():
                sections.append(section)
            break
        sections.append(section)
        used += len(section) + len("\n\n---\n\n")
    return "\n\n---\n\n".join(sections)
