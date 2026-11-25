"""Use-case layer: turns validated requests into plans and answers.

Kept free of HTTP details so it can be tested directly; `main.py` only maps
these results and errors onto routes and status codes.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .ai import Advisor, AdvisorError
from .air_client import AirSnapshot, UpstreamError, parse_air_payload
from .models import AiPlan, AiStatus, AskRequest, AskResponse, Baseline, Place, PlanRequest, PlanResponse
from .rules import build_baseline, rules_answer
from .share import build_share_message

logger = logging.getLogger("breatheeasy")
DEFAULT_PLACE_LABEL = "your area"


class AirSource(Protocol):
    """Anything that can provide forecasts and place search (real client or test fake)."""

    async def get_snapshot(self, latitude: float, longitude: float) -> AirSnapshot: ...

    async def geocode(self, query: str) -> list[Place]: ...


class ServiceError(Exception):
    """Base error carrying a safe, user-facing message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DataUnavailableError(ServiceError):
    """The upstream data provider failed (the browser may retry with its own forecast)."""


class InvalidForecastError(ServiceError):
    """A browser-supplied forecast passed the schema but cannot be used."""


class PlannerService:
    def __init__(self, air: AirSource, advisor: Advisor | None):
        self._air = air
        self._advisor = advisor

    @property
    def ai_enabled(self) -> bool:
        return self._advisor is not None

    async def geocode(self, query: str) -> list[Place]:
        try:
            return await self._air.geocode(query)
        except UpstreamError as exc:
            raise DataUnavailableError("Location search is unavailable right now.") from exc

    async def baseline(self, body: PlanRequest) -> Baseline:
        snapshot = await self._snapshot(body)
        label = body.location.label or DEFAULT_PLACE_LABEL
        return build_baseline(snapshot, body.members, body.home, label)

    async def plan(self, body: PlanRequest) -> PlanResponse:
        baseline = await self.baseline(body)
        ai_plan, ai_status = await self._personalize(baseline, body)
        return PlanResponse(
            baseline=baseline,
            ai=ai_plan,
            ai_status=ai_status,
            share_message=build_share_message(baseline, body.language),
        )

    async def ask(self, body: AskRequest) -> AskResponse:
        baseline = await self.baseline(body)
        if self._advisor is not None:
            try:
                answer = await self._advisor.answer(body.question, baseline, body.language)
                return AskResponse(answer=answer, source="gemini")
            except AdvisorError as exc:
                logger.warning("AI answer unavailable: %s", exc)
        return AskResponse(answer=rules_answer(baseline), source="rules")

    async def _snapshot(self, body: PlanRequest) -> AirSnapshot:
        if body.air is not None:
            # Browser-fetched forecast (server quota exhausted): schema-validated, parsed like upstream data.
            try:
                snapshot = parse_air_payload(body.air.model_dump())
            except UpstreamError as exc:
                raise InvalidForecastError("The forecast data could not be used.") from exc
            if not snapshot.hourly:
                raise InvalidForecastError("The forecast data is out of date. Please retry.")
            return snapshot
        try:
            return await self._air.get_snapshot(body.location.latitude, body.location.longitude)
        except UpstreamError as exc:
            logger.warning("Air-quality lookup failed: %s", exc)
            raise DataUnavailableError("Air-quality data is unavailable right now. Please retry.") from exc

    async def _personalize(self, baseline: Baseline, body: PlanRequest) -> tuple[AiPlan | None, AiStatus]:
        if self._advisor is None:
            return None, "disabled"
        try:
            return await self._advisor.personalize_plan(baseline, body.language), "ok"
        except AdvisorError as exc:
            logger.warning("AI plan unavailable: %s", exc)
            return None, "unavailable"
