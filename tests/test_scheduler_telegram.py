from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatMemberStatus
from telegram.error import NetworkError

from haverbot.domain import ISRAEL, GroupSettings, Sign
from haverbot.scheduler import Scheduler
from haverbot.service import HoroscopeService
from haverbot.telegram_app import TelegramController


def context(**kwargs) -> Any:
    return SimpleNamespace(**kwargs)


async def test_skip_startup_missed_posts_and_duplicate_ticks(store, now):
    await store.save_settings(GroupSettings(-1, enabled=True, post_time="12:00"))
    service = HoroscopeService(store, [], lambda: now)
    send = AsyncMock()
    scheduler = Scheduler(store, service, send, lambda: now)
    await scheduler.tick()
    send.assert_not_awaited()
    now += timedelta(days=1)
    await scheduler.tick()
    await scheduler.tick()
    assert send.await_count == 1
    # Restarting during that same minute never repeats a delivery.
    restarted = Scheduler(store, service, send, lambda: now - timedelta(seconds=5))
    await restarted.tick()
    assert send.await_count == 1
    now += timedelta(days=1, minutes=1)
    await scheduler.tick()
    assert send.await_count == 1


async def test_dst_repeated_hour_sends_once_and_nonexistent_hour_skipped(store):
    # On Oct 25 2026 Israel repeats 01:00; timestamps differ by one hour.
    now = datetime(2026, 10, 25, 0, 50, tzinfo=ISRAEL)
    await store.save_settings(GroupSettings(-1, enabled=True, post_time="01:30"))
    service = HoroscopeService(store, [], lambda: now)
    send = AsyncMock()
    scheduler = Scheduler(store, service, send, lambda: now)
    now = datetime(2026, 10, 25, 1, 30, tzinfo=ISRAEL, fold=0)
    await scheduler.tick()
    now = datetime(2026, 10, 25, 1, 30, tzinfo=ISRAEL, fold=1)
    await scheduler.tick()
    assert send.await_count == 1
    now = datetime(2026, 3, 27, 1, 59, tzinfo=ISRAEL)
    await store.save_settings(GroupSettings(-1, enabled=True, post_time="02:30"))
    scheduler = Scheduler(store, service, send, lambda: now)
    # Jump over the missing hour by advancing through UTC.
    now = datetime.fromtimestamp(now.timestamp() + 120, ISRAEL)
    assert now.hour == 3
    await scheduler.tick()
    assert send.await_count == 1


async def test_scheduled_unavailable_sends_hebrew_and_bypasses_cooldown(store, now):
    settings = GroupSettings(-1, enabled=True)
    service = HoroscopeService(store, [], lambda: now)
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = TelegramController(store, service, bot)
    await store.claim_cooldown(-1, "bulletin", 15, now)
    await controller.scheduled(settings, now)
    assert "אינה זמינה" in bot.send_message.call_args.kwargs["text"]


def update(chat_id=-1, status="member", data=None) -> Any:
    query = (
        SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock(), data=data)
        if data
        else None
    )
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
        effective_user=SimpleNamespace(id=17),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
        callback_query=query,
    )


@pytest.mark.parametrize("status", ["member", "restricted", "left"])
async def test_non_admin_cannot_change_settings_or_buttons(store, now, status):
    bot: Any = SimpleNamespace(
        send_message=AsyncMock(),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status)),
    )
    service = HoroscopeService(store, [], lambda: now)
    controller = TelegramController(store, service, bot)
    event = update(data="cfg:enabled:toggle")
    await controller.settings(event, context(args=["enabled", "on"]))
    await controller.callback(event, context(args=[]))
    assert not (await store.settings(-1)).enabled
    assert bot.get_chat_member.await_count == 2


async def test_admin_changes_are_group_scoped_and_settings_are_hebrew(store, now):
    bot: Any = SimpleNamespace(
        send_message=AsyncMock(),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)
        ),
    )
    service = HoroscopeService(store, [], lambda: now)
    controller = TelegramController(store, service, bot)
    await controller.settings(update(), context(args=["single_cooldown", "3"]))
    assert (await store.settings(-1)).single_minutes == 3
    assert (await store.settings(-2)).single_minutes == 1
    assert "הגדרות" in bot.send_message.call_args.args[1]
    await controller.callback(update(data="cfg:sign:leo"), context(args=[]))
    assert Sign.LEO not in (await store.settings(-1)).signs


async def test_full_button_is_same_provider_date_after_group_settings_change(store, now, forecasts):
    await store.put_forecasts(forecasts)
    await store.save_settings(GroupSettings(-1, provider="walla"))
    service = HoroscopeService(store, [], lambda: now)
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = TelegramController(store, service, bot)
    await controller.callback(update(data="full:mako:2026-10-25:leo"), context())
    text = bot.send_message.call_args.kwargs["text"]
    assert "mako" in text and "אריה" in text and "25/10/2026" in text
    await controller.callback(update(data="full:mako:2026-10-25:aries"), context())
    assert "שניות" in bot.send_message.call_args.kwargs["text"]


async def test_failed_send_releases_cooldown(store, now, forecasts):
    await store.put_forecasts(forecasts)
    await store.save_settings(GroupSettings(-1, provider="mako"))
    service = HoroscopeService(store, [], lambda: now)
    bot: Any = SimpleNamespace(send_message=AsyncMock(side_effect=NetworkError("offline")))
    controller = TelegramController(store, service, bot)
    with pytest.raises(NetworkError):
        await controller._request(-1)
    bot.send_message.side_effect = None
    await controller._request(-1)
    assert "הורוסקופ יומי" in bot.send_message.call_args.kwargs["text"]


async def test_expansion_obeys_disabled_on_demand_setting(store, now, forecasts):
    await store.put_forecasts(forecasts)
    await store.save_settings(GroupSettings(-1, on_demand=False))
    service = HoroscopeService(store, [], lambda: now)
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = TelegramController(store, service, bot)
    await controller.callback(update(data="full:mako:2026-10-25:leo"), context())
    assert "כבויות" in bot.send_message.call_args.kwargs["text"]


async def test_membership_removal_forgets_group(store, now):
    await store.save_settings(GroupSettings(-1, enabled=True))
    service = HoroscopeService(store, [], lambda: now)
    controller = TelegramController(store, service, context())
    event: Any = SimpleNamespace(
        my_chat_member=SimpleNamespace(
            chat=SimpleNamespace(id=-1),
            new_chat_member=SimpleNamespace(status=ChatMemberStatus.LEFT),
        )
    )
    await controller.membership(event, context())
    assert not await store.groups()


async def test_scheduled_failed_attempt_is_not_retried(store, now):
    await store.save_settings(GroupSettings(-1, enabled=True, post_time="12:00"))
    service = HoroscopeService(store, [], lambda: now)
    send = AsyncMock(side_effect=NetworkError("offline"))
    scheduler = Scheduler(store, service, send, lambda: now)
    now += timedelta(days=1)
    await scheduler.tick()
    await scheduler.tick()
    send.assert_awaited_once()
