"""Chat and conversation schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    agent_kind: str = Field(default="chat", max_length=40)


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str | None
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    model: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]

    model_config = {"from_attributes": True}


class ChatMessageInput(BaseModel):
    # No length cap: users paste whole documents. Anything too large for the
    # model's context window is trimmed when the prompt is built, not rejected here.
    content: str = Field(min_length=1)


class ProviderPreference(BaseModel):
    provider: str | None = None
    model: str | None = None


class ChatRequest(ProviderPreference):
    content: str = Field(min_length=1)
    stream: bool = False
    include_memory: bool = Field(default=True, description="Whether to use conversation history")
    document_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="Document IDs whose chunks should ground the RAG answer",
    )


class ChatResponse(BaseModel):
    message: MessageOut
    provider: str
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)
