"""HTTP middleware (T008).

``CorrelationIdMiddleware`` assigns/propagates an ``X-Correlation-Id`` for every request so
that logs and audit records can be tied to a single call (FR-061).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import CORRELATION_ID_HEADER


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cid = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = cid
        return response
