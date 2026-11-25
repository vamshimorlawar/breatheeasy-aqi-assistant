"""PlannerService is tested directly, without HTTP, using the in-memory fakes."""

import asyncio

import pytest

from app.models import AskRequest, PlanRequest
from app.rules import trend
from app.service import DataUnavailableError, InvalidForecastError, PlannerService
from tests.conftest import FakeAdvisor, FakeAirClient, make_payload, make_snapshot


def _plan(body: dict) -> PlanRequest:
    return PlanRequest.model_validate(body)


def test_plan_combines_rules_ai_and_share_message(plan_body):
    service = PlannerService(FakeAirClient(), FakeAdvisor())
    result = asyncio.run(service.plan(_plan(plan_body)))
    assert result.ai_status == "ok" and result.ai is not None
    assert result.baseline.location_label == "Delhi, India"
    assert result.share_message


@pytest.mark.parametrize(("advisor", "status"), [(None, "disabled"), (FakeAdvisor(fail=True), "unavailable")])
def test_plan_without_working_ai_still_returns_rule_plan(plan_body, advisor, status):
    result = asyncio.run(PlannerService(FakeAirClient(), advisor).plan(_plan(plan_body)))
    assert result.ai is None and result.ai_status == status
    assert result.baseline.members


def test_upstream_failure_becomes_data_unavailable(plan_body):
    service = PlannerService(FakeAirClient(fail=True), None)
    with pytest.raises(DataUnavailableError, match="unavailable"):
        asyncio.run(service.plan(_plan(plan_body)))
    with pytest.raises(DataUnavailableError):
        asyncio.run(service.geocode("Delhi"))


def test_browser_forecast_skips_upstream(plan_body):
    air = FakeAirClient(fail=True)
    body = _plan({**plan_body, "air": make_payload([42] * 30)})
    baseline = asyncio.run(PlannerService(air, None).baseline(body))
    assert baseline.current.aqi == 42
    assert air.calls == []


def test_stale_browser_forecast_is_invalid(plan_body):
    payload = make_payload([42] * 3)
    payload["current"]["time"] = "2026-11-18T12:00"
    with pytest.raises(InvalidForecastError, match="out of date"):
        asyncio.run(PlannerService(FakeAirClient(), None).baseline(_plan({**plan_body, "air": payload})))


def test_ask_uses_ai_then_falls_back_to_rules(plan_body):
    body = AskRequest.model_validate({**plan_body, "question": "Can Aarav play outside?"})
    assert asyncio.run(PlannerService(FakeAirClient(), FakeAdvisor()).ask(body)).source == "gemini"
    assert asyncio.run(PlannerService(FakeAirClient(), FakeAdvisor(fail=True)).ask(body)).source == "rules"


def test_known_hour_trend_thresholds():
    assert trend(make_snapshot([200, 180, 170, 160, 150, 140, 130]).hourly) == "improving"
    assert trend(make_snapshot([100, 120, 130, 140, 150, 160, 170]).hourly) == "worsening"
    assert trend(make_snapshot([100, None, None, None, None, None, None]).hourly) == "unknown"
