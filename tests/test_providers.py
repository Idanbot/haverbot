from datetime import date
from pathlib import Path

import httpx
import pytest

from haverbot.domain import Sign
from haverbot.providers.base import HttpFetcher, ProviderError, numeric_date
from haverbot.providers.israelhayom import IsraelHayomProvider
from haverbot.providers.mako import MakoProvider
from haverbot.providers.walla import WallaProvider

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 10, 25)


def fixture(name):
    return (FIXTURES / name).read_text()


def test_mako_full_daily_only_and_rejects_stale_placeholder():
    items = MakoProvider.parse(fixture("mako.html"), DAY, set(Sign))
    assert [f.sign for f in items] == [Sign.ARIES]
    assert "המלא" in items[0].text
    assert "שבועי" not in items[0].text
    assert "תקציר" not in items[0].text
    assert not MakoProvider.parse(fixture("mako.html"), date(2026, 10, 26), set(Sign))


def test_mako_missing_filter():
    assert not MakoProvider.parse(fixture("mako.html"), DAY, {Sign.LEO})


def test_walla_requires_dated_daily_content():
    html = fixture("walla.html")
    item = WallaProvider.parse(html, DAY, Sign.LEO, "https://horoscope.walla.co.il/zodiac/5")
    assert item and "יומית" in item.text and "פרופיל" not in item.text
    assert WallaProvider.parse(html, date(2026, 10, 26), Sign.LEO, "https://example.com") is None
    assert (
        WallaProvider.parse(
            '<section class="today-horoscope"><figure>טלה</figure></section>'
            "<p>תחזית שנתית ארוכה שאינה התחזית היומית</p>",
            DAY,
            Sign.ARIES,
            "https://example.com",
        )
        is None
    )
    assert (
        WallaProvider.parse(html.replace("25.10.2026", ""), DAY, Sign.LEO, "https://x.co") is None
    )


def test_walla_accepts_dated_text_in_updated_daily_section_markup():
    html = (
        '<section class="today-horoscope"><p>25.10.2026</p>'
        "<p>היום צפויה לכם תחזית יומית ארוכה מספיק.</p></section>"
    )
    item = WallaProvider.parse(html, DAY, Sign.ARIES, "https://horoscope.walla.co.il/zodiac/1")
    assert item and "תחזית יומית" in item.text


def test_israelhayom_discovery_rejects_old_weekly_and_foreign_links():
    links = IsraelHayomProvider.discover(fixture("israelhayom-index.html"), DAY, set(Sign))
    assert links == {Sign.ARIES: "https://www.israelhayom.co.il/horoscopes/aries/article/123"}


def test_israelhayom_forecast_day_uses_headline_not_previous_night_publication():
    html = fixture("israelhayom-article.html")
    item = IsraelHayomProvider.parse(html, DAY, Sign.ARIES, "https://www.israelhayom.co.il/a")
    assert item and "ניווט" in item.text
    assert IsraelHayomProvider.parse(html, DAY, Sign.LEO, "https://example.com") is None
    assert IsraelHayomProvider.parse(html, date(2026, 10, 24), Sign.ARIES, "https://x.co") is None


def test_israelhayom_html_body_and_broken_json():
    html = "<h1>מזל טלה, תחזית הורוסקופ יומית 25.10.2026</h1>"
    html += '<script type="application/ld+json">bad</script>'
    html += '<div itemprop="articleBody"><p>תחזית יומית ארוכה מספיק לבדיקה.</p></div>'
    assert IsraelHayomProvider.parse(html, DAY, Sign.ARIES, "https://example.com")


def test_israelhayom_removes_article_navigation_from_structured_forecast():
    html = """
    <h1>מזל טלה, תחזית הורוסקופ יומית 25.10.2026</h1>
    <script type="application/ld+json">
    {"@type":"NewsArticle","headline":"מזל טלה, תחזית הורוסקופ יומית 25.10.2026",
    "articleBody":"תחזית יומית ארוכה מספיק עבור טלה. לתחזיות אסטרולוגיות נוספות היכנסו לאתר אחר"}
    </script>
    """
    item = IsraelHayomProvider.parse(html, DAY, Sign.ARIES, "https://example.com")
    assert item and item.text == "תחזית יומית ארוכה מספיק עבור טלה."


@pytest.mark.parametrize(
    "value,expected",
    [
        ("25/10", DAY),
        ("25.10.2026", DAY),
        ("25/10/26", DAY),
        ("32/13/2026", None),
    ],
)
def test_numeric_dates(value, expected):
    assert numeric_date(value, DAY) == expected


@pytest.mark.parametrize(
    "status,body,retry",
    [
        (403, "denied", 3600),
        (429, "slow", 120),
        (200, "<title>Access Denied</title>", 3600),
        (500, "error", 0),
    ],
)
async def test_http_failures_have_safe_errors_and_backoff(status, body, retry):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text=body, headers={"Retry-After": "120"})
        )
    ) as client:
        with pytest.raises(ProviderError) as error:
            await HttpFetcher(client, interval=0).get("https://example.com/secret-url")
        assert error.value.retry_after == retry
        assert "secret-url" not in str(error.value)


async def test_fetcher_reads_utf8_and_rejects_oversize():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content="שלום".encode(), headers={"content-type": "text/html; charset=latin1"}
            )
        )
    ) as client:
        assert await HttpFetcher(client, 0).get("https://example.com") == "שלום"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 5_000_001))
    ) as client:
        with pytest.raises(ProviderError, match="too large"):
            await HttpFetcher(client, 0).get("https://example.com")


async def test_mako_adapter_over_mocked_network():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=fixture("mako.html"))
        )
    ) as client:
        items = await MakoProvider(HttpFetcher(client, 0)).fetch(DAY, set(Sign))
        assert len(items) == 1


async def test_israelhayom_adapter_discovers_then_fetches():
    def response(request):
        name = (
            "israelhayom-index.html"
            if request.url.path == "/horoscopes"
            else "israelhayom-article.html"
        )
        return httpx.Response(200, text=fixture(name))

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        items = await IsraelHayomProvider(HttpFetcher(client, 0)).fetch(DAY, set(Sign))
        assert len(items) == 1 and items[0].sign == Sign.ARIES


async def test_walla_fetch_keeps_partial_results_when_next_sign_blocked():
    def response(request):
        if request.url.path == "/zodiac/1":
            return httpx.Response(200, text=fixture("walla.html"))
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        items = await WallaProvider(HttpFetcher(client, 0)).fetch(DAY, {Sign.ARIES, Sign.LEO})
        assert len(items) == 1 and items[0].sign == Sign.ARIES


async def test_walla_access_failure_backoff_is_preserved():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "300"})
        )
    ) as client:
        with pytest.raises(ProviderError) as error:
            await WallaProvider(HttpFetcher(client, 0)).fetch(DAY, {Sign.ARIES})
        assert error.value.retry_after == 300
