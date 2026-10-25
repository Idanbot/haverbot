"""Wall-clock scheduling: current minute only, durable at-most-once attempts per local day."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from haverbot.domain import ISRAEL, GroupSettings, israel_now
from haverbot.service import HoroscopeService
from haverbot.storage import Store

logger = logging.getLogger(__name__)
ScheduledSender = Callable[[GroupSettings, datetime], Awaitable[None]]


class Scheduler:
    def __init__(
        self,
        store: Store,
        service: HoroscopeService,
        send: ScheduledSender,
        clock: Callable[[], datetime] = israel_now,
        *,
        extra_tick: Callable[[datetime], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store
        self.service = service
        self.send = send
        self.clock = clock
        self.extra_tick = extra_tick
        # Skip even the current minute on startup: never replay a missed delivery.
        self.started = clock().timestamp()
        self._tick_lock = asyncio.Lock()

    async def tick(self) -> None:
        async with self._tick_lock:
            now = self.clock().astimezone(ISRAEL)
            minute = now.replace(second=0, microsecond=0)
            if minute.timestamp() <= self.started:
                return
            for settings in await self.store.groups():
                if not settings.enabled or settings.post_time != now.strftime("%H:%M"):
                    continue
                if not await self.store.claim_delivery(settings.chat_id, now.date()):
                    continue
                try:
                    # Expire work at the end of its minute; no catch-up after slow network calls.
                    remaining = minute.timestamp() + 60 - self.clock().timestamp()
                    if remaining > 0:
                        async with asyncio.timeout(remaining):
                            await self.send(settings, now)
                except Exception as exc:
                    logger.warning("scheduled_delivery_failed=%s", type(exc).__name__)
            if self.extra_tick is not None:
                try:
                    remaining = minute.timestamp() + 60 - self.clock().timestamp()
                    if remaining > 0:
                        async with asyncio.timeout(remaining):
                            await self.extra_tick(now)
                except Exception as exc:
                    logger.warning("feature_delivery_failed=%s", type(exc).__name__)

    async def run(self) -> None:
        while True:
            try:
                await self.service.maintain()
                await self.tick()
            except Exception as exc:
                logger.error("maintenance_failed=%s", type(exc).__name__)
            await asyncio.sleep(15)
