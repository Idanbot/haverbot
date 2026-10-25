"""Explicit, read-only live diagnostics; never loads the bot token or contacts Telegram."""

import argparse
import asyncio

import httpx

from haverbot.domain import Sign, israel_now
from haverbot.providers.base import HttpFetcher, ProviderError
from haverbot.providers.israelhayom import IsraelHayomProvider
from haverbot.providers.mako import MakoProvider
from haverbot.providers.walla import WallaProvider


async def check(names: list[str]) -> int:
    day = israel_now().date()
    success = False
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "Haverbot/0.1 (daily horoscope cache; Python HTTPX)"},
    ) as client:
        fetcher = HttpFetcher(client)
        providers = [IsraelHayomProvider(fetcher), WallaProvider(fetcher), MakoProvider(fetcher)]
        for provider in providers:
            if names and provider.id not in names:
                continue
            try:
                forecasts = await provider.fetch(day, set(Sign))
                print(
                    f"{provider.id}: {len(forecasts)}/12 signs for {day} (forecast text withheld)"
                )
                success = success or len({f.sign for f in forecasts}) == 12
            except ProviderError as exc:
                print(f"{provider.id}: unavailable ({exc})")
    return 0 if success else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("providers", nargs="*", choices=["israelhayom", "walla", "mako"])
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check(args.providers)))


if __name__ == "__main__":
    main()
