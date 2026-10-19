"""Error envelope of the admin API: {"error": {"code", "message", "details"}} (docs/api.md).

Only paths under /api/ use it. The webhook and public pages keep FastAPI's plain responses.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_failed",
    429: "rate_limited",
    502: "upstream_error",
}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details or []
        self.headers = headers


def validation_details(errors: Any) -> list[dict[str, Any]]:
    """loc, msg and type only. Never the rejected input: it may be a token or a password."""
    return [
        {
            "loc": [str(p) for p in error.get("loc", ())],
            "msg": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        }
        for error in errors
    ]


def invalid(exc: ValidationError) -> ApiError:
    details = validation_details(exc.errors(include_input=False, include_url=False))
    return ApiError(422, "invalid input", details=details)


def _envelope(
    status_code: int,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {
        "error": {
            "code": _CODES.get(status_code, "error"),
            "message": message,
            "details": details or [],
        }
    }
    return JSONResponse(body, status_code=status_code, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    async def on_api_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ApiError)  # noqa: S101
        return _envelope(exc.status_code, exc.message, exc.details, exc.headers)

    async def on_http(request: Request, exc: Exception) -> Any:
        assert isinstance(exc, StarletteHTTPException)  # noqa: S101
        if not request.url.path.startswith("/api/"):
            return await http_exception_handler(request, exc)
        message = exc.detail if isinstance(exc.detail, str) else _CODES.get(exc.status_code, "")
        return _envelope(exc.status_code, message, headers=getattr(exc, "headers", None))

    async def on_validation(request: Request, exc: Exception) -> Any:
        assert isinstance(exc, RequestValidationError)  # noqa: S101
        if not request.url.path.startswith("/api/"):
            return await request_validation_exception_handler(request, exc)
        return _envelope(422, "invalid input", validation_details(exc.errors()))

    app.add_exception_handler(ApiError, on_api_error)
    app.add_exception_handler(StarletteHTTPException, on_http)
    app.add_exception_handler(RequestValidationError, on_validation)
