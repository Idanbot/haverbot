"""SQLite profiles, onboarding, catalog import and random draws without replacement."""

import asyncio
from datetime import date
from typing import TYPE_CHECKING

from haverbot.compliments.domain import Compliment, Gender, Profile

if TYPE_CHECKING:
    from haverbot.storage import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS compliments (
    id INTEGER PRIMARY KEY, gender TEXT NOT NULL, text TEXT NOT NULL, UNIQUE(gender,text)
);
CREATE INDEX IF NOT EXISTS compliments_gender ON compliments(gender);
CREATE TABLE IF NOT EXISTS compliment_profiles (
    chat_id INTEGER PRIMARY KEY CHECK(chat_id>0), name TEXT NOT NULL,
    gender TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
    post_time TEXT NOT NULL DEFAULT '09:00', last_id INTEGER
);
CREATE TABLE IF NOT EXISTS compliment_setup (
    chat_id INTEGER PRIMARY KEY CHECK(chat_id>0), step TEXT NOT NULL, name TEXT
);
CREATE TABLE IF NOT EXISTS compliment_history (
    chat_id INTEGER NOT NULL REFERENCES compliment_profiles(chat_id) ON DELETE CASCADE,
    compliment_id INTEGER NOT NULL REFERENCES compliments(id) ON DELETE CASCADE,
    PRIMARY KEY(chat_id, compliment_id)
);
CREATE TABLE IF NOT EXISTS compliment_deliveries (
    chat_id INTEGER NOT NULL REFERENCES compliment_profiles(chat_id) ON DELETE CASCADE,
    day TEXT NOT NULL, PRIMARY KEY(chat_id, day)
);
"""


class ComplimentRepository:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._draw_lock = asyncio.Lock()

    async def import_catalog(self, items: list[Compliment], *, update: bool = False) -> int:
        # Importers validate the entire file before starting this transaction.
        sql = "INSERT OR IGNORE INTO compliments VALUES (?,?,?)"
        if update:
            sql = (
                "INSERT INTO compliments VALUES (?,?,?) ON CONFLICT(id) "
                "DO UPDATE SET gender=excluded.gender, text=excluded.text"
            )
        await self.store.db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self.store.db.executemany(
                sql, [(item.id, item.gender.value, item.text) for item in items]
            )
            count = cursor.rowcount
            await cursor.close()
            await self.store.db.commit()
            return count
        except BaseException:
            await self.store.db.rollback()
            raise

    async def count(self, gender: Gender) -> int:
        async with self.store.db.execute(
            "SELECT count(*) FROM compliments WHERE gender=?", (gender.value,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def profile(self, chat_id: int) -> Profile | None:
        async with self.store.db.execute(
            "SELECT chat_id,name,gender,enabled,post_time,last_id FROM compliment_profiles "
            "WHERE chat_id=?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return (
            Profile(row[0], row[1], Gender(row[2]), bool(row[3]), row[4], row[5]) if row else None
        )

    async def save_profile(self, profile: Profile) -> None:
        await self.store.db.execute(
            "INSERT INTO compliment_profiles VALUES (?,?,?,?,?,?) ON CONFLICT(chat_id) "
            "DO UPDATE SET name=excluded.name,gender=excluded.gender,enabled=excluded.enabled,"
            "post_time=excluded.post_time",
            (
                profile.chat_id,
                profile.name,
                profile.gender.value,
                profile.enabled,
                profile.post_time,
                profile.last_id,
            ),
        )

    async def setup(self, chat_id: int, step: str, name: str | None = None) -> None:
        await self.store.db.execute(
            "INSERT INTO compliment_setup VALUES (?,?,?) ON CONFLICT(chat_id) "
            "DO UPDATE SET step=excluded.step,name=excluded.name",
            (chat_id, step, name),
        )

    async def pending(self, chat_id: int) -> tuple[str, str | None] | None:
        async with self.store.db.execute(
            "SELECT step,name FROM compliment_setup WHERE chat_id=?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return (str(row[0]), row[1]) if row else None

    async def clear_setup(self, chat_id: int) -> None:
        await self.store.db.execute("DELETE FROM compliment_setup WHERE chat_id=?", (chat_id,))

    async def draw(self, chat_id: int) -> tuple[Profile, Compliment] | None:
        async with self._draw_lock:
            profile = await self.profile(chat_id)
            if profile is None:
                return None
            # One atomic insert/select reserves a random unused id even when called concurrently.
            sql = """
                INSERT INTO compliment_history(chat_id,compliment_id)
                SELECT ?,id FROM compliments WHERE gender=?
                    AND (? IS NULL OR id!=?)
                    AND NOT EXISTS (SELECT 1 FROM compliment_history
                                    WHERE chat_id=? AND compliment_id=compliments.id)
                ORDER BY random() LIMIT 1 RETURNING compliment_id
            """

            async def reserve(exclude: int | None):
                async with self.store.db.execute(
                    sql,
                    (chat_id, profile.gender.value, exclude, exclude, chat_id),
                ) as cursor:
                    return await cursor.fetchone()

            row = await reserve(profile.last_id)
            if row is None:
                await self.store.db.execute(
                    "DELETE FROM compliment_history WHERE chat_id=? AND compliment_id IN "
                    "(SELECT id FROM compliments WHERE gender=?)",
                    (chat_id, profile.gender.value),
                )
                row = await reserve(profile.last_id)
                if row is None:
                    row = await reserve(None)  # A one-record custom catalog must still work.
            if row is None:
                return None
            identity = row[0]
            async with self.store.db.execute(
                "SELECT text FROM compliments WHERE id=?",
                (identity,),
            ) as cursor:
                item = await cursor.fetchone()
            await self.store.db.execute(
                "UPDATE compliment_profiles SET last_id=? WHERE chat_id=?",
                (identity, chat_id),
            )
            return (profile, Compliment(identity, profile.gender, item[0])) if item else None

    async def due_profiles(self, time: str) -> list[Profile]:
        async with self.store.db.execute(
            "SELECT chat_id FROM compliment_profiles WHERE enabled=1 AND post_time=?",
            (time,),
        ) as cursor:
            rows = await cursor.fetchall()
        profiles = [await self.profile(row[0]) for row in rows]
        return [profile for profile in profiles if profile is not None]

    async def claim_delivery(self, chat_id: int, day: date) -> bool:
        async with self.store.db.execute(
            "INSERT OR IGNORE INTO compliment_deliveries VALUES (?,?)",
            (chat_id, day.isoformat()),
        ) as cursor:
            return cursor.rowcount == 1
