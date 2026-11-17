"""Deterministic health-guidance engine.

This module is the *source of truth* for safety decisions (go / caution / avoid, masks,
ventilation timing). It is based on US EPA AQI guidance for sensitive groups. The GenAI
layer only explains and personalises these results; it is never allowed to override them,
and the app keeps working with this engine alone if the AI is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from statistics import mean

from .air_client import AirSnapshot, HourReading
from .aqi import AqiBand, band_for
from .models import (
    Activity,
    ActivityAdvice,
    AgeGroup,
    BandInfo,
    Baseline,
    CurrentAir,
    Home,
    HourPoint,
    Member,
    MemberPlan,
    RiskFactor,
    Status,
    TimeWindow,
)

DISCLAIMER = (
    "BreatheEasy gives general guidance based on public air-quality forecasts. "
    "It is not medical advice. If anyone has trouble breathing, chest pain or dizziness, "
    "contact a doctor or emergency services."
)


class Sensitivity(IntEnum):
    NORMAL = 0
    SENSITIVE = 1
    HIGH = 2


SENSITIVE_AGES = {AgeGroup.INFANT, AgeGroup.CHILD, AgeGroup.TEEN, AgeGroup.SENIOR}
MEDICAL_RISKS = {RiskFactor.ASTHMA, RiskFactor.HEART_LUNG, RiskFactor.PREGNANCY}

# Highest AQI at which outdoor activity is fine ("go") / acceptable with care ("caution").
GO_LIMIT = {Sensitivity.NORMAL: 100, Sensitivity.SENSITIVE: 50, Sensitivity.HIGH: 50}
CAUTION_LIMIT = {Sensitivity.NORMAL: 150, Sensitivity.SENSITIVE: 100, Sensitivity.HIGH: 75}

VENTILATE_MAX_AQI = 100
WINDOW_HOURS = 3


@dataclass(frozen=True)
class ActivitySpec:
    label: str
    phrase: str  # used inside sentences, e.g. "a walk"
    flexible: bool  # can the family choose the time?
    start_hour: int
    end_hour: int  # exclusive


ACTIVITIES: dict[Activity, ActivitySpec] = {
    Activity.SCHOOL_COMMUTE: ActivitySpec("School commute", "the school commute", False, 7, 9),
    Activity.OFFICE_COMMUTE: ActivitySpec("Office commute", "the office commute", False, 8, 10),
    Activity.OUTDOOR_WORK: ActivitySpec("Outdoor work", "outdoor work", False, 9, 18),
    Activity.OUTDOOR_PLAY: ActivitySpec("Outdoor play", "outdoor play", True, 6, 19),
    Activity.EXERCISE: ActivitySpec("Outdoor exercise", "outdoor exercise", True, 5, 21),
    Activity.WALK: ActivitySpec("Walk", "a walk", True, 6, 21),
}


# ------------------------------------------------------------------ helpers


def hour_label(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour} {suffix}"


def range_label(first: datetime, last: datetime) -> str:
    """Label for a block of hourly readings, e.g. '7 AM – 9 AM' for 7:00 and 8:00."""
    return f"{hour_label(first)} – {hour_label(last + timedelta(hours=1))}"


def _band_info(band: AqiBand | None) -> BandInfo | None:
    if band is None:
        return None
    return BandInfo(key=band.key, label=band.label, severity=band.severity, color=band.color)


def member_sensitivity(member: Member) -> Sensitivity:
    score = 0
    if member.age_group in SENSITIVE_AGES:
        score += 1
    if MEDICAL_RISKS.intersection(member.risk_factors):
        score += 1
    return Sensitivity(min(score, Sensitivity.HIGH))


def status_for(aqi: int | None, sensitivity: Sensitivity) -> Status:
    if aqi is None:
        return "caution"
    if aqi <= GO_LIMIT[sensitivity]:
        return "go"
    if aqi <= CAUTION_LIMIT[sensitivity]:
        return "caution"
    return "avoid"


def _valid(hours: tuple[HourReading, ...] | list[HourReading]) -> list[HourReading]:
    return [h for h in hours if h.aqi is not None]


def best_window(hours: tuple[HourReading, ...], size: int = WINDOW_HOURS) -> TimeWindow | None:
    """Cleanest block of `size` consecutive hours (earliest wins ties)."""
    best: tuple[float, int] | None = None
    for i in range(0, len(hours) - size + 1):
        block = hours[i : i + size]
        if any(h.aqi is None for h in block):
            continue
        avg = mean(h.aqi for h in block)  # type: ignore[misc]
        if best is None or avg < best[0]:
            best = (avg, i)
    if best is None:
        return None
    avg, i = best
    start, end = hours[i].time, hours[i + size - 1].time
    return TimeWindow(
        start=start.isoformat(),
        end=end.isoformat(),
        label=range_label(start, end),
        avg_aqi=round(avg),
    )


def trend(hours: tuple[HourReading, ...]) -> str:
    valid = _valid(hours[:7])
    if len(valid) < 4:
        return "unknown"
    now, later = valid[0].aqi, mean(h.aqi for h in valid[1:])  # type: ignore[misc]
    if later <= now - 15:  # type: ignore[operator]
        return "improving"
    if later >= now + 15:  # type: ignore[operator]
        return "worsening"
    return "steady"


# ------------------------------------------------------------ activities


def _slot_hours(hours: tuple[HourReading, ...], spec: ActivitySpec) -> list[HourReading]:
    """Next contiguous occurrence of the activity's time-of-day slot."""
    slot: list[HourReading] = []
    for h in hours:
        if spec.start_hour <= h.time.hour < spec.end_hour:
            slot.append(h)
        elif slot:
            break
    return slot


