"""Chat service: history-aware completion, streaming, audit."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import ChatMessage
from app.ai.prompts import system_prompt
from app.db.models import Conversation
from app.db.repositories.conversation import ConversationRepository, MessageRepository
from app.schemas.chat import ChatRequest, ChatResponse, ConversationCreate, MessageOut
from app.schemas.common import Paginated
from app.services.audit import record_audit
from app.services.providers import build_resolved

_MAX_HISTORY = 30


async def list_conversations(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> Paginated[Conversation]:
    convos, total = await ConversationRepository(session).list_for_user(
        user_id, page=page, page_size=page_size
    )
    return Paginated(
        items=convos,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
    )


async def create_conversation(
    session: AsyncSession, *, user_id: uuid.UUID, body: ConversationCreate
) -> Conversation:
    conv = Conversation(user_id=user_id, title=body.title, agent_kind=body.agent_kind)
    session.add(conv)
    await session.flush()
    await record_audit(
        session,
        action="conversation.created",
        actor_id=user_id,
        resource_type="conversation",
        resource_id=str(conv.id),
    )
    await session.commit()
    await session.refresh(conv)
    return conv


async def get_conversation_detail(
    session: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    return await ConversationRepository(session).get_owned(user_id, conversation_id)


async def delete_conversation(
    session: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> None:
    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_owned(user_id, conversation_id)
    await conv_repo.soft_delete(conv)
    await record_audit(
        session,
        action="conversation.deleted",
        actor_id=user_id,
        resource_type="conversation",
        resource_id=str(conversation_id),
    )
    await session.commit()


async def _build_messages(
    session: AsyncSession,
    conversation: Conversation,
    user_content: str,
    *,
    include_memory: bool,
    document_context: str = "",
) -> list[ChatMessage]:
    msgs: list[ChatMessage] = []
    system = system_prompt(conversation.agent_kind, document_context=document_context)
    msgs.append(ChatMessage(role="system", content=system))
    if include_memory:
        history = await MessageRepository(session).history(conversation.id, max_messages=_MAX_HISTORY)
        for message in history:
            msgs.append(ChatMessage(role=message.role, content=message.content))
    msgs.append(ChatMessage(role="user", content=user_content))
    return msgs


async def _build_document_context(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    document_ids: list[uuid.UUID],
    query: str,
) -> str:
    """Retrieve relevant chunks across the attached documents and format the block.

    Every document attached to the message gets its best-matching chunk in the
    block (see ``app.ai.rag.retrieval.diversify_ranked``) and each excerpt is
    labelled with its filename so the model can cite the file it used.
    """
    if not document_ids:
        return ""
    try:
        from app.ai.rag.retrieval import format_context, retrieve_context  # noqa: PLC0415
        from app.db.repositories.document import DocumentRepository  # noqa: PLC0415
    except ImportError:
        return ""
    docs = await DocumentRepository(session).list_by_ids(document_ids, only_ready=True)
    if not docs:
        return ""
    filenames = {doc.id: doc.filename for doc in docs}
    items = await retrieve_context(
        session,
        document_ids=list(filenames),
        query=query,
        filenames=filenames,
    )
    body = format_context(items)
    if not body:
        return ""
    listing = ", ".join(sorted(filenames.values()))
    return f"Files attached: {listing}\n\n{body}"


async def send_message(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: ChatRequest,
) -> ChatResponse:
    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_owned(user_id, conversation_id)
    msg_repo = MessageRepository(session)
    user_msg = await msg_repo.add(conversation_id=conv.id, role="user", content=body.content)
    await session.flush()
    prepared = await _build_messages(
        session, conv, body.content,
        include_memory=body.include_memory,
        document_context=await _build_document_context(
            session,
            user_id=user_id,
            document_ids=body.document_ids or [],
            query=body.content,
        ),
    )
    provider, provider_name = await build_resolved(session, preferred=body.provider, model=body.model)
    try:
        llm_response = await provider.chat(prepared)
    except Exception as exc:
        user_msg.content = f"[error] {body.content}"  # keep original but flag
        await msg_repo.add(
            conversation_id=conv.id, role="system", content=f"provider_error:{provider_name}:{exc}"
        )
        await session.commit()
        raise
    assistant_msg = await msg_repo.add(
        conversation_id=conv.id,
        role="assistant",
        content=llm_response.content,
        model=llm_response.model,
        token_usage=llm_response.usage,
    )
    await session.flush()
    await record_audit(
        session,
        action="chat.message_sent",
        actor_id=user_id,
        resource_type="conversation",
        resource_id=str(conv.id),
        details={"provider": provider_name, "model": llm_response.model, "usage": llm_response.usage},
    )
    await session.commit()
    await session.refresh(assistant_msg)
    return ChatResponse(
        message=MessageOut.model_validate(assistant_msg),
        provider=provider_name,
        model=llm_response.model,
        usage=llm_response.usage,
    )


async def stream_message(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: ChatRequest,
) -> AsyncGenerator[str, None]:
    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_owned(user_id, conversation_id)
    msg_repo = MessageRepository(session)
    user_msg = await msg_repo.add(conversation_id=conv.id, role="user", content=body.content)
    await session.flush()
    prepared = await _build_messages(
        session, conv, body.content,
        include_memory=body.include_memory,
        document_context=await _build_document_context(
            session,
            user_id=user_id,
            document_ids=body.document_ids or [],
            query=body.content,
        ),
    )
    provider, provider_name = await build_resolved(session, preferred=body.provider, model=body.model)
    try:
        llm_response = await provider.chat(prepared)
    except Exception as exc:
        yield f"data: {json.dumps({'event': 'error', 'detail': str(exc)})}\n\n"
        await session.commit()
        return
    assistant_msg = await msg_repo.add(
        conversation_id=conv.id,
        role="assistant",
        content=llm_response.content,
        model=llm_response.model,
        token_usage=llm_response.usage,
    )
    await session.flush()
    await record_audit(
        session,
        action="chat.message_sent",
        actor_id=user_id,
        resource_type="conversation",
        resource_id=str(conv.id),
        details={"provider": provider_name, "model": llm_response.model},
    )
    yield f"data: {json.dumps({'event': 'chat_start', 'user_message_id': str(user_msg.id)})}\n\n"
    content = llm_response.content
    chunk_size = 40
    for start in range(0, len(content), chunk_size):
        chunk = content[start : start + chunk_size]
        yield f"data: {json.dumps({'event': 'delta', 'content': chunk})}\n\n"
    out = MessageOut.model_validate(assistant_msg)
    yield f"data: {json.dumps({'event': 'chat_end', 'message': out.model_dump(mode='json'), 'provider': provider_name, 'model': llm_response.model})}\n\n"
    await session.commit()
