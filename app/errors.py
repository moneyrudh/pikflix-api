"""
Structured error handling inspired by OpenClaw's error envelope pattern.

Every error response — whether from Pydantic validation, an HTTPException,
or an unhandled crash — is wrapped in a consistent JSON shape:

    {"error": {"message": "...", "type": "...", "code": "..."}}

This makes client-side error handling predictable: the frontend can always
check `response.error.type` without guessing the shape of the payload.
"""

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("pikflix.errors")


def _build_error(
    message: str,
    error_type: str,
    code: Optional[str] = None,
    details: Optional[Any] = None,
    request_id: Optional[str] = None,
) -> dict:
    body: dict = {
        "error": {
            "message": message,
            "type": error_type,
        }
    }
    if code:
        body["error"]["code"] = code
    if details:
        body["error"]["details"] = details
    if request_id:
        body["request_id"] = request_id
    return body


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content=_build_error(
            message=str(exc.detail),
            error_type="http_error",
            request_id=request_id,
        ),
    )


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.warning(
        "Validation error on %s: %s",
        request.url.path,
        exc.errors(),
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_build_error(
            message="Request validation failed",
            error_type="validation_error",
            code="VALIDATION_FAILED",
            details=exc.errors(),
            request_id=request_id,
        ),
    )


async def _generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "Unhandled exception on %s",
        request.url.path,
        extra={"request_id": request_id},
    )
    # Never leak internal details — just return a generic message
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_build_error(
            message="An unexpected error occurred",
            error_type="internal_server_error",
            request_id=request_id,
        ),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Wire all error handlers onto the FastAPI app."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _generic_exception_handler)
