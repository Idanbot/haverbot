"""Environment-only configuration; never include the token in object representations."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Config:
    token: str = field(repr=False)
    database: str = "data/haverbot.sqlite3"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv()
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN must be set in the environment or .env")
        return cls(
            token,
            os.environ.get("HAVERBOT_DATABASE", "data/haverbot.sqlite3"),
            os.environ.get("HAVERBOT_LOG_LEVEL", "INFO").upper(),
        )
