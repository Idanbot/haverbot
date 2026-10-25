"""Hebrew Telegram HTML rendering, independent of Telegram's client objects."""

from dataclasses import dataclass
from datetime import date
from html import escape
from urllib.parse import urlparse

from pyluach.dates import GregorianDate

from haverbot.domain import PROVIDER_NAMES, Forecast, GroupSettings

# A margin below Telegram's 4096 UTF-16 unit limit also covers supplementary emoji.
MAX_UNITS = 4000


def units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Unsafe source URL")
    return escape(url, quote=True)


def header(provider: str, day: date) -> str:
    hebrew = GregorianDate(day.year, day.month, day.day).to_heb().hebrew_date_string()
    return (
        f"🔮 <b>הורוסקופ יומי — {escape(PROVIDER_NAMES[provider])}</b>\n"
        f"📅 {day:%d/%m/%Y} | {hebrew}"
    )


@dataclass(frozen=True, slots=True)
class Button:
    label: str
    data: str


@dataclass(frozen=True, slots=True)
class Message:
    text: str
    buttons: tuple[Button, ...] = ()


def snippet(text: str, words: int, chars: int) -> tuple[str, bool]:
    preview = " ".join(text.split()[:words])
    if units(escape(preview)) > chars:
        # Drop complete words; handle even a single pathological word safely.
        while " " in preview and units(escape(preview)) > chars:
            preview = preview.rsplit(" ", 1)[0]
        if units(escape(preview)) > chars:
            while preview and units(escape(preview)) > chars:
                preview = preview[:-1]
    truncated = preview != text
    return preview + ("…" if truncated else ""), truncated


def bulletin(forecasts: list[Forecast], settings: GroupSettings) -> Message:
    if not forecasts:
        raise ValueError("Empty bulletin")
    provider, day = forecasts[0].provider, forecasts[0].day
    if any(f.provider != provider or f.day != day for f in forecasts):
        raise ValueError("A bulletin must have one provider and date")
    title = header(provider, day)
    # Include HTML syntax in the budget too: conservative even before entities parsing.
    budget = (MAX_UNITS - units(title) - 200) // len(forecasts) - 100
    lines: list[str] = [title]
    buttons: list[Button] = []
    for f in forecasts:
        text, truncated = snippet(f.text, settings.max_words, max(30, budget))
        lines.append(f"• {f.sign.emoji} <b>{f.sign.hebrew}:</b> {escape(text)}")
        if truncated:
            buttons.append(
                Button(
                    f"{f.sign.emoji} התחזית המלאה ל{f.sign.hebrew}",
                    f"full:{provider}:{day.isoformat()}:{f.sign.value}",
                )
            )
    lines.append(f'🔗 <a href="{safe_url(forecasts[0].source_url)}">למקור התחזית</a>')
    result = "\n\n".join(lines)
    # Escaping hostile HTML can expand a preview. Reduce the budget until it fits.
    if units(result) > MAX_UNITS:
        if settings.max_words > 5:
            from dataclasses import replace

            return bulletin(forecasts, replace(settings, max_words=5))
        raise ValueError("Provider content cannot fit a bulletin")
    return Message(result, tuple(buttons))


def full_sign(forecast: Forecast) -> list[Message]:
    prefix = header(forecast.provider, forecast.day)
    prefix += f"\n\n{forecast.sign.emoji} <b>{forecast.sign.hebrew}</b>\n"
    suffix = f'\n\n🔗 <a href="{safe_url(forecast.source_url)}">למקור התחזית</a>'
    chunks: list[str] = []
    current = ""
    # Escaped-word budgeting avoids cutting an entity or breaking Hebrew/emoji.
    for word in forecast.text.split():
        escaped = escape(word)
        pieces = [escaped]
        if units(prefix + escaped + suffix) > MAX_UNITS:
            # Split before escaping to preserve entities, even for unbroken long input.
            pieces = [escape(word[i : i + 200]) for i in range(0, len(word), 200)]
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and units(prefix + candidate + suffix) > MAX_UNITS:
                chunks.append(prefix + current + suffix)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(prefix + current + suffix)
    return [Message(chunk) for chunk in chunks]
