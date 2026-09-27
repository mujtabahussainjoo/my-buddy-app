"""Admin-only endpoints: logs, user management."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import require_admin
from app.core.exceptions import AuthError, ForbiddenError, NotFoundError
from app.core.logging import recent_logs
from app.core.security import hash_password
from app.db.models import User
from app.db.repositories.user import RoleRepository, UserRepository
from app.db.session import get_db
from app.schemas.common import envelope
from app.services import app_settings as app_settings_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/logs", response_model=dict[str, Any])
async def list_recent_logs(
    _: Any = Depends(require_admin),
    min_level: str = Query("WARNING", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
    limit: int = Query(100, ge=1, le=400),
) -> dict[str, Any]:
    """Return the most recent buffered log records (admin only)."""
    entries = recent_logs(min_level=min_level, limit=limit)
    return envelope(entries)


# ── User management ─────────────────────────────────────────────────────────


class AdminCreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=80)
    roles: list[str] = Field(
        default=["user"],
        description='Role names, e.g. ["admin"] or ["user","admin"].',
    )

    @field_validator("password")
    @classmethod
    def password_secure(cls, value: str) -> str:
        if not any(c.isupper() for c in value):
            raise ValueError("password must contain an uppercase letter")
        if not any(c.isdigit() for c in value):
            raise ValueError("password must contain a digit")
        return value

    @field_validator("roles")
    @classmethod
    def roles_not_empty(cls, value: list[str]) -> list[str]:
        valid = {"admin", "user"}
        bad = set(value) - valid
        if bad:
            raise ValueError(f"Invalid roles: {sorted(bad)}. Allowed: {sorted(valid)}")
        return value


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    roles: list[str]
    is_active: bool
    created_at: Any

    model_config = {"from_attributes": True}

    @field_validator("roles", mode="before")
    @classmethod
    def roles_to_names(cls, value: Any) -> list[str] | Any:
        if value and not isinstance(value[0], str):
            return [role.name for role in value]
        return value


@router.get("/users", response_model=dict[str, Any])
async def list_users(
    _: Any = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all users with their roles (admin only)."""
    users = await UserRepository(db).list_all()
    items = [AdminUserOut.model_validate(u).model_dump(mode="json") for u in users]
    return envelope(items)


@router.post("/users", response_model=dict[str, Any])
async def create_user(
    body: AdminCreateUserRequest,
    _: Any = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a user and assign roles (admin only)."""
    user_repo = UserRepository(db)
    if await user_repo.get_by_email(body.email):
        raise AuthError("An account already exists for this email")

    role_repo = RoleRepository(db)
    roles = await role_repo.get_by_names(body.roles)
    known = {r.name for r in roles}
    missing = set(body.roles) - known
    if missing:
        raise AuthError(f"Required roles not seeded: {sorted(missing)}")

    user = await user_repo.create(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        roles=roles,
    )
    await db.flush()
    await db.commit()
    await db.refresh(user)
    return envelope(AdminUserOut.model_validate(user).model_dump(mode="json"))


@router.delete("/users/{user_id}", response_model=dict[str, Any])
async def deactivate_user(
    user_id: uuid.UUID,
    _: Any = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Deactivate a user account (admin only). Does not allow self-deactivation."""
    current_user = await UserRepository(db).get(user_id)
    if current_user is None:
        raise NotFoundError("User not found")
    if current_user.is_active is False:
        return envelope({"status": "already_deactivated"})
    current_user.is_active = False
    await db.commit()
    return envelope({"status": "deactivated", "user_id": str(user_id)})


class AdminUpdateUserRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    roles: list[str] | None = Field(
        default=None,
        description="Full role set to assign. Omit to leave roles unchanged.",
    )
    is_active: bool | None = Field(default=None)
    password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_secure(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not any(c.isupper() for c in value):
            raise ValueError("password must contain an uppercase letter")
        if not any(c.isdigit() for c in value):
            raise ValueError("password must contain a digit")
        return value

    @field_validator("roles")
    @classmethod
    def roles_valid(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        valid = {"admin", "user"}
        bad = set(value) - valid
        if bad:
            raise ValueError(f"Invalid roles: {sorted(bad)}. Allowed: {sorted(valid)}")
        if not value:
            raise ValueError("roles cannot be empty")
        return value


@router.patch("/users/{user_id}", response_model=dict[str, Any])
async def update_user(
    user_id: uuid.UUID,
    body: AdminUpdateUserRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update another account's display name, roles, active status, or password (admin only)."""
    user_repo = UserRepository(db)
    target = await user_repo.get(user_id)
    if target is None:
        raise NotFoundError("User not found")

    is_self = target.id == current_user.id
    if is_self and body.is_active is False:
        raise ForbiddenError("You cannot deactivate your own account")
    if is_self and body.roles is not None and "admin" not in body.roles:
        raise ForbiddenError("You cannot remove your own admin role")

    if body.display_name is not None:
        target.display_name = body.display_name.strip() or None

    if body.roles is not None:
        role_repo = RoleRepository(db)
        roles = await role_repo.get_by_names(body.roles)
        known = {r.name for r in roles}
        missing = set(body.roles) - known
        if missing:
            raise AuthError(f"Required roles not seeded: {sorted(missing)}")
        target.roles = roles

    if body.is_active is not None:
        target.is_active = body.is_active

    if body.password is not None:
        target.password_hash = hash_password(body.password)

    await db.commit()
    await db.refresh(target)
    return envelope(AdminUserOut.model_validate(target).model_dump(mode="json"))


# ── Application settings ────────────────────────────────────────────────────


class PublicSettingsUpdate(BaseModel):
    demo_admin_login: bool = Field(
        description="Show the “Admin demo” shortcut button on the sign-in page.",
    )


@router.get("/settings", response_model=dict[str, Any])
async def read_public_settings(
    _: Any = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the runtime settings an admin can change (admin only)."""
    return envelope(await app_settings_service.get_public_settings(db))


@router.patch("/settings", response_model=dict[str, Any])
async def patch_public_settings(
    body: PublicSettingsUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update the runtime settings; only the supplied keys change."""
    updated = await app_settings_service.update_public_settings(
        db,
        {"demo_admin_login": body.demo_admin_login},
        updated_by=current_user.id,
    )
    return envelope(updated)
