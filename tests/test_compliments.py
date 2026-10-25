import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from haverbot.compliments.catalog import bundled_catalog, load_catalog
from haverbot.compliments.domain import Compliment, Gender, Profile, validate_name
from haverbot.compliments.repository import ComplimentRepository
from haverbot.compliments.telegram import ComplimentController
from haverbot.scheduler import Scheduler
from haverbot.service import HoroscopeService
from haverbot.storage import Store


def event(chat_id=17, text=None, data=None, kind="private") -> Any:
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=kind),
        effective_message=SimpleNamespace(text=text, reply_text=AsyncMock()),
        callback_query=SimpleNamespace(data=data, answer=AsyncMock()) if data else None,
    )


def context(*args) -> Any:
    return SimpleNamespace(args=list(args))


@pytest.fixture
async def repository(store):
    repo = ComplimentRepository(store)
    await repo.import_catalog(bundled_catalog())
    return repo


def test_catalog_has_500_distinct_female_hebrew_personalized_lines():
    items = bundled_catalog()
    assert len(items) == 500
    assert {item.id for item in items} == set(range(1, 501))
    assert len({item.text for item in items}) == 500
    assert all(item.gender == Gender.FEMALE for item in items)
    assert all(
        "נועה" in item.render("נועה") and "{name}" not in item.render("נועה") for item in items
    )
    assert all("\n" not in item.text for item in items)


@pytest.mark.parametrize("name", ["", "Alice", "<נועה>", "נועה\u202e", "א" * 41, "---"])
def test_names_must_be_hebrew_and_safe(name):
    with pytest.raises(ValueError):
        validate_name(name)


@pytest.mark.parametrize(
    "change",
    [
        {"id": True},
        {"gender": "female"},
        {"text": "{name}, you are wonderful"},
        {"text": "{other}, את נהדרת ומיוחדת."},
        {"text": "{name!r}, את נהדרת ומיוחדת."},
        {"text": "{name}, את נהדרת.\nיש בך אור."},
    ],
)
def test_catalog_rejects_invalid_records_before_import(change):
    record = {"id": 1, "gender": "נקבה", "text": "{name}, את נהדרת ומיוחדת."}
    with pytest.raises(ValueError):
        load_catalog(json.dumps({**record, **change}, ensure_ascii=False))


def test_catalog_rejects_duplicate_ids_and_templates():
    record = {"id": 1, "gender": "נקבה", "text": "{name}, את נהדרת ומיוחדת."}
    line = json.dumps(record, ensure_ascii=False)
    for second in [record, {**record, "id": 2}]:
        with pytest.raises(ValueError):
            load_catalog(line + "\n" + json.dumps(second, ensure_ascii=False))


async def test_seed_is_idempotent_and_preserves_manual_updates(repository):
    assert await repository.count(Gender.FEMALE) == 500
    assert await repository.import_catalog(bundled_catalog()) == 0
    changed = Compliment(1, Gender.FEMALE, "{name}, יש בך אור חדש ומיוחד.")
    assert await repository.import_catalog([changed], update=True) == 1
    await repository.import_catalog(bundled_catalog())
    async with repository.store.db.execute("SELECT text FROM compliments WHERE id=1") as cursor:
        assert (await cursor.fetchone())[0] == changed.text


async def test_all_500_draws_unique_then_cycle_avoids_last_and_survives_restart(tmp_path):
    store = Store(str(tmp_path / "compliments.db"))
    await store.open()
    repo = ComplimentRepository(store)
    await repo.import_catalog(bundled_catalog())
    await repo.save_profile(Profile(17, "נועה", Gender.FEMALE))
    draws = [await repo.draw(17) for _ in range(500)]
    assert all(item is not None for item in draws)
    identities = [item[1].id for item in draws if item is not None]
    assert len(set(identities)) == 500
    await store.close()
    await store.open()
    repo = ComplimentRepository(store)
    next_item = await repo.draw(17)
    assert next_item is not None
    assert next_item[1].id != identities[-1]
    assert next_item[0].name == "נועה"
    await store.close()


async def test_concurrent_draws_and_user_histories_are_independent(repository):
    for chat_id in [17, 18]:
        await repository.save_profile(Profile(chat_id, "נועה", Gender.FEMALE))
    results = await asyncio.gather(*(repository.draw(17) for _ in range(20)))
    assert len({item[1].id for item in results}) == 20
    assert await repository.draw(18)
    async with repository.store.db.execute(
        "SELECT count(*) FROM compliment_history WHERE chat_id=18"
    ) as cursor:
        assert (await cursor.fetchone())[0] == 1


async def test_missing_gender_never_receives_female_wording(repository):
    await repository.save_profile(Profile(17, "נועם", Gender.MALE))
    assert await repository.draw(17) is None
    await repository.import_catalog(
        [Compliment(501, Gender.MALE, "{name}, אתה קשוב ומלא באכפתיות.")]
    )
    result = await repository.draw(17)
    assert result[1].gender == Gender.MALE
    assert "אתה" in result[1].render(result[0].name)


