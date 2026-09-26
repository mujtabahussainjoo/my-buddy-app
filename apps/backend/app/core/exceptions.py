"""Consistent error envelope and FastAPI exception handlers.

Every error returned by the API uses the shape:
    {"error": {"code", "message", "request_id", "details"?}, "data": null}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import logger, request_id_var


class AppError(Exception):
    """Base application error carrying an HTTP status and stable error code."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.request_id: str | None = None


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class AlreadyExistsError(AppError):
    status_code = 409
    code = "already_exists"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class ConfigurationError(AppError):
    status_code = 503
    code = "misconfigured"


class CredentialUndecryptableError(AppError):
    """A stored provider key cannot be read with the current encryption secret."""

    status_code = 400
    code = "credential_undecryptable"


class ExternalServiceError(AppError):
    status_code = 502
    code = "external_service_error"


class InsufficientEvidenceError(AppError):
    status_code = 200
    code = "insufficient_evidence"


def _envelope(
    request: Request, status_code: int, code: str, message: str, details: Any = None
) -> dict[str, Any]:
    request_id = request_id_var.get() or "unknown"
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details,
        },
        "data": None,
    }


def _json(request: Request, status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=_envelope(request, status_code, code, message, details)
    )


def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    # Put the reason in the message itself so it is readable in a plain `docker
    # logs` tail, not only in the admin panel's extra fields.
    logger.warning(
        f"app_error: {exc.message}",
        extra={
            "extra_fields": {
                "code": exc.code,
                "status": exc.status_code,
                "detail": exc.message,
                "path": request.url.path,
            }
        },
    )
    return _json(request, exc.status_code, exc.code, exc.message, exc.details)


def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = "Resource not found" if exc.status_code == 404 else str(exc.detail) or "HTTP error"
    logger.warning(
        f"http_error: {message}",
        extra={
            "extra_fields": {
                "code": "http_error",
                "status": exc.status_code,
                "detail": message,
                "path": request.url.path,
            }
        },
    )
    return _json(request, exc.status_code, "http_error", message)


def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    details = [
        {
            "location": ".".join(str(p) for p in err.get("loc", [])),
            "type": err.get("type"),
            "msg": err.get("msg"),
        }
        for err in errors
    ]
    first = f"{details[0]['location']}: {details[0]['msg']}" if details else "Request validation failed"
    logger.warning(
        f"validation_error: {first}",
        extra={
            "extra_fields": {
                "code": "validation_error",
                "status": 422,
                "detail": "Request validation failed",
                "path": request.url.path,
                "fields": "; ".join(f"{d['location']} ({d['msg']})" for d in details[:5]),
            }
        },
    )
    return _json(request, 422, "validation_error", "Request validation failed", details)


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    retry_after = getattr(exc, "retry_after", None)
    return _json(request, 429, "rate_limited", "Rate limit exceeded", {"retry_after": retry_after})


def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    # ``logger.exception`` only works inside an ``except`` block, and a FastAPI
    # exception handler is not one, so pass the exception in explicitly.
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        extra={
            "extra_fields": {
                "path": request.url.path,
                "error_type": type(exc).__name__,
                "error": str(exc) or repr(exc),
            }
        },
    )
    return _json(request, 500, "internal_error", "Internal server error")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_handler)
