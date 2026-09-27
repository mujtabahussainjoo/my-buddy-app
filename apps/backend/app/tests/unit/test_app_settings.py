"""Unit tests for the runtime app settings shared across users."""

from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import app_settings as svc


class FakeSession:
    """Minimal stand-in for AsyncSession that only touches AppSetting rows."""

    def __init__(self, stored: dict[str, Any] | None = None) -> None:
        self.rows: dict[str, Any] = dict(stored or {})
        self.committed = 0

    async def get(self, model: type, key: str) -> Any:
        return self.rows.get(key)

    def add(self, row: Any) -> None:
        self.rows[row.key] = row

    async def commit(self) -> None:
        self.committed += 1


def make_row(value: dict[str, Any]) -> Any:
    row = type("Row", (), {})()
    row.key = svc.PUBLIC_SETTINGS_KEY
    row.value = value
    row.group = None
    row.updated_by = None
    return row


def test_defaults_apply_when_nothing_is_stored() -> None:
    session = FakeSession()
    result = asyncio_run(svc.get_public_settings(as_session(session)))
    assert result == {"demo_admin_login": True}


def test_stored_value_overrides_the_default() -> None:
    session = FakeSession({svc.PUBLIC_SETTINGS_KEY: make_row({"demo_admin_login": False})})
    result = asyncio_run(svc.get_public_settings(as_session(session)))
    assert result == {"demo_admin_login": False}


def test_missing_keys_fall_back_to_defaults() -> None:
    session = FakeSession({svc.PUBLIC_SETTINGS_KEY: make_row({"something_else": True})})
    result = asyncio_run(svc.get_public_settings(as_session(session)))
    assert result == {"demo_admin_login": True}


def test_garbage_values_are_ignored() -> None:
    session = FakeSession(
        {svc.PUBLIC_SETTINGS_KEY: make_row({"demo_admin_login": "yes-please"})}
    )
    result = asyncio_run(svc.get_public_settings(as_session(session)))
    assert result == {"demo_admin_login": True}, "non-boolean values must not be trusted"


def test_database_failure_falls_back_to_defaults() -> None:
    class Broken(FakeSession):
        async def get(self, model: type, key: str) -> Any:
            raise RuntimeError("database is down")

    result = asyncio_run(svc.get_public_settings(as_session(Broken())))
    assert result == {"demo_admin_login": True}, "the login page must still render"


def test_update_creates_the_row_and_persists() -> None:
    session = FakeSession()
    result = asyncio_run(
        svc.update_public_settings(as_session(session), {"demo_admin_login": False}, updated_by=None)
    )
    assert result == {"demo_admin_login": False}
    assert session.committed == 1
    assert session.rows[svc.PUBLIC_SETTINGS_KEY].value == {"demo_admin_login": False}


def test_update_merges_and_keeps_other_keys() -> None:
    session = FakeSession(
        {svc.PUBLIC_SETTINGS_KEY: make_row({"demo_admin_login": True, "unrelated": 1})}
    )
    asyncio_run(svc.update_public_settings(as_session(session), {"demo_admin_login": False}))
    stored = session.rows[svc.PUBLIC_SETTINGS_KEY].value
    assert stored["demo_admin_login"] is False
    assert stored["unrelated"] == 1, "unrelated stored keys survive"


def test_update_rejects_unknown_keys() -> None:
    session = FakeSession()
    result = asyncio_run(
        svc.update_public_settings(as_session(session), {"is_admin_secret": False})
    )
    assert result == {"demo_admin_login": True}, "unknown keys are not written"


def test_update_records_who_changed_it() -> None:
    import uuid

    session = FakeSession()
    who = uuid.uuid4()
    asyncio_run(svc.update_public_settings(as_session(session), {"demo_admin_login": False}, updated_by=who))
    assert session.rows[svc.PUBLIC_SETTINGS_KEY].updated_by == who


def as_session(fake: Any) -> AsyncSession:
    """The fake only implements what the service touches."""
    return cast(AsyncSession, fake)


def asyncio_run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


@pytest.mark.parametrize("flag", [True, False])
def test_flag_round_trips_both_ways(flag: bool) -> None:
    session = FakeSession()
    asyncio_run(svc.update_public_settings(as_session(session), {"demo_admin_login": flag}))
    assert asyncio_run(svc.get_public_settings(as_session(session))) == {"demo_admin_login": flag}
