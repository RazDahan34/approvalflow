"""ASGI middleware that establishes the correlation id for each request."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .correlation import CORRELATION_ID_HEADER, ensure_correlation_id


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cid = ensure_correlation_id(request.headers.get(CORRELATION_ID_HEADER))
        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = cid
        return response
