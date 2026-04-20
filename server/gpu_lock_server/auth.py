"""Bearer-token authentication middleware.

Enforced on every endpoint except `/health` when `GPU_LOCK_TOKEN` is set.
If the env var is empty, auth is disabled and all requests pass through.

Compares via `secrets.compare_digest` to sidestep timing attacks. Accepts
`Authorization: Bearer <token>` and, as a fallback for curl-friendly scripting,
`X-Api-Key: <token>`.
"""
from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

_PUBLIC_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str | None) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if self._token is None or request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        provided = _extract_token(request)
        if provided is None:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        if not secrets.compare_digest(provided, self._token):
            return JSONResponse({"detail": "invalid token"}, status_code=403)
        return await call_next(request)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
            return parts[1]
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key
    return None
