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
    tokens = text.split()
    selected = tokens[:words] if words else tokens
    target = " ".join(selected)
    if len(selected) == len(tokens) and units(escape(target)) <= chars:
        return target, False
    available = max(0, chars - units("…"))
    pieces: list[str] = []
    used = 0
    for word in selected:
        cost = units(escape(word)) + bool(pieces)
        if used + cost > available:
            if not pieces:
                clipped = ""
                for char in word:
                    if units(escape(clipped + char)) > available:
                        break
                    clipped += char
                pieces.append(clipped)
            break
        pieces.append(word)
        used += cost
    return " ".join(pieces) + "…", True


def bulletin(forecasts: list[Forecast], settings: GroupSettings) -> Message:
    if not forecasts:
        raise ValueError("Empty bulletin")
    provider, day = forecasts[0].provider, forecasts[0].day
    if any(f.provider != provider or f.day != day for f in forecasts):
        raise ValueError("A bulletin must have one provider and date")
    title = header(provider, day)
    footer = f'🔗 <a href="{safe_url(forecasts[0].source_url)}">למקור התחזית</a>'
    prefixes = [f"• {f.sign.emoji} <b>{f.sign.hebrew}:</b> " for f in forecasts]
    remaining = MAX_UNITS - units(title + footer + "".join(prefixes)) - 2 * (len(forecasts) + 1)
    if remaining < len(forecasts):
        raise ValueError("Provider metadata cannot fit a bulletin")
    # Share space fairly, then return unused space from short signs to longer ones.
    desired = []
    for f in forecasts:
        tokens = f.text.split()
        selected = tokens[: settings.max_words] if settings.max_words else tokens
        desired.append(units(escape(" ".join(selected))) + (len(selected) < len(tokens)))
    budgets = [0] * len(forecasts)
    pending = set(range(len(forecasts)))
    while pending:
        share = remaining // len(pending)
        short = {i for i in pending if desired[i] <= share}
        if not short:
            for offset, i in enumerate(sorted(pending)):
                budgets[i] = share + (offset < remaining % len(pending))
            break
        for i in short:
            budgets[i] = desired[i]
            remaining -= desired[i]
        pending -= short
    lines: list[str] = [title]
    buttons: list[Button] = []
    for f, prefix, budget in zip(forecasts, prefixes, budgets, strict=True):
        text, truncated = snippet(f.text, settings.max_words, budget)
        lines.append(prefix + escape(text))
        if truncated:
            buttons.append(
                Button(
                    f"{f.sign.emoji} התחזית המלאה ל{f.sign.hebrew}",
                    f"full:{provider}:{day.isoformat()}:{f.sign.value}",
                )
            )
    lines.append(footer)
    return Message("\n\n".join(lines), tuple(buttons))


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
