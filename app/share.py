"""Community alert text for WhatsApp or residents' groups (deterministic, English or Hindi)."""

from __future__ import annotations

from .aqi import band_for
from .models import Baseline, Language


def _tips(severity: int, language: Language) -> list[str]:
    if language == "hi":
        if severity >= 3:
            return [
                "बाहर N95 मास्क पहनें",
                "खिड़कियाँ बंद रखें, एयर प्यूरीफायर चलाएँ",
                "बच्चों, बुज़ुर्गों और साँस के मरीज़ों को घर के अंदर रखें",
                "कचरा या पत्ते न जलाएँ",
            ]
        if severity == 2:
            return ["संवेदनशील लोग बाहर कम समय बिताएँ", "बाहर N95 मास्क पहनें", "कचरा या पत्ते न जलाएँ"]
        return ["हवा ठीक है, बाहर समय बिताएँ", "घर में ताज़ी हवा आने दें"]
    if severity >= 3:
        return [
            "Wear N95 masks outdoors",
            "Keep windows shut and run purifiers",
            "Keep kids, elders and anyone with asthma indoors",
            "Please don't burn garbage or leaves",
        ]
    if severity == 2:
        return [
            "Sensitive people should limit outdoor time",
            "Wear N95 masks outdoors",
            "Please don't burn garbage or leaves",
        ]
    return ["Air is OK. Enjoy time outdoors", "Open windows to let fresh air in"]


def build_share_message(baseline: Baseline, language: Language = "en") -> str:
    aqi = baseline.current.aqi
    band = band_for(aqi)
    severity = band.severity if band else 0
    place = baseline.location_label
    window = baseline.best_window

    if language == "hi":
        label = band.label_hi if band else "अज्ञात"
        lines = [f"🌫️ वायु गुणवत्ता अपडेट: {place}", f"अभी AQI {aqi} ({label})"]
        if window:
            lines.append(f"बाहर जाने का सबसे साफ़ समय: {window.label} (AQI ~{window.avg_aqi})")
        lines.append("सुझाव:")
    else:
        label = band.label if band else "Unknown"
        lines = [f"🌫️ Air quality update: {place}", f"AQI now: {aqi} ({label})"]
        if window:
            lines.append(f"Cleanest time to step out: {window.label} (AQI ~{window.avg_aqi})")
        lines.append("Tips:")
    lines.extend(f"• {tip}" for tip in _tips(severity, language))
    lines.append("— shared via BreatheEasy")
    return "\n".join(lines)
