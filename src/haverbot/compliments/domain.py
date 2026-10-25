"""Compliment templates and private-chat preferences."""

import re
from dataclasses import dataclass
from enum import StrEnum

from haverbot.domain import validate_time


class Gender(StrEnum):
    FEMALE = "נקבה"
    MALE = "זכר"


def validate_name(value: str) -> str:
    value = " ".join(value.split())
    if not 1 <= len(value) <= 40 or not re.fullmatch(r"[א-ת\u0591-\u05c7 '\"׳״־-]+", value):
        raise ValueError("יש להזין שם בעברית, באורך של עד ארבעים תווים.")
    if not re.search(r"[א-ת]", value):
        raise ValueError("יש להזין שם בעברית.")
    return value


@dataclass(frozen=True, slots=True)
class Compliment:
    id: int
    gender: Gender
    text: str

    def render(self, name: str) -> str:
        return self.text.format(name=validate_name(name))


@dataclass(frozen=True, slots=True)
class Profile:
    chat_id: int
    name: str
    gender: Gender
    enabled: bool = False
    post_time: str = "09:00"
    last_id: int | None = None

    def __post_init__(self) -> None:
        if self.chat_id <= 0:
            raise ValueError("מחמאות זמינות בשיחה פרטית בלבד.")
        validate_name(self.name)
        validate_time(self.post_time)
