"""mako's public mobile page includes all signs and full daily text in its HTML."""

from datetime import date

from haverbot.domain import Forecast, Sign
from haverbot.providers.base import Fetcher, forecast, numeric_date, soup


class MakoProvider:
    id = "mako"
    url = "https://mobile.mako.co.il/spirituality-newage/horoscope?partner=ChannelHeader"

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    @classmethod
    def parse(cls, html: str, day: date, missing: set[Sign]) -> list[Forecast]:
        result: list[Forecast] = []
        for container in soup(html).select(".signContainer"):
            title = container.select_one(".signTitle")
            daily = container.select_one(".forecastTitle")
            closed = container.select_one(".closed")
            if title is None or daily is None or closed is None:
                continue
            try:
                sign = Sign.parse(title.get_text(strip=True))
            except ValueError:
                continue
            label = daily.get_text(" ", strip=True)
            if sign not in missing or "יומית" not in label or numeric_date(label, day) != day:
                continue
            # The first span is full daily content; weekly text follows its own title.
            parts: list[str] = []
            for node in closed.children:
                from bs4 import Tag

                if isinstance(node, Tag) and "forecastTitle" in (node.get("class") or []):
                    break
                parts.append(node.get_text(" ", strip=True))
            item = forecast(cls.id, day, sign, " ".join(parts), cls.url)
            if item:
                result.append(item)
        return result

    async def fetch(self, day: date, missing: set[Sign]) -> list[Forecast]:
        return self.parse(await self.fetcher.get(self.url), day, missing)