def activity_advice(
    activity: Activity, hours: tuple[HourReading, ...], sensitivity: Sensitivity, member: Member
) -> ActivityAdvice:
    spec = ACTIVITIES[activity]
    slot = _valid(_slot_hours(hours, spec))
    if not slot:
        return ActivityAdvice(
            activity=activity,
            label=spec.label,
            status="caution",
            time_label=None,
            advice=f"No forecast yet for the usual time of {spec.phrase}. Check again later.",
        )

    if spec.flexible:
        chosen = min(slot, key=lambda h: h.aqi)  # type: ignore[arg-type, return-value]
        aqi, time_label = chosen.aqi, hour_label(chosen.time)
    else:
        aqi = max(h.aqi for h in slot)  # type: ignore[type-var]
        time_label = range_label(slot[0].time, slot[-1].time)

    status = status_for(aqi, sensitivity)
    name, phrase = spec.label, spec.phrase
    if status == "go":
        advice = f"Good time for {phrase}: around {time_label} (AQI ~{aqi})."
    elif status == "caution":
        if spec.flexible:
            advice = (
                f"Best time for {phrase} is around {time_label} (AQI ~{aqi}). "
                "Keep it short and light, and wear a well-fitted N95 if outdoors."
            )
        else:
            advice = f"Air is poor during {phrase} ({time_label}, AQI up to {aqi}). Wear a well-fitted N95."
    else:
        if activity is Activity.SCHOOL_COMMUTE:
            advice = (
                f"Air is very poor at school time (AQI up to {aqi}). Use closed transport, "
                "wear an N95 (if over 2 years old) and ask the school about indoor-only activities."
            )
        elif activity is Activity.OFFICE_COMMUTE:
            advice = (
                f"Work from home if you can (AQI up to {aqi}). Otherwise use closed transport "
                "with AC on recirculate and wear an N95."
            )
        elif activity is Activity.OUTDOOR_WORK:
            advice = (
                f"Outdoor work is risky today (AQI up to {aqi}). Wear an N95 at all times, "
                "take indoor breaks every hour and avoid heavy exertion."
            )
        else:
            advice = (
                f"Not a good day for {phrase} outside. Even the cleanest hour ({time_label}) "
                f"is AQI ~{aqi}. Do it indoors instead."
            )

    if member.age_group is AgeGroup.INFANT and status != "go":
        advice = f"Keep the baby indoors during {phrase} if possible: the air is unhealthy for infants (AQI ~{aqi})."
    return ActivityAdvice(activity=activity, label=name, status=status, time_label=time_label, advice=advice)


# ----------------------------------------------------------------- member


def mask_advice(member: Member, sensitivity: Sensitivity, peak_aqi: int | None) -> str:
    if member.age_group is AgeGroup.INFANT:
        return (
            "Masks are NOT safe for children under 2. Keep them in the cleanest room, "
            "away from smoke, and limit time outside."
        )
    if peak_aqi is None or peak_aqi <= GO_LIMIT[sensitivity]:
        return "No mask needed for normal outdoor time today."
    size = "child-size " if member.age_group is AgeGroup.CHILD else ""
    return f"Wear a well-fitted {size}N95/KN95 mask outdoors. Cloth and surgical masks do little against PM2.5."


def member_warnings(member: Member, sensitivity: Sensitivity, peak_aqi: int | None) -> list[str]:
    warnings: list[str] = []
    if peak_aqi is None:
        return warnings
    risks = set(member.risk_factors)
    if RiskFactor.ASTHMA in risks and peak_aqi > 100:
        warnings.append("Keep the reliever inhaler handy and follow the asthma action plan.")
    if RiskFactor.HEART_LUNG in risks and peak_aqi > 100:
        warnings.append("Take prescribed medicines on time; watch for chest pain, palpitations or breathlessness.")
    if RiskFactor.PREGNANCY in risks and peak_aqi > 100:
        warnings.append("Limit time outdoors and stay in the purified room as much as possible.")
    if RiskFactor.ALLERGY in risks and peak_aqi > 100:
        warnings.append("Rinse eyes and nose after coming indoors; itchy eyes and sneezing may increase.")
    if RiskFactor.OUTDOOR_WORKER in risks and peak_aqi > 150:
        warnings.append("Ask your employer for N95 masks and shaded indoor rest breaks.")
    if sensitivity is Sensitivity.HIGH and peak_aqi > 150:
        warnings.append("Seek medical help quickly if breathing becomes difficult or lips turn bluish.")
    return warnings