async def test_private_first_use_name_gender_and_settings(repository, now):
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = ComplimentController(repository, bot, lambda: now)
    await controller.compliment(event(), context())
    assert (await repository.pending(17))[0] == "name"
    await controller.text(event(text="נועה"), context())
    assert await repository.pending(17) == ("gender", "נועה")
    await controller.callback(event(data="cmp:gender:נקבה"), context())
    profile = await repository.profile(17)
    assert profile.name == "נועה" and profile.gender == Gender.FEMALE and not profile.enabled
    assert await repository.pending(17) is None
    assert any("💝 נועה," in call.args[1] for call in bot.send_message.call_args_list)
    assert await controller.settings(event(), context("name", "רוני"))
    assert await controller.settings(event(), context("compliment_time", "10:15"))
    assert await controller.settings(event(), context("compliments", "on"))
    profile = await repository.profile(17)
    assert (profile.name, profile.post_time, profile.enabled) == ("רוני", "10:15", True)
    assert await repository.profile(18) is None
    await controller.unsubscribe(17)
    assert not (await repository.profile(17)).enabled


async def test_onboarding_survives_restart_and_invalid_names_are_rejected(tmp_path):
    store = Store(str(tmp_path / "setup.db"))
    await store.open()
    repo = ComplimentRepository(store)
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = ComplimentController(repo, bot)
    await controller.begin(17)
    await controller.text(event(text="Alice"), context())
    assert await repo.pending(17) == ("name", None)
    await controller.text(event(text="נועה"), context())
    await store.close()
    await store.open()
    repo = ComplimentRepository(store)
    assert await repo.pending(17) == ("gender", "נועה")
    await store.close()


async def test_group_commands_callbacks_and_text_cannot_store_a_profile(repository, now):
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = ComplimentController(repository, bot, lambda: now)
    group = event(-17, text="נועה", kind="supergroup", data="cmp:gender:נקבה")
    await controller.compliment(group, context())
    await controller.callback(group, context())
    await controller.text(group, context())
    await controller.settings(group, context("name", "נועה"))
    assert await repository.profile(-17) is None
    assert await repository.pending(-17) is None


async def test_gender_changes_disable_daily_until_explicitly_enabled(repository, now):
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = ComplimentController(repository, bot, lambda: now)
    await repository.save_profile(Profile(17, "נועה", Gender.FEMALE, enabled=True))
    await controller.settings(event(), context("gender", "זכר"))
    assert (await repository.profile(17)).gender == Gender.MALE
    assert not (await repository.profile(17)).enabled
    await controller.settings(event(), context("compliments", "on"))
    assert not (await repository.profile(17)).enabled
    await controller.callback(event(data="cmp:gender:נקבה"), context())
    await controller.callback(event(data="cmp:toggle"), context())
    assert (await repository.profile(17)).enabled


async def test_daily_schedule_skips_startup_missed_minutes_and_duplicate_ticks(repository, now):
    now = now.replace(hour=8, minute=59)
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = ComplimentController(repository, bot, lambda: now)
    await repository.save_profile(Profile(17, "נועה", Gender.FEMALE, enabled=True))
    scheduler = Scheduler(
        repository.store,
        HoroscopeService(repository.store, []),
        AsyncMock(),
        lambda: now,
        extra_tick=controller.tick,
    )
    await scheduler.tick()
    bot.send_message.assert_not_awaited()
    now += timedelta(minutes=1)
    await scheduler.tick()
    await scheduler.tick()
    assert bot.send_message.await_count == 1
    restarted = Scheduler(
        repository.store,
        HoroscopeService(repository.store, []),
        AsyncMock(),
        lambda: now,
        extra_tick=controller.tick,
    )
    now += timedelta(seconds=1)
    await restarted.tick()
    assert bot.send_message.await_count == 1
    now += timedelta(days=1, minutes=1)
    await scheduler.tick()
    assert bot.send_message.await_count == 1


async def test_forget_clears_profile_name_history_setup_and_delivery_claims(repository, now):
    await repository.save_profile(Profile(17, "נועה", Gender.FEMALE, enabled=True))
    await repository.draw(17)
    await repository.setup(17, "name")
    await repository.claim_delivery(17, now.date())
    await repository.store.forget_group(17)
    assert await repository.profile(17) is None
    assert await repository.pending(17) is None
    for table in ["compliment_history", "compliment_deliveries"]:
        async with repository.store.db.execute(f"SELECT count(*) FROM {table}") as cursor:
            assert (await cursor.fetchone())[0] == 0
    assert await repository.count(Gender.FEMALE) == 500


async def test_import_rolls_back_all_records_when_existing_text_conflicts(repository):
    import sqlite3

    original = bundled_catalog()
    with pytest.raises(sqlite3.IntegrityError):
        await repository.import_catalog(
            [
                Compliment(1, Gender.FEMALE, "{name}, יש בך שמחה רעננה ומיוחדת."),
                Compliment(501, Gender.FEMALE, original[1].text),
            ],
            update=True,
        )
    async with repository.store.db.execute("SELECT text FROM compliments WHERE id=1") as cursor:
        assert (await cursor.fetchone())[0] == original[0].text
    assert await repository.count(Gender.FEMALE) == 500
