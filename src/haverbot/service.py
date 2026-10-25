"""Application services: cache completion, provider fallback and request authorization."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from haverbot.domain import PROVIDER_NAMES, Forecast, GroupSettings, Sign, israel_now
from haverbot.providers.base import Provider, ProviderError
from haverbot.storage import Bucket, Store

logger = logging.getLogger(__name__)
UNAVAILABLE = (
    "התחזית להיום עדיין אינה זמינה במקור שנבחר או במקורות הגיבוי. "
    "אנחנו מנסים שוב ברקע; אפשר לבקש שוב בעוד כמה דקות."
)
EXPIRED = "התחזית המבוקשת כבר אינה שמורה במטמון (נשמרים עד 7 ימים)."
DISABLED = "בקשות הורוסקופ לפי דרישה כבויות בקבוצה זו. אפשר לפנות למנהלי הקבוצה."


@dataclass(slots=True)
class RetryState:
    attempts: int = 0
    due: datetime | None = None


@dataclass(frozen=True, slots=True)
class RequestResult:
    forecasts: tuple[Forecast, ...] = ()
    error: str | None = None
    # Exact reservation time enables releasing failed sends without clearing a newer claim.
    reserved_at: datetime | None = None
    bucket: Bucket = "bulletin"


class HoroscopeService:
    def __init__(
        self, store: Store, providers: list[Provider], clock: Callable[[], datetime] = israel_now
    ) -> None:
        self.store = store
        self.providers = {p.id: p for p in providers}
        self.clock = clock
        self._states: dict[tuple[str, date], RetryState] = {}
        self._tasks: dict[tuple[str, date], asyncio.Task[None]] = {}

    def request_refresh(self, provider: str, day: date) -> None:
        if (
            provider not in PROVIDER_NAMES
            or provider not in self.providers
            or day != self.clock().date()
        ):
            return
        key = provider, day
        state = self._states.setdefault(key, RetryState())
        if state.due is not None and state.due > self.clock():
            return
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return
        self._tasks[key] = asyncio.create_task(
            self._refresh(provider, day), name=f"fetch-{provider}"
        )

    async def _refresh(self, provider: str, day: date) -> None:
        state = self._states[provider, day]
        retry_after = 0.0
        try:
            cached = await self.store.forecasts(provider, day)
            missing = set(Sign) - cached.keys()
            if not missing:
                state.due = self.clock() + timedelta(days=1)
                return
            items = await self.providers[provider].fetch(day, missing)
            valid = [
                f for f in items if f.provider == provider and f.day == day and f.sign in missing
            ]
            # A worker started yesterday must never reintroduce expired/future data after pruning.
            if self.clock().date() == day:
                await self.store.put_forecasts(valid)
            logger.info("provider=%s cached=%d received=%d", provider, len(cached), len(valid))
        except ProviderError as exc:
            retry_after = exc.retry_after
            logger.warning("provider=%s unavailable=%s", provider, str(exc))
        except Exception as exc:
            # Telegram request URLs contain the token: never log exception messages/tracebacks.
            logger.error("provider=%s unexpected=%s", provider, type(exc).__name__)
        state.attempts += 1
        delay = max(retry_after, min(1800, 60 * 2 ** min(state.attempts - 1, 5)))
        state.due = self.clock() + timedelta(seconds=delay)

    async def maintain(self) -> None:
        today = self.clock().date()
        await self.store.prune(today)
        self._states = {key: state for key, state in self._states.items() if key[1] == today}
        for key, task in list(self._tasks.items()):
            if key[1] != today:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                del self._tasks[key]
        groups = await self.store.groups()
        needed: set[str] = set()
        for settings in groups:
            if settings.enabled or settings.on_demand:
                needed.update(self.provider_order(settings))
        for provider in needed:
            self.request_refresh(provider, today)

    def provider_order(self, settings: GroupSettings) -> list[str]:
        return [settings.provider] + (
            [p for p in PROVIDER_NAMES if p != settings.provider] if settings.fallback else []
        )

    async def resolve(
        self, settings: GroupSettings, day: date, signs: tuple[Sign, ...]
    ) -> tuple[Forecast, ...]:
        for provider in self.provider_order(settings):
            cached = await self.store.forecasts(provider, day)
            if all(sign in cached for sign in signs):
                return tuple(cached[s] for s in signs)
            self.request_refresh(provider, day)
        return ()

    async def request(
        self,
        settings: GroupSettings,
        sign: Sign | None = None,
        *,
        exact: tuple[str, date, Sign] | None = None,
    ) -> RequestResult:
        if not settings.on_demand:
            return RequestResult(error=DISABLED)
        await self.store.ensure_settings(settings)
        now = self.clock()
        bucket: Bucket = "single" if sign is not None or exact is not None else "bulletin"
        if exact is not None:
            provider, day, exact_sign = exact
            if provider not in PROVIDER_NAMES:
                return RequestResult(error=UNAVAILABLE)
            if not now.date() - timedelta(days=6) <= day <= now.date():
                return RequestResult(error=EXPIRED)
            cached = await self.store.forecasts(provider, day)
            forecasts = (cached[exact_sign],) if exact_sign in cached else ()
        else:
            forecasts = await self.resolve(
                settings, now.date(), (sign,) if sign else settings.signs
            )
        if not forecasts:
            return RequestResult(error=EXPIRED if exact else UNAVAILABLE)
        minutes = settings.single_minutes if bucket == "single" else settings.bulletin_minutes
        remaining = await self.store.claim_cooldown(settings.chat_id, bucket, minutes, now)
        if remaining:
            return RequestResult(error=f"כדי למנוע הצפה, אפשר לבקש שוב בעוד {remaining} שניות.")
        return RequestResult(forecasts, reserved_at=now, bucket=bucket)

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
