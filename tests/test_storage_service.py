import asyncio
from dataclasses import replace
from datetime import timedelta

from haverbot.domain import GroupSettings, Sign
from haverbot.providers.base import ProviderError
from haverbot.service import DISABLED, EXPIRED, UNAVAILABLE, HoroscopeService
from haverbot.storage import Store


class FakeProvider:
    id = "mako"

    def __init__(self, forecasts):
        self.items = forecasts
        self.calls = []
        self.error = None
        self.gate = None

    async def fetch(self, day, missing):
        self.calls.append((day, missing))
        if self.gate:
            await self.gate.wait()
        if self.error:
            raise self.error
        return [f for f in self.items if f.sign in missing]


async def drain(service):
    await asyncio.gather(*service._tasks.values())


async def test_settings_persist_and_group_data_isolated(tmp_path):
    path = str(tmp_path / "cache.db")
    store = Store(path)
    await store.open()
    settings = GroupSettings(-123, provider="mako", signs=(Sign.LEO,), enabled=True)
    await store.save_settings(settings)
    assert not (await store.settings(-456)).enabled
    await store.close()
    reopened = Store(path)
    await reopened.open()
    assert await reopened.settings(-123) == settings
    await reopened.close()


async def test_cache_key_retention_and_immutable_same_day(store, forecasts, now):
    await store.put_forecasts(forecasts)
    await store.put_forecasts([replace(forecasts[0], text="changed")])
    assert (await store.forecasts("mako", now.date()))[Sign.ARIES] == forecasts[0]
    assert not await store.forecasts("walla", now.date())
    await store.put_forecasts(
        [replace(forecasts[0], day=now.date() - timedelta(days=i)) for i in range(1, 10)]
    )
    await store.prune(now.date())
    assert await store.forecasts("mako", now.date() - timedelta(days=6))
    assert not await store.forecasts("mako", now.date() - timedelta(days=7))


async def test_concurrent_requests_and_separate_shared_cooldowns(store, forecasts, now):
    await store.put_forecasts(forecasts)
    service = HoroscopeService(store, [], lambda: now)
    settings = GroupSettings(-1, provider="mako")
    results = await asyncio.gather(*(service.request(settings) for _ in range(10)))
    assert sum(bool(r.forecasts) for r in results) == 1
    assert (await service.request(settings, Sign.LEO)).forecasts
    error = (await service.request(settings, Sign.ARIES)).error
    assert error and "60" in error
    assert (await service.request(replace(settings, chat_id=-2), Sign.ARIES)).forecasts
    now += timedelta(seconds=60)
    assert (await service.request(settings, Sign.ARIES)).forecasts
    assert not (await service.request(settings)).forecasts
    now += timedelta(minutes=14)
    assert (await service.request(settings)).forecasts


async def test_disabled_and_unavailable_do_not_consume_cooldown(store, forecasts, now):
    service = HoroscopeService(store, [], lambda: now)
    settings = GroupSettings(-1, provider="mako", fallback=False)
    assert (await service.request(settings)).error == UNAVAILABLE
    await store.put_forecasts(forecasts)
    assert (await service.request(replace(settings, on_demand=False))).error == DISABLED
    assert (await service.request(settings)).forecasts


async def test_fallback_is_one_complete_provider_and_exact_buttons_never_switch(
    store, forecasts, now
):
    await store.put_forecasts(forecasts)
    partial = [replace(forecasts[0], provider="israelhayom")]
    await store.put_forecasts(partial)
    service = HoroscopeService(store, [], lambda: now)
    settings = GroupSettings(-1)
    result = await service.request(settings)
    assert {f.provider for f in result.forecasts} == {"mako"}
    exact = ("israelhayom", now.date(), Sign.LEO)
    assert (await service.request(settings, exact=exact)).error == EXPIRED
    old = ("mako", now.date() - timedelta(days=7), Sign.LEO)
    assert (await service.request(settings, exact=old)).error == EXPIRED
    result = await service.request(settings, exact=("mako", now.date(), Sign.LEO))
    assert result.forecasts[0] == forecasts[4]


async def test_selected_signs_can_use_complete_subset_without_mixing_providers(
    store, forecasts, now
):
    await store.put_forecasts([replace(forecasts[4], provider="israelhayom")])
    service = HoroscopeService(store, [], lambda: now)
    settings = GroupSettings(-1, signs=(Sign.LEO,))
    assert (await service.request(settings)).forecasts[0].provider == "israelhayom"


