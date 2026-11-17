from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.ai import AdvisorError
from app.air_client import AirSnapshot, UpstreamError, parse_air_payload
from app.config import Settings
from app.main import create_app
from app.models import AiPlan, Baseline, MemberNote, Place

START = datetime(2026, 11, 17, 6, 0)


def make_payload(aqis: list[int | None], start: datetime = START, current_minute: int = 30) -> dict:
    """Build an Open-Meteo-shaped payload with hourly AQI values starting at `start`."""
    times = [(start + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(len(aqis))]
    return {
        "timezone": "Asia/Kolkata",
        "current": {
            "time": start.replace(minute=current_minute).isoformat(timespec="minutes"),
            "us_aqi": aqis[0],
            "pm2_5": 80.5,
            "pm10": 120.0,
        },
        "hourly": {
            "time": times,
            "us_aqi": aqis,
            "pm2_5": [None if a is None else a / 2 for a in aqis],
            "pm10": [None if a is None else a for a in aqis],
        },
    }


def make_snapshot(aqis: list[int | None], start: datetime = START) -> AirSnapshot:
    return parse_air_payload(make_payload(aqis, start))


class FakeAirClient:
    def __init__(self, snapshot: AirSnapshot | None = None, fail: bool = False):
        self.snapshot = snapshot or make_snapshot([160] * 24)
        self.fail = fail
        self.calls: list[tuple[float, float]] = []

    async def get_snapshot(self, latitude: float, longitude: float) -> AirSnapshot:
        self.calls.append((latitude, longitude))
        if self.fail:
            raise UpstreamError("down")
        return self.snapshot

    async def geocode(self, query: str) -> list[Place]:
        if self.fail:
            raise UpstreamError("down")
        return [Place(name="Delhi", admin1="Delhi", country="India", latitude=28.65, longitude=77.23)]


class FakeAdvisor:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.questions: list[str] = []

    async def personalize_plan(self, baseline: Baseline, language: str) -> AiPlan:
        if self.fail:
            raise AdvisorError("boom")
        return AiPlan(
            headline="Unhealthy air today: keep the kids indoors",
            summary="Stay inside and run the purifier.",
            member_notes=[MemberNote(nickname=m.nickname, note="Take care!") for m in baseline.members],
        )

    async def answer(self, question: str, baseline: Baseline, language: str) -> str:
        if self.fail:
            raise AdvisorError("boom")
        self.questions.append(question)
        return f"AI answer in {language}"


def make_settings(**overrides) -> Settings:
    values = dict(
        gemini_api_key=None,
        gemini_model="gemini-2.5-flash",
        ai_timeout_seconds=5,
        cache_ttl_seconds=60,
        rate_limit_per_minute=1000,
        http_timeout_seconds=5,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def plan_body() -> dict:
    return {
        "location": {"latitude": 28.6519, "longitude": 77.2315, "label": "Delhi, India"},
        "members": [
            {"nickname": "Aarav", "age_group": "child", "risk_factors": ["asthma"], "activities": ["school_commute"]},
            {"nickname": "Mom", "age_group": "adult", "activities": ["walk"]},
        ],
        "home": {"has_purifier": True, "has_n95": False, "has_ac": False},
        "language": "en",
    }


@pytest.fixture
def make_client():
    """Build TestClients with injected fakes; lifespan runs and is closed after the test."""
    with ExitStack() as stack:

        def factory(air=None, advisor=None, settings=None) -> TestClient:
            app = create_app(settings or make_settings(), air_client=air or FakeAirClient(), advisor=advisor)
            return stack.enter_context(TestClient(app))

        yield factory
