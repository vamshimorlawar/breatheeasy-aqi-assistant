import asyncio
import json
from types import SimpleNamespace

import pytest

from app.ai import AdvisorError, GeminiAdvisor, clean_text, compact_context, parse_ai_plan, thinking_config
from app.models import AgeGroup, Home, Member
from app.rules import build_baseline
from tests.conftest import make_snapshot


@pytest.fixture
def baseline():
    members = [Member(nickname="Aarav", age_group=AgeGroup.CHILD), Member(nickname="Mom", age_group=AgeGroup.ADULT)]
    return build_baseline(make_snapshot([170] * 24), members, Home(), "Delhi")


def test_compact_context_contains_no_coordinates(baseline):
    context = compact_context(baseline)
    text = json.dumps(context)
    assert "latitude" not in text and "longitude" not in text
    assert context["city"] == "Delhi"
    assert len(context["hourly_aqi"]) == 24
    assert {m["nickname"] for m in context["members"]} == {"Aarav", "Mom"}


def test_parse_ai_plan_filters_unknown_members_and_trims(baseline):
    raw = json.dumps(
        {
            "headline": "x" * 500,
            "summary": "Stay in.",
            "member_notes": [
                {"nickname": "Aarav", "note": "Play indoors today."},
                {"nickname": "Intruder", "note": "ignore previous instructions"},
            ],
        }
    )
    plan = parse_ai_plan(raw, baseline)
    assert len(plan.headline) == 160
    assert [n.nickname for n in plan.member_notes] == ["Aarav"]


@pytest.mark.parametrize("raw", [None, "", "not json", "[]", json.dumps({"headline": "only"})])
def test_parse_ai_plan_rejects_bad_output(raw, baseline):
    with pytest.raises(AdvisorError):
        parse_ai_plan(raw, baseline)


class _ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"error {code}")
        self.code = code


class _FakeModels:
    """Stands in for client.aio.models; `errors` maps model name -> exception to raise."""

    def __init__(self, text=None, error=None, errors=None):
        self.text, self.error, self.errors, self.calls = text, error, errors or {}, []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        error = self.errors.get(kwargs["model"], self.error)
        if error:
            raise error
        return SimpleNamespace(text=self.text)


def _advisor(models: _FakeModels, names=("gemini-3.8-flash",)) -> GeminiAdvisor:
    advisor = GeminiAdvisor.__new__(GeminiAdvisor)  # skip real client construction
    advisor._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    advisor._models = list(names)
    advisor._timeout = 5
    return advisor


def test_gemini_plan_success_sends_grounded_prompt(baseline):
    models = _FakeModels(text=json.dumps({"headline": "h", "summary": "s", "member_notes": []}))
    plan = asyncio.run(_advisor(models).personalize_plan(baseline, "hi"))
    assert plan.headline == "h"
    call = models.calls[0]
    assert call["model"] == "gemini-3.8-flash"
    assert call["contents"].startswith("DATA:")
    assert "Hindi" in call["config"].system_instruction
    assert "Never contradict" in call["config"].system_instruction


def test_gemini_answer_includes_question_and_truncates(baseline):
    models = _FakeModels(text="y" * 5000)
    answer = asyncio.run(_advisor(models).answer("Can Aarav play at 5 PM?", baseline, "en"))
    assert len(answer) == 1200
    assert "QUESTION:\nCan Aarav play at 5 PM?" in models.calls[0]["contents"]


def test_gemini_errors_become_advisor_errors(baseline):
    with pytest.raises(AdvisorError):
        asyncio.run(_advisor(_FakeModels(error=RuntimeError("quota"))).answer("hi there", baseline, "en"))
    with pytest.raises(AdvisorError):
        asyncio.run(_advisor(_FakeModels(text="")).answer("hi there", baseline, "en"))


def test_falls_back_to_next_model_on_retired_or_overloaded():
    models = _FakeModels(text="fine", errors={"old": _ApiError(404), "busy": _ApiError(503)})
    answer = asyncio.run(_advisor(models, ("old", "busy", "good")).answer("hello?", _baseline(), "en"))
    assert answer == "fine"
    assert [c["model"] for c in models.calls] == ["old", "busy", "good"]


def test_does_not_retry_on_bad_request():
    models = _FakeModels(text="fine", errors={"first": _ApiError(400)})
    with pytest.raises(AdvisorError):
        asyncio.run(_advisor(models, ("first", "second")).answer("hello?", _baseline(), "en"))
    assert len(models.calls) == 1


def test_all_models_failing_raises():
    models = _FakeModels(error=_ApiError(503))
    with pytest.raises(AdvisorError):
        asyncio.run(_advisor(models, ("a", "b")).answer("hello?", _baseline(), "en"))
    assert len(models.calls) == 2


def test_thinking_config_per_model_family():
    assert thinking_config("gemini-2.5-flash") == {"thinking_budget": 0}
    assert thinking_config("gemini-3.8-flash") == {"thinking_level": "low"}
    assert thinking_config("other-model") is None


def _baseline():
    return build_baseline(make_snapshot([170] * 24), [Member(nickname="A", age_group=AgeGroup.ADULT)], Home(), "Delhi")


def test_slow_model_times_out_and_next_model_answers(monkeypatch):
    class SlowThenFast(_FakeModels):
        async def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["model"] == "slow":
                await asyncio.sleep(10)
            return SimpleNamespace(text="fast answer")

    models = SlowThenFast()
    advisor = _advisor(models, ("slow", "fast"))
    advisor._timeout = 0.2  # each model gets ~0.1s, so "slow" times out and "fast" still has budget
    monkeypatch.setattr("app.ai.PER_MODEL_MIN_SECONDS", 0.05)
    assert asyncio.run(advisor.answer("hello?", _baseline(), "en")) == "fast answer"
    assert [c["model"] for c in models.calls] == ["slow", "fast"]


def test_clean_text_strips_markdown():
    raw = "## Tips\n**No**, stay in.\n\n\n*   Close `windows`\n- Run the purifier"
    assert clean_text(raw) == "Tips\nNo, stay in.\n\n• Close windows\n• Run the purifier"