def member_plan(
    member: Member, hours: tuple[HourReading, ...], peak_aqi: int | None, current_aqi: int | None
) -> MemberPlan:
    sensitivity = member_sensitivity(member)
    activities = [activity_advice(a, hours, sensitivity, member) for a in member.activities]
    statuses = [a.status for a in activities] or [status_for(current_aqi, sensitivity)]
    overall: Status = "avoid" if "avoid" in statuses else "caution" if "caution" in statuses else "go"
    return MemberPlan(
        nickname=member.nickname,
        sensitivity=sensitivity.name.lower(),  # type: ignore[arg-type]
        overall_status=overall,
        mask=mask_advice(member, sensitivity, peak_aqi),
        activities=activities,
        warnings=member_warnings(member, sensitivity, peak_aqi),
    )


# --------------------------------------------------------------- household


def ventilation_advice(hours: tuple[HourReading, ...]) -> str:
    valid = _valid(hours)
    if not valid:
        return "Forecast unavailable. Keep windows closed if the air looks or smells smoky."
    cleanest = min(valid, key=lambda h: h.aqi)  # type: ignore[arg-type, return-value]
    when = hour_label(cleanest.time)
    if cleanest.aqi <= VENTILATE_MAX_AQI:  # type: ignore[operator]
        return (
            f"Open windows for 15–20 minutes around {when} (AQI ~{cleanest.aqi}) to let fresh air in. "
            "Keep them closed at other times."
        )
    return (
        f"Outdoor air stays unhealthy all day. Keep windows closed. If you must ventilate "
        f"(cooking, stuffiness), do it briefly around {when}, the cleanest hour (AQI ~{cleanest.aqi})."
    )


def household_actions(home: Home, members: list[Member], peak_aqi: int | None) -> list[str]:
    if peak_aqi is None:
        return ["Forecast unavailable. Check again in a little while."]
    actions: list[str] = []
    if peak_aqi > 100:
        if home.has_purifier:
            actions.append(
                "Run the air purifier on high in the room where the family spends most time, "
                "with doors and windows closed."
            )
        else:
            actions.append(
                "Choose one 'clean room': keep its doors and windows shut and seal gaps with towels. "
                "A HEPA purifier sized for that room helps most."
            )
        if home.has_ac:
            actions.append("Set the AC to recirculate mode. Do not use fresh-air or outside-air mode.")
        needs_masks = any(m.age_group is not AgeGroup.INFANT for m in members)
        if needs_masks and not home.has_n95:
            actions.append("Get well-fitted N95/KN95 masks for everyone over 2 years old.")
    if peak_aqi > 150:
        actions.append(
            "Avoid burning incense, candles, mosquito coils or wood indoors, and use the kitchen exhaust when frying."
        )
        actions.append("Wet-mop floors instead of sweeping to avoid stirring up settled dust.")
    if peak_aqi > 200:
        actions.append("Postpone non-essential trips. Check on elderly neighbours who live alone.")
    if not actions:
        actions.append("Air is acceptable today. Enjoy outdoor time and air out your home.")
    return actions


# ------------------------------------------------------------------ public


def build_baseline(snapshot: AirSnapshot, members: list[Member], home: Home, location_label: str) -> Baseline:
    hours = snapshot.hourly
    valid = _valid(hours)
    peak_aqi = max((h.aqi for h in valid[:12]), default=None)  # type: ignore[type-var]
    current_aqi = snapshot.current.aqi if snapshot.current.aqi is not None else (valid[0].aqi if valid else None)

    return Baseline(
        location_label=location_label,
        timezone=snapshot.timezone,
        current=CurrentAir(
            time=snapshot.current.time.isoformat(),
            aqi=current_aqi,
            pm2_5=snapshot.current.pm2_5,
            pm10=snapshot.current.pm10,
            band=_band_info(band_for(current_aqi)),
        ),
        peak_aqi=peak_aqi,
        trend=trend(hours),  # type: ignore[arg-type]
        hourly=[
            HourPoint(
                time=h.time.isoformat(),
                label=hour_label(h.time),
                aqi=h.aqi,
                pm2_5=h.pm2_5,
                band=_band_info(band_for(h.aqi)),
            )
            for h in hours
        ],
        best_window=best_window(hours),
        ventilation=ventilation_advice(hours),
        household_actions=household_actions(home, members, peak_aqi),
        members=[member_plan(m, hours, peak_aqi, current_aqi) for m in members],
        disclaimer=DISCLAIMER,
    )


def rules_answer(baseline: Baseline) -> str:
    """Fallback answer for the Q&A assistant when the AI is unavailable."""
    current = baseline.current
    band = current.band.label if current.band else "unknown"
    parts = [f"Right now the AQI in {baseline.location_label} is {current.aqi} ({band})."]
    if baseline.best_window:
        parts.append(
            f"The cleanest time in the next 24 hours is {baseline.best_window.label} "
            f"(average AQI ~{baseline.best_window.avg_aqi})."
        )
    for plan in baseline.members:
        parts.append(f"{plan.nickname}: {plan.overall_status.upper()}. {plan.mask}")
    parts.append("(The AI assistant is unavailable, so this is the standard guidance.)")
    return " ".join(parts)
