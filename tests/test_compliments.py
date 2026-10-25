import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from haverbot.compliments.catalog import bundled_catalog, load_catalog
from haverbot.compliments.domain import Category, Compliment, Gender, Profile, validate_name
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


def test_catalog_has_general_and_romantic_compliments_for_both_genders():
    items = bundled_catalog()
    assert len(items) == 755
    assert {item.id for item in items} == set(range(1, 756))
    assert len({item.text for item in items}) == 755
    assert sum(item.gender == Gender.FEMALE for item in items) == 700
    assert sum(item.gender == Gender.MALE for item in items) == 55
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
    assert await repository.count(Gender.FEMALE) == 700
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


async def test_missing_gender_never_receives_female_wording(store):
    repository = ComplimentRepository(store)
    await repository.import_catalog(
        [item for item in bundled_catalog() if item.gender == Gender.FEMALE]
    )
    await repository.save_profile(Profile(17, "נועם", Gender.MALE))
    assert await repository.draw(17) is None
    await repository.import_catalog(
        [Compliment(501, Gender.MALE, "{name}, אתה קשוב ומלא באכפתיות.")]
    )
    result = await repository.draw(17)
    assert result is not None
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
    assert (await repository.profile(17)).enabled
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
    assert await repository.count(Gender.FEMALE) == 700


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
    assert await repository.count(Gender.FEMALE) == 700


async def test_male_draws_use_all_five_templates_then_avoid_immediate_repeat(repository):
    await repository.save_profile(Profile(17, "נועם", Gender.MALE))
    draws = [await repository.draw(17) for _ in range(6)]
    assert all(item is not None for item in draws)
    records = [item[1] for item in draws if item is not None]
    assert {item.id for item in records[:5]} == set(range(501, 506))
    assert records[5].id != records[4].id
    assert all(item.gender == Gender.MALE and "אתה" in item.render("נועם") for item in records)


def test_catalog_categories_and_legacy_import_defaults():
    items = bundled_catalog()
    for gender, expected in [(Gender.FEMALE, 200), (Gender.MALE, 50)]:
        romantic = [
            item for item in items if item.gender == gender and item.category == Category.ROMANTIC
        ]
        assert len(romantic) == expected
    legacy = json.dumps({"id": 1, "gender": "נקבה", "text": "{name}, את מביאה איתך אור נעים."})
    assert load_catalog(legacy)[0].category == Category.GENERAL
    for category in ["רומנטית", "כללית"]:
        record = {**json.loads(legacy), "category": category}
        assert load_catalog(json.dumps(record))[0].category == Category(category)
    with pytest.raises(ValueError):
        load_catalog(json.dumps({**json.loads(legacy), "category": "romantic"}))


async def test_romantic_only_draws_200_female_entries_and_preserves_general_history(repository):
    await repository.save_profile(Profile(17, "נועה", Gender.FEMALE))
    general_draw = await repository.draw(17)
    assert general_draw is not None and general_draw[1].category == Category.GENERAL
    profile = await repository.profile(17)
    assert profile is not None
    await repository.save_profile(replace(profile, general=False, romantic=True))
    results = [await repository.draw(17) for _ in range(201)]
    records = [result[1] for result in results if result is not None]
    assert len(records) == 201
    assert len({item.id for item in records[:200]}) == 200
    assert all(
        item.category == Category.ROMANTIC and item.gender == Gender.FEMALE for item in records
    )
    assert records[200].id != records[199].id
    async with repository.store.db.execute(
        "SELECT count(*) FROM compliment_history WHERE chat_id=17 AND compliment_id=?",
        (general_draw[1].id,),
    ) as cursor:
        assert (await cursor.fetchone())[0] == 1


async def test_both_categories_draw_all_55_male_entries_without_gender_leaks(repository):
    await repository.save_profile(Profile(17, "נועם", Gender.MALE, romantic=True))
    results = [await repository.draw(17) for _ in range(55)]
    records = [result[1] for result in results if result is not None]
    assert len({item.id for item in records}) == 55
    assert all(item.gender == Gender.MALE for item in records)
    assert sum(item.category == Category.GENERAL for item in records) == 5
    assert sum(item.category == Category.ROMANTIC for item in records) == 50