async def test_cooldown_survives_restart_and_can_be_released(tmp_path, now):
    path = str(tmp_path / "state.db")
    store = Store(path)
    await store.open()
    assert await store.claim_cooldown(-1, "single", 1, now) == 0
    await store.close()
    await store.open()
    assert await store.claim_cooldown(-1, "single", 1, now) == 60
    await store.release_cooldown(-1, "single", now - timedelta(seconds=1))
    assert await store.claim_cooldown(-1, "single", 1, now) == 60
    await store.release_cooldown(-1, "single", now)
    assert await store.claim_cooldown(-1, "single", 1, now) == 0
    await store.close()


async def test_partial_cache_retry_fetches_only_missing_and_coalesces(store, forecasts, now):
    provider = FakeProvider(forecasts[:2])
    provider.gate = asyncio.Event()
    service = HoroscopeService(store, [provider], lambda: now)
    settings = GroupSettings(-1, provider="mako", fallback=False)
    for _ in range(5):
        assert (await service.request(settings)).error == UNAVAILABLE
    await asyncio.sleep(0)
    assert len(provider.calls) == 1
    provider.gate.set()
    await drain(service)
    assert len(await store.forecasts("mako", now.date())) == 2
    provider.items = forecasts
    service.request_refresh("mako", now.date())
    await drain(service)
    assert len(provider.calls) == 1
    now += timedelta(seconds=60)
    service.request_refresh("mako", now.date())
    await drain(service)
    assert provider.calls[-1][1] == set(Sign) - {Sign.ARIES, Sign.TAURUS}
    assert len(await store.forecasts("mako", now.date())) == 12
    await service.close()


async def test_retry_after_and_day_rollover(store, forecasts, now):
    provider = FakeProvider(forecasts)
    provider.error = ProviderError("blocked", retry_after=3600)
    service = HoroscopeService(store, [provider], lambda: now)
    service.request_refresh("mako", now.date())
    await drain(service)
    now += timedelta(minutes=30)
    service.request_refresh("mako", now.date())
    await drain(service)
    assert len(provider.calls) == 1
    await store.save_settings(GroupSettings(-1, provider="mako", fallback=False))
    now += timedelta(days=1)
    await service.maintain()
    await drain(service)
    assert len(provider.calls) == 2
    await service.close()


async def test_foreign_provider_and_stale_results_are_not_cached(store, forecasts, now):
    provider = FakeProvider(
        [
            replace(forecasts[0], day=now.date() - timedelta(days=1)),
            replace(forecasts[1], provider="walla"),
        ]
    )
    service = HoroscopeService(store, [provider], lambda: now)
    service.request_refresh("mako", now.date())
    await drain(service)
    assert not await store.forecasts("mako", now.date())
    await service.close()


async def test_forget_removes_subscription_cooldowns_and_deliveries(store, now):
    await store.save_settings(GroupSettings(-1, enabled=True))
    await store.claim_cooldown(-1, "bulletin", 15, now)
    assert await store.claim_delivery(-1, now.date())
    await store.forget_group(-1)
    assert not await store.groups()
    assert await store.claim_cooldown(-1, "bulletin", 15, now) == 0
    assert await store.claim_delivery(-1, now.date())


async def test_cooldown_atomic_across_connections(tmp_path, now):
    path = str(tmp_path / "shared.db")
    first, second = Store(path), Store(path)
    await first.open()
    await second.open()
    try:
        outcomes = await asyncio.gather(
            first.claim_cooldown(-1, "single", 1, now),
            second.claim_cooldown(-1, "single", 1, now),
        )
        assert sorted(outcomes) == [0, 60]
    finally:
        await first.close()
        await second.close()


async def test_first_on_demand_request_enrolls_background_retry(store, now, forecasts):
    provider = FakeProvider([])
    service = HoroscopeService(store, [provider], lambda: now)
    settings = GroupSettings(-1, provider="mako", fallback=False)
    assert (await service.request(settings)).error == UNAVAILABLE
    await drain(service)
    assert len(provider.calls) == 1
    now += timedelta(seconds=60)
    provider.items = forecasts
    await service.maintain()
    await drain(service)
    assert len(provider.calls) == 2
    assert (await service.request(settings)).forecasts
    await service.close()
