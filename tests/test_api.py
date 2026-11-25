import pytest

from tests.conftest import FakeAdvisor, FakeAirClient, make_payload, make_settings


def test_health_reports_ai_status(make_client):
    assert make_client().get("/api/health").json()["ai_enabled"] is False
    assert make_client(advisor=FakeAdvisor()).get("/api/health").json()["ai_enabled"] is True


def test_index_and_static_assets_served_with_security_headers(make_client):
    client = make_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "BreatheEasy" in response.text
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/static/js/main.js").status_code == 200
    assert client.head("/").status_code == 200


def test_plan_with_ai(make_client, plan_body):
    client = make_client(advisor=FakeAdvisor())
    response = client.post("/api/plan", json=plan_body)
    assert response.status_code == 200
    data = response.json()
    assert data["ai_status"] == "ok"
    assert data["ai"]["headline"].startswith("Unhealthy")
    assert [m["nickname"] for m in data["baseline"]["members"]] == ["Aarav", "Mom"]
    assert "Delhi, India" in data["share_message"]
    assert response.headers["cache-control"] == "no-store"


def test_plan_falls_back_when_ai_fails(make_client, plan_body):
    data = make_client(advisor=FakeAdvisor(fail=True)).post("/api/plan", json=plan_body).json()
    assert data["ai"] is None and data["ai_status"] == "unavailable"
    assert data["baseline"]["members"][0]["overall_status"] == "avoid"


def test_plan_without_ai_key_is_disabled(make_client, plan_body):
    data = make_client().post("/api/plan", json=plan_body).json()
    assert data["ai_status"] == "disabled"


def test_plan_hindi_share_message(make_client, plan_body):
    plan_body["language"] = "hi"
    data = make_client().post("/api/plan", json=plan_body).json()
    assert "वायु गुणवत्ता" in data["share_message"]


def test_upstream_failure_returns_502(make_client, plan_body):
    response = make_client(air=FakeAirClient(fail=True)).post("/api/plan", json=plan_body)
    assert response.status_code == 502
    assert "unavailable" in response.json()["detail"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["location"].update(latitude=95),
        lambda b: b["location"].update(label="<img src=x onerror=alert(1)>"),
        lambda b: b["members"][0].update(nickname="<script>"),
        lambda b: b["members"][0].update(age_group="alien"),
        lambda b: b["members"][0].update(risk_factors=["smoker"]),
        lambda b: b["members"].append(dict(b["members"][0])),  # duplicate nickname
        lambda b: b.update(members=[]),
        lambda b: b.update(members=[{"nickname": f"P{i}", "age_group": "adult"} for i in range(9)]),
        lambda b: b.update(language="fr"),
        lambda b: b.update(extra_field="nope"),
    ],
)
def test_plan_rejects_invalid_input_without_reflecting_it(make_client, plan_body, mutate):
    mutate(plan_body)
    response = make_client().post("/api/plan", json=plan_body)
    assert response.status_code == 422
    assert "<script>" not in response.text and "onerror" not in response.text


def test_ask_uses_ai_and_falls_back(make_client, plan_body):
    body = {**plan_body, "question": "Can Aarav play cricket at 5 PM?"}
    advisor = FakeAdvisor()
    ok = make_client(advisor=advisor).post("/api/ask", json=body).json()
    assert ok == {"answer": "AI answer in en", "source": "gemini"}
    assert advisor.questions == ["Can Aarav play cricket at 5 PM?"]

    fallback = make_client(advisor=FakeAdvisor(fail=True)).post("/api/ask", json=body).json()
    assert fallback["source"] == "rules" and "Aarav" in fallback["answer"]


@pytest.mark.parametrize("question", ["", "hi", "x" * 301])
def test_ask_validates_question_length(make_client, plan_body, question):
    response = make_client().post("/api/ask", json={**plan_body, "question": question})
    assert response.status_code == 422


def test_geocode(make_client):
    client = make_client()
    assert client.get("/api/geocode?q=Delhi").json()[0]["name"] == "Delhi"
    assert client.get("/api/geocode?q=D").status_code == 422
    assert make_client(air=FakeAirClient(fail=True)).get("/api/geocode?q=Delhi").status_code == 502


def test_rate_limit_returns_429(make_client):
    client = make_client(settings=make_settings(rate_limit_per_minute=3))
    codes = [client.get("/api/geocode?q=Delhi").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert client.get("/api/health").status_code == 200  # health checks are never limited


# ---- browser-fetched forecast fallback (server's provider quota exhausted)


def test_plan_uses_browser_forecast_when_server_provider_fails(make_client, plan_body):
    client = make_client(air=FakeAirClient(fail=True))
    assert client.post("/api/plan", json=plan_body).status_code == 502  # client then retries with `air`
    response = client.post("/api/plan", json={**plan_body, "air": make_payload([60] * 30)})
    assert response.status_code == 200
    assert response.json()["baseline"]["current"]["aqi"] == 60


def test_ask_accepts_browser_forecast(make_client, plan_body):
    client = make_client(air=FakeAirClient(fail=True))
    body = {**plan_body, "air": make_payload([60] * 30), "question": "Can we walk now?"}
    assert client.post("/api/ask", json=body).json()["source"] == "rules"


@pytest.mark.parametrize(
    "tamper",
    [
        lambda air: air["current"].update(us_aqi=-5),
        lambda air: air["current"].update(us_aqi=99999),
        lambda air: air["current"].update(time="<script>"),
        lambda air: air["hourly"].update(time=air["hourly"]["time"] * 5),  # > 96 hours
        lambda air: air["hourly"].update(extra=[1]),
        lambda air: air.update(timezone="Asia/Kolkata; drop"),
        lambda air: air["hourly"]["us_aqi"].__setitem__(0, "100"),
    ],
)
def test_browser_forecast_is_strictly_validated(make_client, plan_body, tamper):
    air = make_payload([60] * 30)
    tamper(air)
    response = make_client().post("/api/plan", json={**plan_body, "air": air})
    assert response.status_code == 422
    assert "<script>" not in response.text


def test_stale_browser_forecast_rejected(make_client, plan_body):
    air = make_payload([60] * 3)
    air["current"]["time"] = "2026-11-18T12:00"  # all hourly values are in the past
    response = make_client().post("/api/plan", json={**plan_body, "air": air})
    assert response.status_code == 422


def test_csp_allows_only_open_meteo_for_browser_fallback(make_client):
    csp = make_client().get("/").headers["content-security-policy"]
    assert "connect-src 'self' https://air-quality-api.open-meteo.com;" in csp
