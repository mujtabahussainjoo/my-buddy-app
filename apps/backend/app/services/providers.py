"""Provider configuration service: env + DB credential merge."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIProviderError
from app.ai.encryption import decrypt_secret, encrypt_secret, fingerprint
from app.ai.registry import build_provider, list_provider_names, provider_meta, provider_meta_with_guidance
from app.core.config import settings
from app.core.exceptions import CredentialUndecryptableError
from app.core.logging import logger
from app.db.models import ProviderCredential
from app.schemas.providers import ProviderStatus, ProviderUpdateRequest, ResolvedProvider
from app.services.audit import record_audit

_ENV_KEY_FIELD: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "huggingface": "HUGGINGFACE_API_TOKEN",
    "ollama": "OLLAMA_BASE_URL",
}

_REACHABILITY_TTL = 60.0
_reachability_cache: dict[str, tuple[float, bool]] = {}


def _env_api_key(name: str) -> str:
    field = _ENV_KEY_FIELD.get(name)
    if not field:
        return ""
    return str(getattr(settings, field, "") or "")


async def _ollama_reachable() -> bool:
    """Cached reachability probe so dead Ollama gracefully falls back to mock."""
    cached = _reachability_cache.get("ollama")
    now = time.monotonic()
    if cached and now - cached[0] < _REACHABILITY_TTL:
        return cached[1]
    try:
        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags")
        reachable = response.status_code == 200
    except Exception:
        reachable = False
    _reachability_cache["ollama"] = (now, reachable)
    return reachable


_EMBED_LIKE_MODEL_TOKENS = ("embed", "nomic", "mxbai", "bge", "mini", "minilm", "granite")


def _model_base(name: str) -> str:
    """Strip :tag suffix, e.g. 'llama3:latest' -> 'llama3'."""
    return name.split(":", 1)[0]


async def _ollama_available_models() -> list[str]:
    """Cached list of model names from the Ollama server (/api/tags)."""
    cached = _reachability_cache.get("ollama_models")
    now = time.monotonic()
    if cached and now - cached[0] < _REACHABILITY_TTL:
        return cached[1]
    models: list[str] = []
    try:
        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags")
        if response.status_code == 200:
            models = [str(m.get("name", "")) for m in response.json().get("models", [])]
    except Exception:
        models = []
    _reachability_cache["ollama_models"] = (now, models)
    return models


async def _ollama_chat_model(preferred: str | None = None) -> str:
    """Pick a chat-capable model that actually exists on the local/remote Ollama.

    Prefers an explicitly configured model, then a known chat default, then the
    first non-embedding model present. Fallback: llama3.2.
    """
    default = "llama3.2"
    if preferred:
        return preferred
    models = await _ollama_available_models()
    if not models:
        return default
    bases = [_model_base(m) for m in models]
    if default in bases:
        return default
    candidates = [base for base in bases if not any(tok in base.lower() for tok in _EMBED_LIKE_MODEL_TOKENS)]
    if candidates:
        return candidates[0]
    return models[0]


async def get_provider_statuses(session: AsyncSession) -> list[ProviderStatus]:
    rows = (await session.execute(select(ProviderCredential))).scalars().all()
    by_name = {row.provider: row for row in rows}
    statuses: list[ProviderStatus] = []
    for name in ("mock", "openai", "anthropic", "google", "huggingface", "ollama", "venv"):
        if name not in {p for p in list_provider_names()}:
            continue
        display_name, capabilities, default_model, suggestions, notes = provider_meta_with_guidance(name)
        row = by_name.get(name)
        env_key = _env_api_key(name)
        configured = bool(row) or bool(env_key) or name in {"mock", "venv"}
        statuses.append(
            ProviderStatus(
                name=name,
                display_name=display_name,
                description=notes,
                model_suggestions=suggestions,
                chat_unsupported="chat" not in capabilities,
                enabled=bool(row.enabled) if row else (name in {"mock", "ollama"}),
                configured=configured,
                key_fingerprint=row.key_fingerprint if row else None,
                default_model=default_model,
                model_chat=row.model_chat if row else None,
                model_reasoning=row.model_reasoning if row else None,
                model_embedding=row.model_embedding if row else None,
                model_image=row.model_image if row else None,
                base_url=row.base_url if row else None,
                fallback_order=row.fallback_order if row else None,
                capabilities=list(capabilities),
            )
        )
    _mark_default(statuses)
    return statuses


def _mark_default(statuses: list[ProviderStatus]) -> None:
    """Default = lowest fallback_order among enabled, usable, real providers.

    mock is always a last-resort: it only becomes the default when no other
    enabled provider can actually serve chat.
    """
    usable = [s for s in statuses if s.enabled and _status_usable(s)]
    real = [s for s in usable if s.name != "mock"]
    active = real or usable
    if not active:
        return
    ordered = [s for s in active if s.fallback_order is not None]
    if ordered:
        best_key = min(s.fallback_order for s in ordered)  # type: ignore[type-var]
        for s in ordered:
            if s.fallback_order == best_key:
                s.is_default = True
                return
    active[0].is_default = True


async def _active_chat_provider(session: AsyncSession, *, exclude: str | None = None) -> str | None:
    """Name of the enabled provider that would actually serve chat, or None.

    Mirrors resolve_provider's ordering/availability checks but only inspects
    DB rows (no provider build), so upsert can decide when to auto-promote.
    mock is treated as a last resort (never reported while a real provider works).
    """
    rows = (
        (
            await session.execute(
                select(ProviderCredential)
                .where(ProviderCredential.enabled.is_(True), ProviderCredential.provider != exclude)
                .order_by(ProviderCredential.fallback_order.asc().nulls_last())
            )
        )
        .scalars()
        .all()
    )
    usable: list[ProviderCredential] = []
    for row in rows:
        api_key = (
            decrypt_secret(row.encrypted_api_key) if row.encrypted_api_key else _env_api_key(row.provider)
        )
        if _is_usable_for_chat(row.provider, api_key=api_key, base_url=row.base_url or ""):
            usable.append(row)
    real = [row for row in usable if row.provider != "mock"]
    if real:
        return real[0].provider
    return usable[0].provider if usable else None


async def upsert_provider(
    session: AsyncSession,
    *,
    name: str,
    body: ProviderUpdateRequest,
    actor_id: uuid.UUID,
) -> ProviderStatus:
    if name not in list_provider_names():
        raise AIProviderError(f"Unknown provider: {name}", code="unknown_provider")
    row = (
        await session.execute(select(ProviderCredential).where(ProviderCredential.provider == name))
    ).scalar_one_or_none()
    if row is None:
        row = ProviderCredential(provider=name)
        session.add(row)
    new_key = body.api_key
    if new_key is not None and not new_key.strip():
        raise AIProviderError("api_key must be at least 8 characters", code="validation_error")

    if body.set_default is True:
        await _reorder_others(session, exclude=name)
        row.fallback_order = 0
        row.enabled = True
    elif body.enabled is True and row.fallback_order is None:
        row.fallback_order = await _next_fallback_order(session, exclude=name)

    if new_key:
        row.encrypted_api_key = encrypt_secret(new_key)
        row.key_fingerprint = fingerprint(new_key)
    if body.base_url is not None:
        row.base_url = body.base_url or None
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.model_chat is not None:
        row.model_chat = body.model_chat or None
    if body.model_reasoning is not None:
        row.model_reasoning = body.model_reasoning or None
    if body.model_embedding is not None:
        row.model_embedding = body.model_embedding or None
    if body.model_image is not None:
        row.model_image = body.model_image or None
    if body.fallback_order is not None:
        row.fallback_order = body.fallback_order

    # Auto-promote: enabling a chat-capable provider that can actually work
    # should take over from mock as the default, otherwise users keep getting
    # canned mock replies even though a real provider is turned on.
    if name != "mock" and row.enabled and "chat" in (provider_meta(name)[1]):
        self_usable = _is_usable_for_chat(
            name,
            api_key=decrypt_secret(row.encrypted_api_key) if row.encrypted_api_key else _env_api_key(name),
            base_url=row.base_url or "",
        )
        if self_usable and row.fallback_order not in (0, None):
            active = await _active_chat_provider(session, exclude=name)
            if active is None or active == "mock":
                await _reorder_others(session, exclude=name)
                row.fallback_order = 0
    await record_audit(
        session,
        action="settings.provider_configured",
        actor_id=actor_id,
        resource_type="provider_credential",
        resource_id=name,
        details={
            "updated": {
                "enabled": row.enabled,
                "fallback_order": row.fallback_order,
                "set_default": body.set_default is True,
            }
        },
    )
    await session.commit()
    await session.refresh(row)
    display_name, capabilities, default_model, suggestions, notes = provider_meta_with_guidance(name)
    status = ProviderStatus(
        name=name,
        display_name=display_name,
        description=notes,
        model_suggestions=suggestions,
        chat_unsupported="chat" not in capabilities,
        enabled=row.enabled,
        configured=True,
        key_fingerprint=row.key_fingerprint,
        default_model=default_model,
        model_chat=row.model_chat,
        model_reasoning=row.model_reasoning,
        model_embedding=row.model_embedding,
        model_image=row.model_image,
        base_url=row.base_url,
        fallback_order=row.fallback_order,
        capabilities=list(capabilities),
    )
    statuses = await get_provider_statuses(session)
    status.is_default = next((s.is_default for s in statuses if s.name == name), False)
    return status


async def _next_fallback_order(session: AsyncSession, *, exclude: str) -> int:
    """Highest existing order + 1 so newly enabled providers append to the chain."""
    rows = (
        await session.execute(
            select(ProviderCredential).where(
                ProviderCredential.provider != exclude, ProviderCredential.fallback_order.is_not(None)
            )
        )
    ).scalars().all()
    orders = [r.fallback_order for r in rows if r.fallback_order is not None]
    return (max(orders) + 1) if orders else 0


async def _reorder_others(session: AsyncSession, *, exclude: str) -> None:
    """Bump every ordered provider up by one to make room for the new default at 0."""
    rows = (
        await session.execute(
            select(ProviderCredential).where(
                ProviderCredential.provider != exclude, ProviderCredential.fallback_order.is_not(None)
            )
        )
    ).scalars().all()
    for r in rows:
        r.fallback_order = (r.fallback_order or 0) + 1


def _is_usable_for_chat(name: str, *, api_key: str, base_url: str) -> bool:
    """Whether a provider is actually able to answer chat requests."""
    display_name, capabilities, _ = provider_meta(name)
    if "chat" not in capabilities:
        return False
    if name in {"mock", "venv"}:
        return True
    if name == "ollama":
        return bool(base_url)
    return bool(api_key)


def _status_usable(status: ProviderStatus) -> bool:
    return _is_usable_for_chat(
        status.name,
        api_key="configured" if status.configured else "",
        base_url=status.base_url or "",
    )


def _skip_reason(name: str, *, api_key: str, base_url: str) -> str:
    """Human-readable explanation for why resolve_provider skipped a provider."""
    display_name, capabilities, _ = provider_meta(name)
    if "chat" not in capabilities:
        return f"{display_name} does not support chat in this build"
    if name in {"mock", "venv"}:
        return ""
    if name == "ollama":
        return "Ollama is enabled but no base URL is set" if not base_url else "Ollama base URL set but unreachable"
    return f"{display_name} is enabled but no API key is configured" if not api_key else ""


async def resolve_provider(
    session: AsyncSession,
    *,
    preferred: str | None = None,
    model: str | None = None,
) -> ResolvedProvider:
    """Pick the best chat provider: DB-enabled (by fallback_order) >> env >> mock.

    Providers that are enabled but cannot actually chat (no key set, or no chat
    capability like HuggingFace) are skipped so a broken config never takes the
    whole chat feature down.
    """
    if preferred:
        return ResolvedProvider(
            name=preferred, model=model or provider_meta(preferred)[2], source="preferred"
        )
    rows = (
        (
            await session.execute(
                select(ProviderCredential)
                .where(ProviderCredential.enabled.is_(True))
                .order_by(ProviderCredential.fallback_order.asc().nulls_last())
            )
        )
        .scalars()
        .all()
    )
    mock_fallback: ProviderCredential | None = None
    for row in rows:
        try:
            api_key = (
                decrypt_secret(row.encrypted_api_key)
                if row.encrypted_api_key
                else _env_api_key(row.provider)
            )
        except CredentialUndecryptableError:
            # One unreadable key must not take down chat for everyone; skip it
            # and keep looking, exactly like a provider with no key at all.
            logger.warning(
                "provider_key_unreadable",
                extra={
                    "extra_fields": {
                        "provider": row.provider,
                        "action": "re-enter the API key in Settings",
                    }
                },
            )
            continue
        if _is_usable_for_chat(row.provider, api_key=api_key, base_url=row.base_url or ""):
            if row.provider == "mock":
                mock_fallback = row
                continue
            resolved_model = (
                row.model_chat or provider_meta(row.provider)[2]
                if row.provider != "ollama"
                else await _ollama_chat_model(row.model_chat or None)
            )
            return ResolvedProvider(
                name=row.provider,
                model=model or resolved_model,
                source="db",
            )
        logger.warning(
            "provider_skipped",
            extra={
                "extra_fields": {
                    "provider": row.provider,
                    "reason": _skip_reason(row.provider, api_key=api_key and "yes" or "no", base_url=row.base_url or ""),
                }
            },
        )
    if mock_fallback is not None:
        return ResolvedProvider(
            name="mock",
            model=model or provider_meta("mock")[2],
            source="db",
        )
    for env_name in ("openai", "anthropic", "google"):
        if _env_api_key(env_name) and _is_usable_for_chat(env_name, api_key=_env_api_key(env_name), base_url=""):
            return ResolvedProvider(name=env_name, model=model or provider_meta(env_name)[2], source="env")
    if await _ollama_reachable():
        return ResolvedProvider(
            name="ollama",
            model=model or await _ollama_chat_model(),
            source="env",
        )
    logger.info("provider_fallback", extra={"extra_fields": {"provider": "mock", "reason": "no usable provider"}})
    return ResolvedProvider(name="mock", model=model or provider_meta("mock")[2], source="default")


async def build_resolved(
    session: AsyncSession,
    *,
    preferred: str | None = None,
    model: str | None = None,
) -> tuple[Any, str]:
    """Build a concrete provider instance from the resolved DB credentials."""
    resolved = await resolve_provider(session, preferred=preferred, model=model)
    row = (
        await session.execute(select(ProviderCredential).where(ProviderCredential.provider == resolved.name))
    ).scalar_one_or_none()
    try:
        api_key = (
            decrypt_secret(row.encrypted_api_key)
            if row and row.encrypted_api_key
            else _env_api_key(resolved.name)
        )
    except CredentialUndecryptableError:
        # resolve_provider already skipped these; reaching here means the caller
        # explicitly asked for this provider, so say what to do about it.
        raise CredentialUndecryptableError(
            f"The saved API key for {resolved.name} can no longer be read. "
            "Please re-enter it in Settings."
        ) from None
    base_url = row.base_url if row and row.base_url else ""
    instance = build_provider(resolved.name, api_key=api_key, base_url=base_url, model=resolved.model)
    return instance, resolved.name
