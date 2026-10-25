"""Aggregate Walla's dated per-sign daily API, with rendered-page fallback."""

import json
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

    @staticmethod
    def daily_url(day: date, sign: Sign) -> str:
        # This is the public endpoint and date format used by Walla's frontend.
        index = list(Sign).index(sign) + 1
        return (
            f"https://dal.walla.co.il/horoscope/daily?id={index}"
            f"&date={day:%d%m%Y}&from=horoscope.walla.co.il"
        )

    @classmethod
    def parse_daily(cls, payload: str, day: date, sign: Sign, url: str) -> Forecast | None:
        try:
            data = json.loads(payload)
        except ValueError, TypeError:
            return None
        if not isinstance(data, dict) or data.get("result") != "success":
            return None
        content = data.get("data")
        if not isinstance(content, dict) or not isinstance(content.get("text"), str):
            return None
        # The API binds the forecast date and sign in the request, rather than
        # repeating them in its {title, text} response. Never use an undated API.
        return forecast(cls.id, day, sign, soup(content["text"]).get_text(" ", strip=True), url)

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
                item = None
                try:
                    payload = await self.fetcher.get(self.daily_url(day, sign))
                    item = self.parse_daily(payload, day, sign, url)
                except ProviderError as exc:
                    # Walla returns 404 when that sign/date has no forecast.
                    # Access denials and throttling must retain their backoff.
                    if exc.status_code != 404:
                        raise
                if item is None:
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
