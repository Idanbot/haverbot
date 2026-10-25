"""Framework-independent models and validation shared by every feature layer."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

ISRAEL = ZoneInfo("Asia/Jerusalem")


class Sign(StrEnum):
    ARIES = "aries"
    TAURUS = "taurus"
    GEMINI = "gemini"
    CANCER = "cancer"
    LEO = "leo"
    VIRGO = "virgo"
    LIBRA = "libra"
    SCORPIO = "scorpio"
    SAGITTARIUS = "sagittarius"
    CAPRICORN = "capricorn"
    AQUARIUS = "aquarius"
    PISCES = "pisces"

    @property
    def hebrew(self) -> str:
        return SIGN_LABELS[self][0]

    @property
    def emoji(self) -> str:
        return SIGN_LABELS[self][1]

    @classmethod
    def parse(cls, value: str) -> Sign:
        value = value.strip().lower()
        for sign in cls:
            if value in (sign.value, sign.hebrew):
                return sign
        raise ValueError("מזל לא מוכר. אפשר להשתמש בשם בעברית או באנגלית, למשל אריה או leo.")


SIGN_LABELS: dict[Sign, tuple[str, str]] = dict(
    zip(
        Sign,
        [
            ("טלה", "♈"),
            ("שור", "♉"),
            ("תאומים", "♊"),
            ("סרטן", "♋"),
            ("אריה", "♌"),
            ("בתולה", "♍"),
            ("מאזניים", "♎"),
            ("עקרב", "♏"),
            ("קשת", "♐"),
            ("גדי", "♑"),
            ("דלי", "♒"),
            ("דגים", "♓"),
        ],
        strict=True,
    )
)
PROVIDER_NAMES = {"mako": "mako", "israelhayom": "ישראל היום"}
UNAVAILABLE_PROVIDERS = {"walla": "וואלה"}


def israel_now() -> datetime:
    return datetime.now(ISRAEL)


def validate_time(value: str) -> str:
    import re

    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("יש להזין שעה בפורמט HH:MM בין 00:00 ל־23:59.")
    return value


def validate_minutes(value: int) -> int:
    if not 1 <= value <= 1440:
        raise ValueError("זמן ההמתנה חייב להיות בין דקה אחת ל־1440 דקות.")
    return value


@dataclass(frozen=True, slots=True)
class Forecast:
    provider: str
    day: date
    sign: Sign
    text: str
    source_url: str


@dataclass(frozen=True, slots=True)
class GroupSettings:
    chat_id: int
    enabled: bool = False
    post_time: str = "08:00"
    provider: str = "mako"
    fallback: bool = True
    signs: tuple[Sign, ...] = tuple(Sign)
    on_demand: bool = True
    bulletin_minutes: int = 15
    single_minutes: int = 1
    max_words: int = 0

    def __post_init__(self) -> None:
        validate_time(self.post_time)
        validate_minutes(self.bulletin_minutes)
        validate_minutes(self.single_minutes)
        if self.provider in UNAVAILABLE_PROVIDERS:
            raise ValueError("המקור אינו זמין כרגע. אפשר לבחור mako או israelhayom.")
        if self.provider not in PROVIDER_NAMES:
            raise ValueError("מקור לא מוכר.")
        if not self.signs or len(set(self.signs)) != len(self.signs):
            raise ValueError("יש לבחור לפחות מזל אחד, ללא כפילויות.")
        if self.max_words != 0 and not 5 <= self.max_words <= 100:
            raise ValueError("מספר המילים חייב להיות בין 5 ל־100.")
