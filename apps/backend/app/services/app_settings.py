"""Non-secret, admin-editable application settings shared by every user.

These live in the ``app_settings`` table so they can be changed at runtime
without a redeploy. Anything safe to expose before sign-in (such as whether the
demo admin button is shown) is served by a public endpoint; the write side is
admin-only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.models import AppSetting

#: Single row holding the publicly readable settings.
PUBLIC_SETTINGS_KEY = "public"
PUBLIC_SETTINGS_GROUP = "general"

#: ``demo_admin_login`` shows the "Admin demo" shortcut on the sign-in page.
DEFAULT_PUBLIC_SETTINGS: dict[str, bool] = {"demo_admin_login": True}

_PUBLIC_KEYS = frozenset(DEFAULT_PUBLIC_SETTINGS)


def _coerce(raw: Any) -> dict[str, Any]:
    """Merge stored values over the defaults, ignoring anything unexpected."""
    merged = dict(DEFAULT_PUBLIC_SETTINGS)
    if isinstance(raw, dict):
        for key in _PUBLIC_KEYS:
            value = raw.get(key)
            if isinstance(value, bool):
                merged[key] = value
    return merged


async def get_public_settings(session: AsyncSession) -> dict[str, bool]:
    """Read the public settings, falling back to defaults on any problem."""
    try:
        row = await session.get(AppSetting, PUBLIC_SETTINGS_KEY)
    except Exception:  # pragma: no cover - defensive, keeps the login page alive
        logger.warning(
            "public_settings_read_failed",
            extra={"extra_fields": {"setting": PUBLIC_SETTINGS_KEY}},
            exc_info=True,
        )
        return dict(DEFAULT_PUBLIC_SETTINGS)
    return _coerce(row.value if row else None)


async def update_public_settings(
    session: AsyncSession,
    values: dict[str, bool],
    *,
    updated_by: Any = None,
) -> dict[str, bool]:
    """Merge ``values`` into the stored public settings and persist them."""
    row = await session.get(AppSetting, PUBLIC_SETTINGS_KEY)
    stored = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    # Only overlay the keys we own, so any other setting stays untouched.
    stored.update({key: value for key, value in values.items() if key in _PUBLIC_KEYS})

    if row is None:
        row = AppSetting(key=PUBLIC_SETTINGS_KEY, value=stored, group=PUBLIC_SETTINGS_GROUP)
        session.add(row)
    else:
        row.value = stored
        row.group = PUBLIC_SETTINGS_GROUP
    if updated_by is not None:
        row.updated_by = updated_by
    await session.commit()

    logger.info(
        "public_settings_updated",
        extra={"extra_fields": {"keys": sorted(values)}},
    )
    return _coerce(stored)
