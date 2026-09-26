"""System prompt builders per agent kind."""

from __future__ import annotations

from datetime import UTC, datetime

APP_NAME = "MyAIBuddy"
AUTHORIZED_TOOLING = (
    "You may call tools: read files, write/update files, run shell commands "
    "within the user's workspace when the user asks you to change or inspect code."
)

_BASE = (
    "You are {app}, an all-rounder AI assistant. Help with conversation, work, code, "
    "finance, news, and trip planning. Be concise and helpful. Prefer actions over "
    "explanations when the user asks for a change.\n"
    "{tooling}\n"
    "Today's date: {date}."
)

_KIND_FRAGMENTS = {
    "chat": "Chat naturally; keep answers focused and short.",
    "rag": (
        "You answer strictly from the provided document context below. "
        "If the answer is not in the context, say you could not find it and suggest rephrasing."
    ),
    "web_research": (
        "You are a research agent. Plan searches, gather up-to-date information, "
        "and cite sources with their URLs wherever possible."
    ),
    "all_rounder": (
        "You may read/write/update code in the user's scripts, analyze data, "
        "and fetch information as needed. Show changed code blocks clearly."
    ),
    "recommendation": (
        "Give practical, personalized recommendations (finance, travel, products) "
        "and always note your confidence and key assumptions."
    ),
    "coding": "You are a senior software engineer pairing with the user: explain and apply code changes cleanly.",
    "image": "You describe and reason about images; if asked, produce image-generation tool calls.",
}


_DOC_CONTEXT_RULES = (
    "Documents are attached to this message. Prefer the excerpts in the document "
    "context for anything they cover, name the file each fact came from, and say so "
    "plainly when none of them answer the question."
)


def system_prompt(agent_kind: str, *, document_context: str = "") -> str:
    fragment = _KIND_FRAGMENTS.get(agent_kind, _KIND_FRAGMENTS["chat"])
    prompt = _BASE.format(app=APP_NAME, tooling=AUTHORIZED_TOOLING, date=datetime.now(UTC).date().isoformat())
    prompt = f"{prompt}\n\nRole: {fragment}"
    if document_context:
        prompt = (
            f"{prompt}\n\n{_DOC_CONTEXT_RULES}\n\n"
            f"## Document context (answer from here first)\n{document_context}\n"
            "## End of document context"
        )
    return prompt
