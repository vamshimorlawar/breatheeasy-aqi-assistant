"""Request/response schemas. All user input is strictly validated here."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Letters (any script), digits, spaces and a few punctuation marks only.
NICKNAME_PATTERN = r"^[\w][\w .'\-]{0,29}$"
PLACE_LABEL_PATTERN = r"^[\w][\w ,.'()\-]{0,79}$"

Language = Literal["en", "hi"]
Status = Literal["go", "caution", "avoid"]


class AgeGroup(str, Enum):
    INFANT = "infant"  # under 2
    CHILD = "child"  # 2-12
    TEEN = "teen"  # 13-17
    ADULT = "adult"
    SENIOR = "senior"  # 65+


class RiskFactor(str, Enum):
    ASTHMA = "asthma"
    HEART_LUNG = "heart_lung"
    PREGNANCY = "pregnancy"
    ALLERGY = "allergy"
    OUTDOOR_WORKER = "outdoor_worker"


class Activity(str, Enum):
    SCHOOL_COMMUTE = "school_commute"
    OFFICE_COMMUTE = "office_commute"
    OUTDOOR_PLAY = "outdoor_play"
    EXERCISE = "exercise"
    WALK = "walk"
    OUTDOOR_WORK = "outdoor_work"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------- requests


class Member(StrictModel):
    nickname: str = Field(min_length=1, max_length=30, pattern=NICKNAME_PATTERN)
    age_group: AgeGroup
    risk_factors: list[RiskFactor] = Field(default_factory=list, max_length=5)
    activities: list[Activity] = Field(default_factory=list, max_length=6)

    @field_validator("risk_factors", "activities")
    @classmethod
    def _dedupe(cls, values: list) -> list:
        return list(dict.fromkeys(values))


class Home(StrictModel):
    has_purifier: bool = False
    has_n95: bool = False
    has_ac: bool = False


class Location(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str | None = Field(default=None, max_length=80, pattern=PLACE_LABEL_PATTERN)


class PlanRequest(StrictModel):
    location: Location
    members: list[Member] = Field(min_length=1, max_length=8)
    home: Home = Field(default_factory=Home)
    language: Language = "en"

    @model_validator(mode="after")
    def _unique_nicknames(self) -> PlanRequest:
        names = [m.nickname.casefold() for m in self.members]
        if len(names) != len(set(names)):
            raise ValueError("Each household member needs a unique nickname.")
        return self


class AskRequest(PlanRequest):
    question: str = Field(min_length=3, max_length=300)


# --------------------------------------------------------------- responses


class Place(BaseModel):
    name: str
    admin1: str | None = None
    country: str | None = None
    latitude: float
    longitude: float


class BandInfo(BaseModel):
    key: str
    label: str
    severity: int
    color: str


class HourPoint(BaseModel):
    time: str
    label: str
    aqi: int | None
    pm2_5: float | None
    band: BandInfo | None


class CurrentAir(BaseModel):
    time: str
    aqi: int | None
    pm2_5: float | None
    pm10: float | None
    band: BandInfo | None


class TimeWindow(BaseModel):
    start: str
    end: str
    label: str
    avg_aqi: int


class ActivityAdvice(BaseModel):
    activity: Activity
    label: str
    status: Status
    time_label: str | None
    advice: str


class MemberPlan(BaseModel):
    nickname: str
    sensitivity: Literal["normal", "sensitive", "high"]
    overall_status: Status
    mask: str
    activities: list[ActivityAdvice]
    warnings: list[str]


class Baseline(BaseModel):
    location_label: str
    timezone: str
    current: CurrentAir
    peak_aqi: int | None
    trend: Literal["improving", "worsening", "steady", "unknown"]
    hourly: list[HourPoint]
    best_window: TimeWindow | None
    ventilation: str
    household_actions: list[str]
    members: list[MemberPlan]
    disclaimer: str


class MemberNote(BaseModel):
    nickname: str = Field(max_length=30)
    note: str = Field(max_length=400)


class AiPlan(BaseModel):
    headline: str = Field(max_length=160)
    summary: str = Field(max_length=900)
    member_notes: list[MemberNote] = Field(default_factory=list, max_length=8)


class PlanResponse(BaseModel):
    baseline: Baseline
    ai: AiPlan | None
    ai_status: Literal["ok", "unavailable", "disabled"]
    share_message: str


class AskResponse(BaseModel):
    answer: str
    source: Literal["gemini", "rules"]
