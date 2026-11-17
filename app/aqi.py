"""US EPA AQI categories (the scale returned by the Open-Meteo Air Quality API)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AqiBand:
    key: str
    label: str
    label_hi: str
    severity: int  # 0 (good) .. 5 (hazardous)
    upper: int  # inclusive upper bound of the band
    color: str


BANDS: tuple[AqiBand, ...] = (
    AqiBand("good", "Good", "अच्छा", 0, 50, "#00a651"),
    AqiBand("moderate", "Moderate", "मध्यम", 1, 100, "#d4a600"),
    AqiBand(
        "usg",
        "Unhealthy for sensitive groups",
        "संवेदनशील लोगों के लिए अस्वस्थ",
        2,
        150,
        "#f26f21",
    ),
    AqiBand("unhealthy", "Unhealthy", "अस्वस्थ", 3, 200, "#d6262c"),
    AqiBand("very_unhealthy", "Very unhealthy", "बहुत अस्वस्थ", 4, 300, "#8f3f97"),
    AqiBand("hazardous", "Hazardous", "खतरनाक", 5, 10_000, "#7e0023"),
)


def band_for(aqi: int | None) -> AqiBand | None:
    """Return the AQI band for a value, or None when the value is missing/invalid."""
    if aqi is None or aqi < 0:
        return None
    for band in BANDS:
        if aqi <= band.upper:
            return band
    return BANDS[-1]
