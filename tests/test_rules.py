from datetime import datetime

import pytest

from app.aqi import band_for
from app.models import Activity, AgeGroup, Home, Member, RiskFactor
from app.rules import (
    Sensitivity,
    activity_advice,
    best_window,
    build_baseline,
    hour_label,
    household_actions,
    mask_advice,
    member_sensitivity,
    rules_answer,
    status_for,
    trend,
    ventilation_advice,
)
from tests.conftest import make_snapshot


def member(**kwargs) -> Member:
    data = {"nickname": "Sam", "age_group": AgeGroup.ADULT}
    data.update(kwargs)
    return Member(**data)


@pytest.mark.parametrize(
    "aqi,key",
    [
        (0, "good"),
        (50, "good"),
        (51, "moderate"),
        (100, "moderate"),
        (101, "usg"),
        (150, "usg"),
        (151, "unhealthy"),
        (201, "very_unhealthy"),
        (300, "very_unhealthy"),
        (301, "hazardous"),
        (999, "hazardous"),
    ],
)
def test_band_boundaries(aqi, key):
    assert band_for(aqi).key == key


def test_band_for_missing_values():
    assert band_for(None) is None
    assert band_for(-1) is None


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, Sensitivity.NORMAL),
        ({"risk_factors": [RiskFactor.ALLERGY]}, Sensitivity.NORMAL),
        ({"age_group": AgeGroup.SENIOR}, Sensitivity.SENSITIVE),
        ({"risk_factors": [RiskFactor.PREGNANCY]}, Sensitivity.SENSITIVE),
        ({"age_group": AgeGroup.CHILD, "risk_factors": [RiskFactor.ASTHMA]}, Sensitivity.HIGH),
        ({"age_group": AgeGroup.SENIOR, "risk_factors": [RiskFactor.ASTHMA, RiskFactor.HEART_LUNG]}, Sensitivity.HIGH),
    ],
)
def test_member_sensitivity(kwargs, expected):
    assert member_sensitivity(member(**kwargs)) is expected


@pytest.mark.parametrize(
    "aqi,sensitivity,expected",
    [
        (100, Sensitivity.NORMAL, "go"),
        (101, Sensitivity.NORMAL, "caution"),
        (151, Sensitivity.NORMAL, "avoid"),
        (50, Sensitivity.SENSITIVE, "go"),
        (80, Sensitivity.SENSITIVE, "caution"),
        (101, Sensitivity.SENSITIVE, "avoid"),
        (76, Sensitivity.HIGH, "avoid"),
        (None, Sensitivity.NORMAL, "caution"),
    ],
)
def test_status_for(aqi, sensitivity, expected):
    assert status_for(aqi, sensitivity) == expected


def test_hour_label():
    assert hour_label(datetime(2026, 1, 1, 0)) == "12 AM"
    assert hour_label(datetime(2026, 1, 1, 9)) == "9 AM"
    assert hour_label(datetime(2026, 1, 1, 12)) == "12 PM"
    assert hour_label(datetime(2026, 1, 1, 17)) == "5 PM"


def test_best_window_finds_cleanest_block():
    aqis = [200, 190, 180, 90, 80, 85, 170, 160] + [150] * 16
    window = best_window(make_snapshot(aqis).hourly)
    assert window.avg_aqi == 85
    assert window.label == "9 AM – 12 PM"  # 06:00 start, cleanest hours 09-11


def test_best_window_skips_missing_data():
    aqis = [None, None, None]
    assert best_window(make_snapshot(aqis).hourly) is None


def test_trend_detection():
    assert trend(make_snapshot([200, 180, 170, 160, 150, 150, 150]).hourly) == "improving"
    assert trend(make_snapshot([100, 130, 140, 150, 150, 150, 150]).hourly) == "worsening"
    assert trend(make_snapshot([100, 101, 99, 100, 102, 98, 100]).hourly) == "steady"
    assert trend(make_snapshot([100, None]).hourly) == "unknown"


