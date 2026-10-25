"""Composition root: one process, one polling client, one persistent database."""

import asyncio
import logging

import httpx
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
)

from haverbot.config import Config
from haverbot.providers.base import BROWSER_HEADERS, HttpFetcher
from haverbot.providers.israelhayom import IsraelHayomProvider
from haverbot.providers.mako import MakoProvider
from haverbot.providers.walla import WallaProvider
from haverbot.scheduler import Scheduler
from haverbot.service import HoroscopeService
from haverbot.storage import Store
from haverbot.telegram_app import TelegramController


def main() -> None:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # These libraries may log token-bearing URLs or Telegram payloads at INFO/DEBUG.
    for name in ("httpx", "httpcore", "telegram"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    store = Store(config.database)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=10),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=4),
        headers=BROWSER_HEADERS,
    )
    fetcher = HttpFetcher(client)
    service = HoroscopeService(
        store, [MakoProvider(fetcher), IsraelHayomProvider(fetcher), WallaProvider(fetcher)]
    )
    worker: asyncio.Task[None] | None = None

    async def start(application: Application) -> None:
        nonlocal worker
        await store.open()
        await application.bot.set_my_commands(
            [
                BotCommand("horoscope", "תחזית למזלות הקבוצה או למזל בודד"),
                BotCommand("sign", "תחזית מלאה למזל בודד"),
                BotCommand("settings", "הגדרות הקבוצה למנהלים"),
                BotCommand("unsubscribe", "ביטול הפרסום היומי"),
                BotCommand("help", "עזרה"),
                BotCommand("forget", "מחיקת הגדרות הקבוצה"),
            ]
        )
        worker = asyncio.create_task(Scheduler(store, service, controller.scheduled).run())

    async def stop(application: Application) -> None:
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await service.close()

    async def shutdown(application: Application) -> None:
        await client.aclose()
        await store.close()

    application = (
        Application.builder()
        .token(config.token)
        .concurrent_updates(False)
        .post_init(start)
        .post_stop(stop)
        .post_shutdown(shutdown)
        .build()
    )
    controller = TelegramController(store, service, application.bot)
    for name, handler in (
        ("start", controller.help),
        ("help", controller.help),
        ("horoscope", controller.horoscope),
        ("sign", controller.sign),
        ("settings", controller.settings),
        ("unsubscribe", controller.unsubscribe),
        ("forget", controller.forget),
    ):
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(CallbackQueryHandler(controller.callback, pattern=r"^(full|cfg):"))
    application.add_handler(
        ChatMemberHandler(controller.membership, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    application.add_error_handler(controller.error)
    # Old queued commands are discarded on restart, consistent with no catch-up scheduling.
    try:
        application.run_polling(
            allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER],
            drop_pending_updates=True,
        )
    except Exception as exc:
        logging.getLogger(__name__).error("bot_stopped=%s", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
