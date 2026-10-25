"""Discover dated daily articles, then extract article body with a validated forecast date."""

import json
import logging
import re
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

from haverbot.domain import Forecast, Sign
from haverbot.providers.base import Fetcher, ProviderError, forecast, numeric_date, soup

logger = logging.getLogger(__name__)


def json_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value] + [child for v in value.values() for child in json_objects(v)]
    if isinstance(value, list):
        return [child for v in value for child in json_objects(v)]
    return []


class IsraelHayomProvider:
    id = "israelhayom"
    url = "https://www.israelhayom.co.il/horoscopes"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    @classmethod
    def discover(cls, html: str, day: date, missing: set[Sign]) -> dict[Sign, str]:
        result: dict[Sign, str] = {}
        for anchor in soup(html).select('a[href*="/horoscopes/"]'):
            text = anchor.get_text(" ", strip=True)
            href = str(anchor.get("href", ""))
            if "יומית" not in text or numeric_date(text, day, year_required=True) != day:
                continue
            url = urljoin(cls.url, href)
            if urlparse(url).hostname not in {"www.israelhayom.co.il", "www.hayom.co.il"}:
                continue
            for sign in missing:
                if re.search(rf"מזל\s+{sign.hebrew}(?:\s|[,،:])", text + " "):
                    result[sign] = url
        return result

    @classmethod
    def parse(cls, html: str, day: date, sign: Sign, url: str) -> Forecast | None:
        document = soup(html)
        title = document.find("h1")
        headline = title.get_text(" ", strip=True) if title else ""
        body = ""
        for script in document.select('script[type="application/ld+json"]'):
            try:
                objects = json_objects(json.loads(script.get_text()))
            except ValueError, TypeError:
                continue
            for obj in objects:
                if obj.get("@type") in ("NewsArticle", "Article"):
                    headline = headline or str(obj.get("headline", ""))
                    candidate = obj.get("articleBody", "")
                    if isinstance(candidate, str):
                        # ArticleBody currently appends a promotional link after the forecast.
                        body = re.split(
                            r"לתחזיות אסטרולוגיות נוספות היכנסו", candidate, maxsplit=1
                        )[0]
        if (
            "יומית" not in headline
            or sign.hebrew not in headline
            or numeric_date(headline, day, year_required=True) != day
        ):
            return None
        if not body:
            section = document.select_one(
                '[itemprop="articleBody"], .article-body, .article-content, '
                '[class*="ArticleBody"], [class*="articleBody"]'
            )
            if section:
                paragraphs = section.select("p")
                body = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
        return forecast(cls.id, day, sign, body, url)

    async def fetch(self, day: date, missing: set[Sign]) -> list[Forecast]:
        links = self.discover(await self.fetcher.get(self.url), day, missing)
        result: list[Forecast] = []
        for sign, url in links.items():
            try:
                item = self.parse(await self.fetcher.get(url), day, sign, url)
                if item:
                    result.append(item)
            except ProviderError as exc:
                if exc.retry_after:
                    if result:
                        return result
                    raise
                logger.info("provider=israelhayom sign=%s unavailable=%s", sign, type(exc).__name__)
        return result
