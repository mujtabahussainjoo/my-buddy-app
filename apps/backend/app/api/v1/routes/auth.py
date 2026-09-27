"""Authentication endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user
from app.core.config import settings
from app.core.limits import limiter
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserProfile,
)
from app.schemas.common import envelope
from app.services import app_settings as app_settings_service
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/public-settings", response_model=dict[str, Any])
async def public_settings(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Settings the sign-in page needs before anyone is authenticated."""
    return envelope(await app_settings_service.get_public_settings(session))



def _client_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": get_remote_address(request),
        "user_agent": request.headers.get("User-Agent"),
    }


@router.post("/register", response_model=dict[str, Any])
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await auth_service.register_user(
        db,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        ip_address=_client_context(request)["ip_address"],
        requested_roles=body.roles,
    )
    await db.refresh(user)
    return envelope(UserProfile.model_validate(user))


@router.post("/login", response_model=dict[str, Any])
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ctx = _client_context(request)
    tokens = await auth_service.login_user(
        db,
        email=body.email,
        password=body.password,
        ip_address=ctx["ip_address"],
        user_agent=ctx["user_agent"],
    )
    return envelope(TokenPair(**tokens))


@router.post("/refresh", response_model=dict[str, Any])
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tokens = await auth_service.refresh_tokens(
        db,
        refresh_token=body.refresh_token,
        ip_address=_client_context(request)["ip_address"],
    )
    return envelope(TokenPair(**tokens))


@router.get("/me", response_model=dict[str, Any])
async def me(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return envelope(UserProfile.model_validate(current_user))


@router.post("/change-password", response_model=dict[str, Any])
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await auth_service.change_password(
        db,
        user=current_user,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return envelope({"status": "ok"})
