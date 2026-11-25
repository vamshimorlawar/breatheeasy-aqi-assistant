"""Security helpers: per-client rate limiting and HTTP security headers."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


def security_headers(frame_ancestors: str = "'none'") -> dict[str, str]:
    """Strict default headers. `frame_ancestors` lists origins allowed to embed the app
    (e.g. a hosting dashboard); by default nobody may frame it."""
    headers = {
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            # The browser may fetch the public forecast directly if the server's quota is exhausted.
            "connect-src 'self' https://air-quality-api.open-meteo.com; "
            "font-src 'self'; object-src 'none'; base-uri 'none'; "
            f"frame-ancestors {frame_ancestors}; form-action 'self'"
        ),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(self), camera=(), microphone=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if frame_ancestors == "'none'":
        headers["X-Frame-Options"] = "DENY"  # legacy browsers; CSP frame-ancestors covers the rest
    return headers


SECURITY_HEADERS = security_headers()
UNLIMITED_PATHS = frozenset({"/api/health"})  # uptime checks must never be throttled


class RateLimiter:
    """Sliding-window limiter kept in memory (enough for a single-instance deployment)."""

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        max_clients: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limit = limit
        self.window = window_seconds
        self.max_clients = max_clients
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_clients:
                self._evict(now)
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def _evict(self, now: float) -> None:
        stale = [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]
        for key in stale:
            del self._hits[key]
        if len(self._hits) >= self.max_clients:  # still full: drop the oldest clients
            for key in list(self._hits)[: self.max_clients // 10 or 1]:
                del self._hits[key]


class SecurityMiddleware(BaseHTTPMiddleware):
    """Rate-limits API calls per client and adds security headers to every response."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter, headers: dict[str, str] | None = None):
        super().__init__(app)
        self.limiter = limiter
        self.headers = headers or SECURITY_HEADERS

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        is_api = path.startswith("/api/")
        if is_api and path not in UNLIMITED_PATHS and not self.limiter.allow(_client_key(request)):
            response: Response = JSONResponse(
                {"detail": "Too many requests. Please wait a minute and try again."},
                status_code=429,
                headers={"Retry-After": str(int(self.limiter.window))},
            )
        else:
            response = await call_next(request)
        for name, value in self.headers.items():
            response.headers.setdefault(name, value)
        if is_api:
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"
