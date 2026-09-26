"""Unit tests for the provider registry, mock provider, and secret encryption."""

from __future__ import annotations

import asyncio

import pytest

from app.ai.base import AIProviderError, ChatMessage
from app.ai.encryption import decrypt_secret, encrypt_secret, fingerprint
from app.ai.registry import build_provider, list_provider_names
from app.services.providers import _is_usable_for_chat, _skip_reason


def test_provider_registry_has_expected_backends() -> None:
    names = set(list_provider_names())
    assert {"mock", "openai", "anthropic", "ollama", "google", "venv"}.issubset(names)


def test_build_provider_unknown() -> None:
    with pytest.raises(AIProviderError):
        build_provider("does-not-exist")


def test_secret_encryption_roundtrip() -> None:
    blob = encrypt_secret("sk-secret-123")
    assert blob != b"sk-secret-123"
    assert decrypt_secret(blob) == "sk-secret-123"
    assert fingerprint("sk-secret-123") == fingerprint("sk-secret-123")


def test_mock_chat_returns_echo() -> None:
    async def run() -> str:
        provider = build_provider("mock")
        response = await provider.chat([ChatMessage(role="user", content="hello world")])
        return response.content

    content = asyncio.run(run())
    assert "hello world" in content


def test_mock_embeddings_shape() -> None:
    async def run() -> list[list[float]]:
        provider = build_provider("mock")
        return await provider.embed(["alpha", "beta beta"])

    vectors = asyncio.run(run())
    assert len(vectors) == 2
    assert len(vectors[0]) == 384


def test_is_usable_for_chat_mock_always_true() -> None:
    assert _is_usable_for_chat("mock", api_key="", base_url="") is True


def test_is_usable_for_chat_apikey_provider_requires_key() -> None:
    assert _is_usable_for_chat("openai", api_key="", base_url="") is False
    assert _is_usable_for_chat("openai", api_key="sk-test", base_url="") is True


def test_is_usable_for_chat_hf_no_chat_capability() -> None:
    assert _is_usable_for_chat("huggingface", api_key="hf_token", base_url="") is False
    assert _skip_reason("huggingface", api_key="hf_token", base_url="") == (
        "Hugging Face does not support chat in this build"
    )


def test_skip_reason_openai_missing_key() -> None:
    assert _skip_reason("openai", api_key="", base_url="") == (
        "OpenAI is enabled but no API key is configured"
    )


def test_a_key_encrypted_with_another_secret_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed JWT_SECRET_KEY must produce an actionable message, not a crash."""
    from app.ai import encryption
    from app.core.config import settings
    from app.core.exceptions import CredentialUndecryptableError

    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "secret-number-one")
    blob = encryption.encrypt_secret("sk-secret-123")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "secret-number-two")

    with pytest.raises(CredentialUndecryptableError) as caught:
        encryption.decrypt_secret(blob)

    assert "re-enter" in caught.value.message
    assert caught.value.status_code == 400
    assert caught.value.code == "credential_undecryptable"


def test_an_unreadable_key_is_skipped_instead_of_breaking_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One corrupt credential must not take chat down; the next provider is used."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.core.config import settings
    from app.services import providers

    unreadable = SimpleNamespace(
        provider="openai",
        encrypted_api_key=b"garbage-that-was-encrypted-with-another-key",
        base_url=None,
        enabled=True,
        fallback_order=0,
        model_chat=None,
    )
    mock_row = SimpleNamespace(
        provider="mock",
        encrypted_api_key=None,
        base_url=None,
        enabled=True,
        fallback_order=1,
        model_chat=None,
    )

    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "secret-number-two")
    scalars = MagicMock()
    scalars.all.return_value = [unreadable, mock_row]
    result = MagicMock()
    result.scalars.return_value = scalars

    class Session:
        async def execute(self, *_args: object, **_kwargs: object) -> MagicMock:
            return result

    resolved = asyncio.run(
        providers.resolve_provider(Session(), preferred=None, model=None)  # type: ignore[arg-type]
    )

    assert resolved.name == "mock", "should fall through to the mock provider"


def test_dev_secret_is_stable_across_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """A generated secret must be reused, or every saved key breaks on restart."""
    from app.core import config

    secret_file = tmp_path / "dev-jwt-secret"  # type: ignore[operator]
    monkeypatch.setattr(config, "DEV_SECRET_FILE", secret_file)

    first = config._load_or_create_dev_secret()
    second = config._load_or_create_dev_secret()

    assert first == second
    assert len(first) > 32
    assert secret_file.read_text(encoding="utf-8").strip() == first


def test_an_existing_dev_secret_file_is_reused(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    from app.core import config

    secret_file = tmp_path / "dev-jwt-secret"  # type: ignore[operator]
    secret_file.write_text("already-set-secret\n", encoding="utf-8")
    monkeypatch.setattr(config, "DEV_SECRET_FILE", secret_file)

    assert config._load_or_create_dev_secret() == "already-set-secret"
