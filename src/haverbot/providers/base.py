"""Provider contract and bounded, polite HTTP fetching."""

import asyncio
import logging
import re
import time
from datetime import date, datetime
from typing import Protocol
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from haverbot.domain import ISRAEL, Forecast, Sign

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    def __init__(self, message: str, retry_after: float = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Fetcher(Protocol):
    async def get(self, url: str) -> str: ...


class Provider(Protocol):
    id: str

    async def fetch(self, day: date, missing: set[Sign]) -> list[Forecast]: ...


class HttpFetcher:
    """One client, bounded response size and one request every two seconds per host."""

    def __init__(self, client: httpx.AsyncClient, interval: float = 2) -> None:
        self.client = client
        self.interval = interval
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def get(self, url: str) -> str:
        host = urlparse(url).hostname or ""
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self.interval - (time.monotonic() - self._last.get(host, 0))
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = time.monotonic()
            try:
                async with self.client.stream("GET", url) as response:
                    if response.status_code == 429:
                        value = response.headers.get("Retry-After", "60")
                        try:
                            retry_after = float(value)
                        except ValueError:
                            try:
                                from email.utils import parsedate_to_datetime

                                retry_after = (
                                    parsedate_to_datetime(value) - datetime.now(ISRAEL)
                                ).total_seconds()
                            except ValueError, TypeError:
                                retry_after = 60
                        raise ProviderError("Rate limited", max(60, min(retry_after, 86400)))
                    if response.status_code in {401, 403}:
                        raise ProviderError("Automated access denied", 3600)
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 5_000_000:
                            raise ProviderError("Response too large")
                    # These providers publish UTF-8, sometimes with a misleading legacy header.
                    text = body.decode("utf-8", errors="replace")
            except httpx.HTTPError as exc:
                # Do not include URLs or response bodies in logs.
                raise ProviderError(type(exc).__name__) from None
            if any(
                marker in text.lower()
                for marker in (
                    "radware block page",
                    "<title>access denied",
                    "please solve this captcha",
                )
            ):
                raise ProviderError("Automated access challenge", 3600)
            return text


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def clean_text(text: str) -> str:
    # Remove bidi override/control characters supplied by remote HTML. Hebrew stays logical-order.
    text = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text)
    return " ".join(text.split())


def numeric_date(text: str, reference: date, *, year_required: bool = False) -> date | None:
    pattern = r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?!\d)"
    for match in re.finditer(pattern, text):
        d, m, year = match.groups()
        if year_required and not year:
            continue
        y = int(year) if year else reference.year
        if y < 100:
            y += 2000
        try:
            return date(y, int(m), int(d))
        except ValueError:
            continue
    return None


def forecast(provider: str, day: date, sign: Sign, text: str, url: str) -> Forecast | None:
    text = clean_text(text)
    if len(text) < 15 or any(
        marker in text
        for marker in (
            "מתעדכנת ברגעים אלה",
            "התחזית אינה זמינה",
            "בקרוב תתפרסם",
        )
    ):
        return None
    return Forecast(provider, day, sign, text[:50_000], url)
