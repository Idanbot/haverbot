"""Walla sign pages: only the dated daily section qualifies, never profile/yearly text."""

import logging
from datetime import date

from haverbot.domain import Forecast, Sign
from haverbot.providers.base import Fetcher, ProviderError, forecast, numeric_date, soup

logger = logging.getLogger(__name__)


class WallaProvider:
    id = "walla"
    base = "https://horoscope.walla.co.il"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    @classmethod
    def parse(cls, html: str, day: date, sign: Sign, url: str) -> Forecast | None:
        document = soup(html)
        daily_section = document.select_one(".today-horoscope")
        if daily_section is None:
            return None
        section = daily_section.select_one(".daily-forecast") or daily_section
        # A fetch date is not evidence of a forecast date.
        if numeric_date(section.get_text(" ", strip=True), day) != day:
            return None
        content = section.select("p")
        if not content:
            return None
        return forecast(
            cls.id, day, sign, " ".join(p.get_text(" ", strip=True) for p in content), url
        )

    async def fetch(self, day: date, missing: set[Sign]) -> list[Forecast]:
        result: list[Forecast] = []
        for index, sign in enumerate(Sign, start=1):
            if sign not in missing:
                continue
            url = f"{self.base}/zodiac/{index}"
            try:
                item = self.parse(await self.fetcher.get(url), day, sign, url)
                if item:
                    result.append(item)
            except ProviderError as exc:
                if exc.retry_after:
                    if result:
                        return result
                    raise
                logger.info("provider=walla sign=%s unavailable=%s", sign, type(exc).__name__)
        return result
