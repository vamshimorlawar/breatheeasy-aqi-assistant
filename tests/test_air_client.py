import asyncio

import httpx
import pytest

from app.air_client import AirQualityClient, TTLCache, UpstreamError, parse_air_payload
from tests.conftest import make_payload


def test_parse_payload_starts_at_current_hour_and_limits_to_24():
    payload = make_payload(list(range(100, 148)))  # 48 hours
    payload["current"]["time"] = "2026-11-17T08:30"  # two hours after the first reading
    snapshot = parse_air_payload(payload)
    assert snapshot.timezone == "Asia/Kolkata"
    assert len(snapshot.hourly) == 24
    assert snapshot.hourly[0].time.hour == 8
    assert snapshot.hourly[0].aqi == 102
    assert snapshot.current.pm2_5 == 80.5


def test_parse_payload_handles_missing_and_bad_values():
    payload = make_payload([100, None, 120])
    payload["hourly"]["us_aqi"][2] = "bad"
    payload["hourly"]["pm10"] = [1, 2]  # wrong length -> ignored
    snapshot = parse_air_payload(payload)
    assert [h.aqi for h in snapshot.hourly] == [100, None, None]
    assert all(h.pm10 is None for h in snapshot.hourly)


@pytest.mark.parametrize(
    "payload",
    [{}, {"current": {}, "hourly": {}}, {"current": {"time": "nope"}, "hourly": {"time": []}}],
)
def test_parse_payload_rejects_malformed(payload):
    with pytest.raises(UpstreamError):
        parse_air_payload(payload)


def test_ttl_cache_expiry_and_eviction():
    now = [0.0]
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=10, max_items=2, clock=lambda: now[0])
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)  # evicts least recently used ("b")
    assert cache.get("b") is None
    now[0] = 11
    assert cache.get("a") is None


def run(coro):
    return asyncio.run(coro)


def test_snapshot_is_cached_and_coordinates_are_rounded():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=make_payload([150] * 24))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = AirQualityClient(http, cache_ttl_seconds=60)
            first = await client.get_snapshot(28.65195, 77.23149)
            second = await client.get_snapshot(28.6531, 77.2288)  # same ~1 km cell
            results = await asyncio.gather(*(client.get_snapshot(28.65, 77.23) for _ in range(5)))
            return first, second, results

    first, second, results = run(scenario())
    assert first is second and all(r is first for r in results)
    assert len(seen) == 1
    assert seen[0].url.params["latitude"] == "28.65"
    assert seen[0].url.params["longitude"] == "77.23"


def test_upstream_http_error_raises():
    async def scenario():
        transport = httpx.MockTransport(lambda r: httpx.Response(503))
        async with httpx.AsyncClient(transport=transport) as http:
            await AirQualityClient(http).get_snapshot(1, 1)

    with pytest.raises(UpstreamError):
        run(scenario())


def test_geocode_parses_and_skips_bad_rows():
    body = {
        "results": [
            {"name": "Delhi", "admin1": "NCT", "country": "India", "latitude": 28.6, "longitude": 77.2},
            {"name": "Broken"},
        ]
    }
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = AirQualityClient(http)
            places = await client.geocode("  Delhi ")
            again = await client.geocode("delhi")
            return places, again

    places, again = run(scenario())
    assert [p.name for p in places] == ["Delhi"]
    assert again == places and len(calls) == 1