def test_flexible_activity_picks_cleanest_hour_in_its_slot():
    # 06:00 start; 03:00-05:00 next day are cleanest overall but outside the play slot (6-19).
    aqis = [120] * 24
    aqis[4] = 40  # 10 AM
    aqis[21] = 10  # 3 AM next day, should be ignored
    advice = activity_advice(Activity.OUTDOOR_PLAY, make_snapshot(aqis).hourly, Sensitivity.NORMAL, member())
    assert advice.time_label == "10 AM"
    assert advice.status == "go"


def test_fixed_activity_uses_worst_hour_of_slot():
    aqis = [60] * 24
    aqis[2] = 180  # 8 AM within school commute (7-9)
    advice = activity_advice(Activity.SCHOOL_COMMUTE, make_snapshot(aqis).hourly, Sensitivity.SENSITIVE, member())
    assert advice.status == "avoid"
    assert advice.time_label == "7 AM – 9 AM"
    assert "closed transport" in advice.advice


def test_infant_activity_and_mask_never_recommend_masks():
    baby = member(nickname="Baby", age_group=AgeGroup.INFANT)
    assert "NOT safe" in mask_advice(baby, Sensitivity.SENSITIVE, 250)
    advice = activity_advice(Activity.WALK, make_snapshot([250] * 24).hourly, Sensitivity.SENSITIVE, baby)
    assert "baby indoors" in advice.advice


def test_mask_advice_by_level():
    assert "No mask" in mask_advice(member(), Sensitivity.NORMAL, 90)
    assert "N95" in mask_advice(member(), Sensitivity.NORMAL, 160)
    assert "child-size" in mask_advice(member(age_group=AgeGroup.CHILD), Sensitivity.SENSITIVE, 120)


def test_ventilation_advice():
    assert "Open windows" in ventilation_advice(make_snapshot([150, 60, 170]).hourly)
    assert "Keep windows closed" in ventilation_advice(make_snapshot([250, 220, 230]).hourly)
    assert "unavailable" in ventilation_advice(make_snapshot([None]).hourly)


def test_household_actions_depend_on_equipment_and_severity():
    members = [member()]
    clean = household_actions(Home(), members, 40)
    assert len(clean) == 1 and "acceptable" in clean[0]

    bad = household_actions(Home(has_purifier=True, has_ac=True), members, 250)
    text = " ".join(bad)
    assert "purifier on high" in text and "recirculate" in text and "N95" in text
    assert "Postpone" in text

    no_purifier = " ".join(household_actions(Home(has_n95=True), members, 120))
    assert "clean room" in no_purifier and "Get well-fitted" not in no_purifier


def test_build_baseline_end_to_end():
    members = [
        member(
            nickname="Aarav",
            age_group=AgeGroup.CHILD,
            risk_factors=[RiskFactor.ASTHMA],
            activities=[Activity.SCHOOL_COMMUTE],
        ),
        member(nickname="Mom", activities=[Activity.WALK]),
    ]
    baseline = build_baseline(make_snapshot([180] * 24), members, Home(), "Delhi")
    assert baseline.current.aqi == 180
    assert baseline.current.band.key == "unhealthy"
    assert baseline.peak_aqi == 180
    assert len(baseline.hourly) == 24
    aarav, mom = baseline.members
    assert aarav.sensitivity == "high" and aarav.overall_status == "avoid"
    assert any("inhaler" in w for w in aarav.warnings)
    assert mom.overall_status == "avoid"
    assert "not medical advice" in baseline.disclaimer

    answer = rules_answer(baseline)
    assert "Delhi" in answer and "Aarav" in answer and "180" in answer


def test_member_without_activities_uses_current_air():
    baseline = build_baseline(make_snapshot([40] * 24), [member()], Home(), "Pune")
    assert baseline.members[0].overall_status == "go"
    assert baseline.members[0].activities == []
