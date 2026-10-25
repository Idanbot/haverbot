"""Import validated Hebrew JSONL into a local SQLite database; never contacts Telegram."""

import argparse
import asyncio
import os
from pathlib import Path

from haverbot.compliments.catalog import catalog_file
from haverbot.compliments.repository import ComplimentRepository
from haverbot.storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--database", default=os.getenv("HAVERBOT_DATABASE", "data/haverbot.sqlite3")
    )
    parser.add_argument(
        "--update", action="store_true", help="Update existing ids (default: insert new ids)"
    )
    args = parser.parse_args()
    try:
        items = catalog_file(args.file)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    async def run() -> None:
        store = Store(args.database)
        await store.open()
        try:
            count = await ComplimentRepository(store).import_catalog(items, update=args.update)
            print(f"Imported {count} compliments from {len(items)} validated records.")
        finally:
            await store.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
