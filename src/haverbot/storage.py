"""SQLite persistence. Atomic claims serialize cooldowns and scheduled deliveries."""

import asyncio
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import aiosqlite

from haverbot.domain import Forecast, GroupSettings, Sign

Bucket = Literal["bulletin", "single"]


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store is not open")
        return self._db

    async def open(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path, isolation_level=None)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS groups (
                chat_id INTEGER PRIMARY KEY, settings TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS forecasts (
                day TEXT NOT NULL, provider TEXT NOT NULL, sign TEXT NOT NULL,
                body TEXT NOT NULL, source_url TEXT NOT NULL,
                PRIMARY KEY(day, provider, sign)
            );
            CREATE TABLE IF NOT EXISTS cooldowns (
                chat_id INTEGER NOT NULL, bucket TEXT NOT NULL, sent_at REAL NOT NULL,
                PRIMARY KEY(chat_id, bucket)
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                chat_id INTEGER NOT NULL, day TEXT NOT NULL,
                PRIMARY KEY(chat_id, day)
            );
            CREATE TABLE IF NOT EXISTS ingestions (
                provider TEXT NOT NULL, day TEXT NOT NULL,
                PRIMARY KEY(provider, day)
            );
            UPDATE groups SET settings=json_set(settings, '$.provider', 'mako')
            WHERE json_extract(settings, '$.provider')='walla';
            UPDATE groups SET settings=json_set(settings, '$.max_words', 0)
            WHERE json_extract(settings, '$.max_words')=40
                AND (SELECT user_version FROM pragma_user_version)<3;
            PRAGMA user_version=3;
        """)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def settings(self, chat_id: int) -> GroupSettings:
        async with self.db.execute(
            "SELECT settings FROM groups WHERE chat_id=?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return GroupSettings(chat_id)
        data = json.loads(row[0])
        data["signs"] = tuple(Sign(s) for s in data["signs"])
        return GroupSettings(**data)

    async def save_settings(self, settings: GroupSettings) -> None:
        await self.db.execute(
            "INSERT INTO groups VALUES (?,?) ON CONFLICT(chat_id) "
            "DO UPDATE SET settings=excluded.settings",
            (settings.chat_id, json.dumps(asdict(settings), ensure_ascii=False)),
        )

    async def ensure_settings(self, settings: GroupSettings) -> None:
        """Remember on-demand-only groups without overwriting an admin's saved settings."""
        await self.db.execute(
            "INSERT OR IGNORE INTO groups VALUES (?,?)",
            (settings.chat_id, json.dumps(asdict(settings), ensure_ascii=False)),
        )

    async def groups(self) -> list[GroupSettings]:
        async with self.db.execute("SELECT chat_id FROM groups") as cursor:
            ids = await cursor.fetchall()
        return [await self.settings(row[0]) for row in ids]

    async def put_forecasts(self, forecasts: list[Forecast]) -> None:
        # Immutable within a day: an old expansion button must reproduce its bulletin's text.
        await self.db.executemany(
            "INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?,?)",
            [
                (f.day.isoformat(), f.provider, f.sign.value, f.text, f.source_url)
                for f in forecasts
            ],
        )

    async def forecasts(self, provider: str, day: date) -> dict[Sign, Forecast]:
        async with self.db.execute(
            "SELECT sign, body, source_url FROM forecasts WHERE provider=? AND day=?",
            (provider, day.isoformat()),
        ) as cursor:
            rows = await cursor.fetchall()
        return {Sign(s): Forecast(provider, day, Sign(s), text, url) for s, text, url in rows}

    async def claim_cooldown(
        self, chat_id: int, bucket: Bucket, minutes: int, now: datetime
    ) -> int:
        """Return remaining seconds, or reserve this bucket and return zero."""
        async with self._lock:
            # A single conditional UPSERT stays atomic even across separate processes/connections.
            async with self.db.execute(
                "INSERT INTO cooldowns VALUES (?,?,?) ON CONFLICT(chat_id,bucket) "
                "DO UPDATE SET sent_at=excluded.sent_at "
                "WHERE cooldowns.sent_at<=? RETURNING sent_at",
                (chat_id, bucket, now.timestamp(), now.timestamp() - minutes * 60),
            ) as cursor:
                claimed = await cursor.fetchone()
            if claimed is not None:
                return 0
            async with self.db.execute(
                "SELECT sent_at FROM cooldowns WHERE chat_id=? AND bucket=?", (chat_id, bucket)
            ) as cursor:
                row = await cursor.fetchone()
            remaining = 0 if row is None else row[0] + minutes * 60 - now.timestamp()
            return max(1, int(remaining + 0.999))

    async def release_cooldown(self, chat_id: int, bucket: Bucket, now: datetime) -> None:
        await self.db.execute(
            "DELETE FROM cooldowns WHERE chat_id=? AND bucket=? AND sent_at=?",
            (chat_id, bucket, now.timestamp()),
        )

    async def claim_delivery(self, chat_id: int, day: date) -> bool:
        async with self.db.execute(
            "INSERT OR IGNORE INTO deliveries VALUES (?,?)", (chat_id, day.isoformat())
        ) as cursor:
            return cursor.rowcount == 1

    async def claim_ingestion(self, provider: str, day: date) -> bool:
        """Reserve the daily attempt before network I/O, including across restarts."""
        async with self.db.execute(
            "INSERT OR IGNORE INTO ingestions VALUES (?,?)", (provider, day.isoformat())
        ) as cursor:
            return cursor.rowcount == 1

    async def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=6)).isoformat()
        await self.db.execute(
            "DELETE FROM forecasts WHERE day<? OR day>?", (cutoff, today.isoformat())
        )
        await self.db.execute("DELETE FROM deliveries WHERE day<?", (cutoff,))
        await self.db.execute("DELETE FROM ingestions WHERE day<?", (cutoff,))
        # Cooldowns cannot remain active for longer than 24h.
        from haverbot.domain import ISRAEL

        await self.db.execute(
            "DELETE FROM cooldowns WHERE sent_at<?",
            (datetime.combine(today - timedelta(days=1), datetime.min.time(), ISRAEL).timestamp(),),
        )

    async def forget_group(self, chat_id: int) -> None:
        for table in ("groups", "cooldowns", "deliveries"):
            await self.db.execute(f"DELETE FROM {table} WHERE chat_id=?", (chat_id,))
