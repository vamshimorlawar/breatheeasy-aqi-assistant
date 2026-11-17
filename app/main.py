"""FastAPI application: JSON API plus the static, accessible web UI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .ai import Advisor, AdvisorError, GeminiAdvisor
from .air_client import AirQualityClient, UpstreamError
from .config import PROJECT_ROOT, Settings, get_settings
from .models import AskRequest, AskResponse, Baseline, Place, PlanRequest, PlanResponse
from .rules import build_baseline, rules_answer
from .security import RateLimiter, SecurityMiddleware, security_headers
from .share import build_share_message

logger = logging.getLogger("breatheeasy")
STATIC_DIR = PROJECT_ROOT / "static"


def create_app(
    settings: Settings | None = None,
    air_client: AirQualityClient | None = None,
    advisor: Advisor | None = None,
) -> FastAPI:
    """App factory. Tests inject fake clients; production builds the real ones."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        http: httpx.AsyncClient | None = None
        if air_client is None:
            http = httpx.AsyncClient(
                timeout=settings.http_timeout_seconds,
                headers={"User-Agent": f"BreatheEasy/{__version__}"},
            )
            app.state.air = AirQualityClient(http, settings.cache_ttl_seconds)
        else:
            app.state.air = air_client
        if advisor is not None:
            app.state.advisor = advisor
        elif settings.ai_enabled:
            app.state.advisor = GeminiAdvisor(
                settings.gemini_api_key or "", list(settings.gemini_models), settings.ai_timeout_seconds
            )
        else:
            app.state.advisor = None
            logger.warning("GEMINI_API_KEY not set: running with rule-based guidance only.")
        try:
            yield
        finally:
            if http is not None:
                await http.aclose()

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

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Return field + message only; never reflect the submitted values back.
        errors = [
            {"loc": [str(p) for p in err.get("loc", ())], "msg": str(err.get("msg", "Invalid value"))}
            for err in exc.errors()[:10]
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    async def _baseline(request: Request, body: PlanRequest) -> Baseline:
        try:
            snapshot = await request.app.state.air.get_snapshot(body.location.latitude, body.location.longitude)
        except UpstreamError as exc:
            logger.warning("Air-quality lookup failed: %s", exc)
            raise HTTPException(
                status_code=502, detail="Air-quality data is unavailable right now. Please retry."
            ) from exc
        label = body.location.label or "your area"
        return build_baseline(snapshot, body.members, body.home, label)

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, object]:
        return {"status": "ok", "version": __version__, "ai_enabled": request.app.state.advisor is not None}

    @app.get("/api/geocode", response_model=list[Place])
    async def geocode(request: Request, q: str = Query(min_length=2, max_length=60)) -> list[Place]:
        try:
            return await request.app.state.air.geocode(q)
        except UpstreamError as exc:
            raise HTTPException(status_code=502, detail="Location search is unavailable right now.") from exc

    @app.post("/api/plan", response_model=PlanResponse)
    async def plan(request: Request, body: PlanRequest) -> PlanResponse:
        baseline = await _baseline(request, body)
        advisor: Advisor | None = request.app.state.advisor
        ai_plan, ai_status = None, "disabled"
        if advisor is not None:
            try:
                ai_plan, ai_status = await advisor.personalize_plan(baseline, body.language), "ok"
            except AdvisorError as exc:
                logger.warning("AI plan unavailable: %s", exc)
                ai_status = "unavailable"
        return PlanResponse(
            baseline=baseline,
            ai=ai_plan,
            ai_status=ai_status,  # type: ignore[arg-type]
            share_message=build_share_message(baseline, body.language),
        )

    @app.post("/api/ask", response_model=AskResponse)
    async def ask(request: Request, body: AskRequest) -> AskResponse:
        baseline = await _baseline(request, body)
        advisor: Advisor | None = request.app.state.advisor
        if advisor is not None:
            try:
                return AskResponse(answer=await advisor.answer(body.question, baseline, body.language), source="gemini")
            except AdvisorError as exc:
                logger.warning("AI answer unavailable: %s", exc)
        return AskResponse(answer=rules_answer(baseline), source="rules")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