async def test_category_settings_buttons_and_commands_cannot_disable_every_category(
    repository, now
):
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = ComplimentController(repository, bot, lambda: now)
    await repository.save_profile(Profile(17, "נועה", Gender.FEMALE))
    await controller.settings(event(), context("general", "off"))
    profile = await repository.profile(17)
    assert profile and profile.general and not profile.romantic
    assert "לפחות" in bot.send_message.call_args.args[1]
    await controller.settings(event(), context("romantic", "on"))
    await controller.callback(event(data="cmp:category:general"), context())
    profile = await repository.profile(17)
    assert profile and not profile.general and profile.romantic
    await controller.callback(event(data="cmp:category:romantic"), context())
    assert (await repository.profile(17)) == profile
    assert "לפחות" in bot.send_message.call_args.args[1]
    await controller.settings(event(), context("general", "on"))
    await controller.settings(event(), context("romantic", "off"))
    profile = await repository.profile(17)
    assert profile and profile.general and not profile.romantic
    await controller.compliment(event(), context())
    expected = {
        "💝 " + item.render("נועה")
        for item in bundled_catalog()
        if item.gender == Gender.FEMALE and item.category == Category.GENERAL
    }
    assert bot.send_message.call_args.args[1] in expected


async def test_daily_compliments_respect_romantic_only_preference(repository, now):
    bot: Any = SimpleNamespace(send_message=AsyncMock())
    controller = ComplimentController(repository, bot, lambda: now)
    await repository.save_profile(
        Profile(
            17,
            "נועם",
            Gender.MALE,
            enabled=True,
            post_time=now.strftime("%H:%M"),
            general=False,
            romantic=True,
        )
    )
    await controller.tick(now)
    expected = {
        "💝 " + item.render("נועם")
        for item in bundled_catalog()
        if item.gender == Gender.MALE and item.category == Category.ROMANTIC
    }
    assert bot.send_message.call_args.args[1] in expected
    await controller.tick(now)
    assert bot.send_message.await_count == 1


async def test_category_migration_preserves_legacy_profiles_and_history(tmp_path):
    import sqlite3
    from contextlib import closing

    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE compliments(id INTEGER PRIMARY KEY, gender TEXT NOT NULL,
                text TEXT NOT NULL, UNIQUE(gender,text));
            CREATE TABLE compliment_profiles(chat_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                gender TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                post_time TEXT NOT NULL DEFAULT '09:00', last_id INTEGER);
            CREATE TABLE compliment_history(chat_id INTEGER NOT NULL,
                compliment_id INTEGER NOT NULL, PRIMARY KEY(chat_id,compliment_id));
            INSERT INTO compliments VALUES(1,'נקבה','{name}, את מביאה איתך אור נעים.');
            INSERT INTO compliment_profiles VALUES(17,'נועה','נקבה',1,'10:15',1);
            INSERT INTO compliment_history VALUES(17,1);
            PRAGMA user_version=4;
        """)
    store = Store(str(path))
    await store.open()
    repository = ComplimentRepository(store)
    profile = await repository.profile(17)
    assert profile == Profile(17, "נועה", Gender.FEMALE, enabled=True, post_time="10:15", last_id=1)
    assert profile and profile.general and not profile.romantic
    assert await repository.count(Gender.FEMALE, (Category.GENERAL,)) == 1
    async with store.db.execute("SELECT count(*) FROM compliment_history") as cursor:
        history = await cursor.fetchone()
        assert history and history[0] == 1
    await repository.save_profile(replace(profile, general=False, romantic=True))
    await store.close()
    await store.open()
    repository = ComplimentRepository(store)
    profile = await repository.profile(17)
    assert profile and not profile.general and profile.romantic
    assert profile.enabled and profile.post_time == "10:15" and profile.last_id == 1
    await store.close()
