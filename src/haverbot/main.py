"""Composition root: one process, one polling client, one persistent database."""

import asyncio
import logging

import httpx
from telegram import BotCommand, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from haverbot.compliments.catalog import bundled_catalog
from haverbot.compliments.repository import ComplimentRepository
from haverbot.compliments.telegram import ComplimentController
from haverbot.config import Config
from haverbot.providers.base import BROWSER_HEADERS, HttpFetcher
from haverbot.providers.israelhayom import IsraelHayomProvider
from haverbot.providers.mako import MakoProvider
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
    compliment_repository = ComplimentRepository(store)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=10),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=4),
        headers=BROWSER_HEADERS,
    )
    fetcher = HttpFetcher(client)
    service = HoroscopeService(
        store, [MakoProvider(fetcher), IsraelHayomProvider(fetcher)], ingest_time=config.ingest_time
    )
    worker: asyncio.Task[None] | None = None

    async def start(application: Application) -> None:
        nonlocal worker
        await store.open()
        await compliment_repository.import_catalog(bundled_catalog())
        await application.bot.set_my_commands(
            [
                BotCommand("horoscope", "תחזית מהמטמון למזלות שנבחרו או למזל אחד"),
                BotCommand("start", "התחלה ועזרה — בפרטי או בקבוצה"),
                BotCommand("sign", "תחזית מלאה למזל בודד"),
                BotCommand("compliment", "מחמאה אישית אקראית בעברית — בפרטי בלבד"),
                BotCommand("compliments", "שם, לשון פנייה ומחמאות יומיות — בפרטי"),
                BotCommand("settings", "הגדרות אישיות או הגדרות קבוצה למנהלים"),
                BotCommand("unsubscribe", "ביטול הפרסום היומי"),
                BotCommand("help", "עזרה"),
                BotCommand("forget", "מחיקת ההגדרות וזמני ההמתנה בצ׳אט"),
            ]
        )
        worker = asyncio.create_task(
            Scheduler(store, service, controller.scheduled, extra_tick=compliments.tick).run()
        )

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
    compliments = ComplimentController(compliment_repository, application.bot)

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await controller.help(update, context)
        await compliments.start(update, context)

    async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await compliments.settings(update, context):
            await controller.settings(update, context)

    async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
            await compliments.unsubscribe(update.effective_chat.id)
        await controller.unsubscribe(update, context)

    for name, handler in (
        ("start", start_command),
        ("help", controller.help),
        ("horoscope", controller.horoscope),
        ("sign", controller.sign),
        ("compliment", compliments.compliment),
        ("compliments", compliments.settings_command),
        ("settings", settings_command),
        ("unsubscribe", unsubscribe_command),
        ("forget", controller.forget),
    ):
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(CallbackQueryHandler(controller.callback, pattern=r"^(full|cfg):"))
    application.add_handler(CallbackQueryHandler(compliments.callback, pattern=r"^cmp:"))
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, compliments.text)
    )
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
