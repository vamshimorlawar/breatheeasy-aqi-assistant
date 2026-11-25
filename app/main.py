"""FastAPI application: JSON API plus the static, accessible web UI.

Routes stay thin: they validate input (Pydantic), call `PlannerService`, and map
service errors to HTTP status codes.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, cast

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .ai import Advisor, GeminiAdvisor
from .air_client import AirQualityClient
from .config import PROJECT_ROOT, Settings, get_settings
from .models import AskRequest, AskResponse, Place, PlanRequest, PlanResponse
from .security import RateLimiter, SecurityMiddleware, security_headers
from .service import AirSource, DataUnavailableError, InvalidForecastError, PlannerService

logger = logging.getLogger("breatheeasy")
STATIC_DIR = PROJECT_ROOT / "static"


def _build_advisor(settings: Settings) -> Advisor | None:
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set: running with rule-based guidance only.")
        return None
    return GeminiAdvisor(settings.gemini_api_key, list(settings.gemini_models), settings.ai_timeout_seconds)


def get_service(request: Request) -> PlannerService:
    return cast(PlannerService, request.app.state.service)


Service = Annotated[PlannerService, Depends(get_service)]


def create_app(
    settings: Settings | None = None,
    air_client: AirSource | None = None,
    advisor: Advisor | None = None,
) -> FastAPI:
    """App factory. Tests inject fake clients; production builds the real ones."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout_seconds,
            headers={"User-Agent": f"BreatheEasy/{__version__}"},
        ) as http:
            air = air_client or AirQualityClient(http, settings.cache_ttl_seconds)
            ai = advisor if advisor is not None else _build_advisor(settings)
            app.state.service = PlannerService(air, ai)
            yield

    app = FastAPI(
        title="BreatheEasy",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,  # no third-party Swagger assets; keeps the CSP strict
        redoc_url=None,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        SecurityMiddleware,
        limiter=RateLimiter(settings.rate_limit_per_minute),
        headers=security_headers(settings.frame_ancestors),
    )
    _register_error_handlers(app)
    _register_routes(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Return field + message only; never reflect the submitted values back.
        errors = [
            {"loc": [str(p) for p in err.get("loc", ())], "msg": str(err.get("msg", "Invalid value"))}
            for err in exc.errors()[:10]
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(DataUnavailableError)
    async def data_unavailable(_: Request, exc: DataUnavailableError) -> JSONResponse:
        # 502 tells the browser it may retry with a forecast it fetches itself.
        return JSONResponse({"detail": exc.message}, status_code=502)

    @app.exception_handler(InvalidForecastError)
    async def invalid_forecast(_: Request, exc: InvalidForecastError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=422)


def _register_routes(app: FastAPI) -> None:
    @app.get("/api/health")
    async def health(service: Service) -> dict[str, object]:
        return {"status": "ok", "version": __version__, "ai_enabled": service.ai_enabled}

    @app.get("/api/geocode", response_model=list[Place])
    async def geocode(service: Service, q: Annotated[str, Query(min_length=2, max_length=60)]) -> list[Place]:
        return await service.geocode(q)

    @app.post("/api/plan", response_model=PlanResponse)
    async def plan(service: Service, body: PlanRequest) -> PlanResponse:
        return await service.plan(body)

    @app.post("/api/ask", response_model=AskResponse)
    async def ask(service: Service, body: AskRequest) -> AskResponse:
        return await service.ask(body)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")


app = create_app()
