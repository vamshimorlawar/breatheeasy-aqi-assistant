from app.models import AgeGroup, Home, Member
from app.rules import build_baseline
from app.security import RateLimiter
from app.share import build_share_message
from tests.conftest import make_snapshot


def _baseline(aqi: int):
    return build_baseline(make_snapshot([aqi] * 24), [Member(nickname="A", age_group=AgeGroup.ADULT)], Home(), "Noida")


def test_share_message_english_severe():
    text = build_share_message(_baseline(220), "en")
    assert "Noida" in text and "AQI now: 220 (Very unhealthy)" in text
    assert "N95" in text and "BreatheEasy" in text


def test_share_message_hindi_and_good_air():
    assert "बहुत अस्वस्थ" in build_share_message(_baseline(220), "hi")
    assert "Air is OK" in build_share_message(_baseline(30), "en")


def test_rate_limiter_sliding_window():
    now = [0.0]
    limiter = RateLimiter(limit=2, window_seconds=60, clock=lambda: now[0])
    assert limiter.allow("ip") and limiter.allow("ip")
    assert not limiter.allow("ip")
    assert limiter.allow("other-ip")
    now[0] = 61
    assert limiter.allow("ip")


def test_rate_limiter_memory_is_bounded():
    now = [0.0]
    limiter = RateLimiter(limit=5, window_seconds=60, max_clients=10, clock=lambda: now[0])
    for i in range(100):
        limiter.allow(f"ip-{i}")
    assert len(limiter._hits) <= 10


def test_frame_ancestors_default_blocks_framing():
    from app.security import security_headers

    headers = security_headers()
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"


def test_frame_ancestors_allows_listed_origin_only():
    from app.security import security_headers

    headers = security_headers("https://huggingface.co")
    assert "frame-ancestors https://huggingface.co;" in headers["Content-Security-Policy"]
    assert "X-Frame-Options" not in headers


def test_frame_ancestors_env_rejects_csp_injection(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("FRAME_ANCESTORS", "https://huggingface.co https://*.hf.space")
    assert get_settings().frame_ancestors == "https://huggingface.co https://*.hf.space"
    monkeypatch.setenv("FRAME_ANCESTORS", "https://huggingface.co; script-src *")
    assert get_settings().frame_ancestors == "'none'"
    monkeypatch.setenv("FRAME_ANCESTORS", "javascript:alert(1)")
    assert get_settings().frame_ancestors == "'none'"
