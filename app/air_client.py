"""Client for the free Open-Meteo Air Quality and Geocoding APIs (no API key needed).

Responses are cached in memory per rounded location, so repeated requests from the
same neighbourhood within the TTL never hit the upstream API.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

import httpx

from .models import Place

AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
HOURLY_FIELDS = "us_aqi,pm2_5,pm10,nitrogen_dioxide,ozone"
CURRENT_FIELDS = "us_aqi,pm2_5,pm10"
FORECAST_HOURS = 24


RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_DELAY_SECONDS = 1.0


def _provider_reason(response: httpx.Response) -> str:
    try:
        reason = response.json().get("reason")
    except (ValueError, AttributeError):
        return ""
    return f": {str(reason)[:120]}" if reason else ""


class UpstreamError(RuntimeError):
    """Raised when the air-quality provider fails or returns unusable data."""


@dataclass(frozen=True)
class HourReading:
    time: datetime  # local time at the location (naive)
    aqi: int | None
    pm2_5: float | None
    pm10: float | None


@dataclass(frozen=True)
class AirSnapshot:
    timezone: str
    current: HourReading
    hourly: tuple[HourReading, ...]  # next FORECAST_HOURS hours, starting this hour


K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """Small bounded in-memory cache with per-entry expiry (LRU eviction)."""

    def __init__(self, ttl_seconds: float, max_items: int = 512, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._max = max_items
        self._clock = clock
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if self._clock() >= expires:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        self._data[key] = (self._clock() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _int(value: Any) -> int | None:
    number = _num(value)
    return round(number) if number is not None else None


def parse_air_payload(payload: dict[str, Any]) -> AirSnapshot:
    """Convert an Open-Meteo response into an AirSnapshot (pure function, easy to test)."""
    try:
        current_raw = payload["current"]
        hourly_raw = payload["hourly"]
        times = hourly_raw["time"]
        current_time = datetime.fromisoformat(current_raw["time"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UpstreamError("Unexpected air-quality response format") from exc

    def series(name: str) -> list[Any]:
        values = hourly_raw.get(name) or []
        return values if len(values) == len(times) else [None] * len(times)

    aqi, pm25, pm10 = series("us_aqi"), series("pm2_5"), series("pm10")
    this_hour = current_time.replace(minute=0, second=0, microsecond=0)

    readings: list[HourReading] = []
    for i, raw_time in enumerate(times):
        try:
            moment = datetime.fromisoformat(raw_time)
        except (TypeError, ValueError):
            continue
        if moment < this_hour:
            continue
        readings.append(HourReading(moment, _int(aqi[i]), _num(pm25[i]), _num(pm10[i])))
        if len(readings) == FORECAST_HOURS:
            break

    current = HourReading(
        current_time,
        _int(current_raw.get("us_aqi")),
        _num(current_raw.get("pm2_5")),
        _num(current_raw.get("pm10")),
    )
    return AirSnapshot(str(payload.get("timezone", "UTC")), current, tuple(readings))


class AirQualityClient:
    def __init__(self, http: httpx.AsyncClient, cache_ttl_seconds: float = 900):
        self._http = http
        self._air_cache: TTLCache[tuple[float, float], AirSnapshot] = TTLCache(cache_ttl_seconds)
        self._geo_cache: TTLCache[str, list[Place]] = TTLCache(24 * 3600)
        self._locks: dict[tuple[float, float], asyncio.Lock] = {}

    @staticmethod
    def cache_key(latitude: float, longitude: float) -> tuple[float, float]:
        # ~1 km grid: shares cache between neighbours and avoids sending exact homes upstream.
        return (round(latitude, 2), round(longitude, 2))

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET JSON, retrying once on rate limits / transient server errors."""
        for attempt in range(2):
            try:
                response = await self._http.get(url, params=params)
                if response.status_code in RETRY_STATUSES and attempt == 0:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as exc:
                # Server-side log only: status and provider's short reason, never user input.
                reason = _provider_reason(exc.response)
                raise UpstreamError(f"Provider returned HTTP {exc.response.status_code}{reason}") from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise UpstreamError(f"Provider unreachable ({type(exc).__name__})") from exc
            if not isinstance(data, dict):
                raise UpstreamError("Unexpected provider response")
            return data
        raise UpstreamError("Provider unavailable after retry")  # pragma: no cover

    async def get_snapshot(self, latitude: float, longitude: float) -> AirSnapshot:
        key = self.cache_key(latitude, longitude)
        cached = self._air_cache.get(key)
        if cached is not None:
            return cached
        # One upstream request per location even under concurrent load.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._air_cache.get(key)
            if cached is not None:
                return cached
            payload = await self._get_json(
                AIR_URL,
                {
                    "latitude": key[0],
                    "longitude": key[1],
                    "hourly": HOURLY_FIELDS,
                    "current": CURRENT_FIELDS,
                    "forecast_days": 3,
                    "timezone": "auto",
                },
            )
            snapshot = parse_air_payload(payload)
            if not snapshot.hourly:
                raise UpstreamError("No forecast available for this location")
            self._air_cache.set(key, snapshot)
        self._locks.pop(key, None)
        return snapshot

    async def geocode(self, query: str) -> list[Place]:
        normalized = " ".join(query.split()).casefold()
        cached = self._geo_cache.get(normalized)
        if cached is not None:
            return cached
        data = await self._get_json(GEOCODE_URL, {"name": normalized, "count": 5, "language": "en", "format": "json"})
        places: list[Place] = []
        for item in data.get("results") or []:
            try:
                places.append(
                    Place(
                        name=str(item["name"]),
                        admin1=item.get("admin1"),
                        country=item.get("country"),
                        latitude=float(item["latitude"]),
                        longitude=float(item["longitude"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._geo_cache.set(normalized, places)
        return places
