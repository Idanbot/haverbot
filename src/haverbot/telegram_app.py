"""Telegram transport and command handlers. Domain/services contain no Telegram objects."""

import asyncio
import logging
from dataclasses import replace
from datetime import date, datetime
from weakref import WeakValueDictionary

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from haverbot.domain import PROVIDER_NAMES, GroupSettings, Sign, validate_minutes, validate_time
from haverbot.formatting import Message, bulletin, full_sign
from haverbot.service import UNAVAILABLE, HoroscopeService
from haverbot.storage import Store

logger = logging.getLogger(__name__)
Context = ContextTypes.DEFAULT_TYPE
HELP = """שלום! אני חברבוט 🔮 — הורוסקופ בעברית, בפרטי ובקבוצות.
/start או /help — עזרה
/horoscope — תחזית למזלות שבחרתם
/horoscope leo או /sign אריה — תחזית מלאה למזל אחד
/compliment — מחמאה אישית אקראית בעברית, בפרטי בלבד
/compliments או /settings compliments — שם, לשון פנייה ומחמאות יומיות
/settings — בחירת מקור, מזלות, שעה, אורך וזמני המתנה
/unsubscribe — ביטול הפרסום היומי
/forget — מחיקת השם, ההגדרות וזמני ההמתנה בצ׳אט

בפרטי אתם שולטים בהגדרות שלכם; בקבוצות רק מנהלים משנים הגדרות.
הוסיפו את הבוט כמנהל בקבוצה כדי לאפשר בדיקת הרשאות.
הפרסום היומי מתחיל כבוי. השעות לפי שעון ישראל.
המקורות נאספים פעם ביום. כל הבקשות והכפתורים נטענים מהמטמון.
אורך התחזית מותאם לתקציב התווים; כפתורים מציגים טקסט מלא כשצריך.
בהפעלה הראשונה בפרטי נבקש שם ולשון פנייה למחמאות. המחמאות מותאמות ללשון הפנייה שנבחרה.
מחמאות רומנטיות הן לבחירה: /settings romantic on. כלליות: /settings general on.
"""
SETTINGS_HELP = """שינוי ערכים באמצעות פקודות:
/settings time 08:00
/settings cooldown 15
/settings single_cooldown 1
/settings words auto
/settings signs all
/settings signs אריה,טלה
/settings provider mako
/settings enabled on
/settings on_demand off
/settings fallback on

זמני ההמתנה בדקות: 1–1440. מילים למזל: auto להתאמה לתקציב התווים, או 5–100 להגבלה ידנית.
כפתורי התחזית המלאה משתמשים בהמתנה המשותפת לכל המזלות הבודדים.
"""


def keyboard(message: Message) -> InlineKeyboardMarkup | None:
    if not message.buttons:
        return None
    buttons = [InlineKeyboardButton(b.label, callback_data=b.data) for b in message.buttons]
    return InlineKeyboardMarkup([buttons[i : i + 2] for i in range(0, len(buttons), 2)])


class TelegramController:
    def __init__(self, store: Store, service: HoroscopeService, bot: Bot) -> None:
        self.store = store
        self.service = service
        self.bot = bot
        self._settings_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    async def send(self, chat_id: int, messages: list[Message]) -> None:
        for message in messages:
            await self.bot.send_message(
                chat_id=chat_id,
                text=message.text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard(message),
                disable_web_page_preview=True,
            )

    async def help(self, update: Update, context: Context) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(HELP)

    async def _chat(self, update: Update) -> int | None:
        chat = update.effective_chat
        if chat and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP, ChatType.PRIVATE}:
            return chat.id
        if update.effective_message:
            await update.effective_message.reply_text(
                "הפקודה זמינה בקבוצה או בשיחה פרטית.\n" + HELP
            )
        return None

    async def _admin(self, update: Update) -> bool:
        if update.effective_chat is None or update.effective_user is None:
            return False
        if update.effective_chat.type == ChatType.PRIVATE:
            return True
        try:
            member = await self.bot.get_chat_member(
                update.effective_chat.id, update.effective_user.id
            )
            allowed = member.status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}
        except TelegramError:
            allowed = False
        if not allowed and update.effective_message:
            await update.effective_message.reply_text(
                "רק מנהלי הקבוצה רשאים לשנות הגדרות. אם אתם מנהלים, ודאו שגם לבוט יש הרשאת מנהל."
            )
        return allowed

    async def horoscope(self, update: Update, context: Context) -> None:
        chat_id = await self._chat(update)
        if chat_id is None:
            return
        try:
            args = context.args or []
            if len(args) > 1:
                raise ValueError("שימוש: /horoscope או /horoscope אריה")
            sign = Sign.parse(args[0]) if args else None
        except ValueError as exc:
            await self.send(chat_id, [Message(str(exc))])
            return
        await self._request(chat_id, sign=sign)

    async def sign(self, update: Update, context: Context) -> None:
        if not context.args:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "בחרו מזל, למשל /sign אריה\n" + " · ".join(s.hebrew for s in Sign)
                )
            return
        await self.horoscope(update, context)

    async def _request(
        self,
        chat_id: int,
        *,
        sign: Sign | None = None,
        exact: tuple[str, date, Sign] | None = None,
    ) -> None:
        settings = await self.store.settings(chat_id)
        result = await self.service.request(settings, sign, exact=exact)
        if result.error:
            await self.send(chat_id, [Message(result.error)])
            return
        sent_any = False
        try:
            messages = (
                full_sign(result.forecasts[0])
                if sign is not None or exact is not None
                else [bulletin(list(result.forecasts), settings)]
            )
            # Release a reservation only if no part reached Telegram. A partial send counts.
            for message in messages:
                await self.send(chat_id, [message])
                sent_any = True
        except Exception:
            if not sent_any and result.reserved_at:
                await self.store.release_cooldown(chat_id, result.bucket, result.reserved_at)
            raise

    async def scheduled(self, settings: GroupSettings, now: datetime) -> None:
        forecasts = await self.service.resolve(settings, now.date(), settings.signs)
        message = bulletin(list(forecasts), settings) if forecasts else Message(UNAVAILABLE)
        try:
            await self.send(settings.chat_id, [message])
        except Forbidden:
            # Removed bots cannot maintain a useful subscription. Do not retain chat identifiers.
            await self.store.forget_group(settings.chat_id)

    @staticmethod
    def settings_view(settings: GroupSettings) -> tuple[str, InlineKeyboardMarkup]:
        def flag(value: bool) -> str:
            return "✅" if value else "❌"

        text = (
            "⚙️ <b>הגדרות הורוסקופ לצ׳אט</b>\n"
            f"פרסום יומי: {flag(settings.enabled)}\n"
            f"שעה בישראל: <b>{settings.post_time}</b>\n"
            f"מקור מועדף: <b>{PROVIDER_NAMES[settings.provider]}</b>\n"
            f"גיבוי אוטומטי: {flag(settings.fallback)}\n"
            f"לפי דרישה: {flag(settings.on_demand)}\n"
            f"המתנה לבקשת תחזית למזלות הנבחרים: {settings.bulletin_minutes} דקות\n"
            f"המתנה משותפת למזלות בודדים: {settings.single_minutes} דקות\n"
            + (
                "אורך התחזית: אוטומטי לפי תקציב התווים\n"
                if not settings.max_words
                else f"עד {settings.max_words} מילים לכל מזל\n"
            )
            + f"מזלות: {', '.join(s.hebrew for s in settings.signs)}\n\n"
            + SETTINGS_HELP
        )
        rows = [
            [
                InlineKeyboardButton(
                    f"{flag(settings.enabled)} פרסום יומי", callback_data="cfg:enabled:toggle"
                ),
                InlineKeyboardButton(
                    f"{flag(settings.on_demand)} לפי דרישה", callback_data="cfg:on_demand:toggle"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"{flag(settings.fallback)} גיבוי אוטומטי", callback_data="cfg:fallback:toggle"
                )
            ],
            [
                InlineKeyboardButton(
                    ("✅ " if settings.provider == provider else "") + label,
                    callback_data=f"cfg:provider:{provider}",
                )
                for provider, label in PROVIDER_NAMES.items()
            ],
        ]
        signs = [
            InlineKeyboardButton(
                f"{flag(s in settings.signs)} {s.emoji} {s.hebrew}",
                callback_data=f"cfg:sign:{s.value}",
            )
            for s in Sign
        ]
        rows.extend(signs[i : i + 3] for i in range(0, len(signs), 3))
        rows.append([InlineKeyboardButton("כל המזלות", callback_data="cfg:signs:all")])
        if settings.chat_id > 0:
            rows.append([InlineKeyboardButton("💝 הגדרות מחמאות", callback_data="cmp:settings")])
        return text, InlineKeyboardMarkup(rows)

    @staticmethod
    def apply_setting(settings: GroupSettings, key: str, value: str) -> GroupSettings:
        if key in {"enabled", "on_demand", "fallback"}:
            if value not in {"on", "off", "toggle"}:
                raise ValueError("יש לבחור on או off.")
            current = getattr(settings, key)
            return replace(settings, **{key: not current if value == "toggle" else value == "on"})
        if key == "time":
            return replace(settings, post_time=validate_time(value))
        if key == "provider":
            return replace(settings, provider=value)
        if key == "words" and value == "auto":
            return replace(settings, max_words=0)
        if key in {"cooldown", "single_cooldown", "words"}:
            try:
                number = int(value)
            except ValueError:
                raise ValueError("יש להזין מספר שלם.") from None
            if key == "words":
                return replace(settings, max_words=number)
            field = "bulletin_minutes" if key == "cooldown" else "single_minutes"
            return replace(settings, **{field: validate_minutes(number)})
        if key == "signs":
            signs = set(Sign) if value == "all" else {Sign.parse(v) for v in value.split(",")}
            return replace(settings, signs=tuple(s for s in Sign if s in signs))
        if key == "sign":
            sign = Sign.parse(value)
            signs = set(settings.signs) ^ {sign}
            return replace(settings, signs=tuple(s for s in Sign if s in signs))
        raise ValueError("הגדרה לא מוכרת. השתמשו ב־/settings להצגת האפשרויות.")

    async def _change(self, chat_id: int, key: str, value: str) -> GroupSettings:
        lock = self._settings_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            settings = self.apply_setting(await self.store.settings(chat_id), key, value)
            await self.store.save_settings(settings)
        return settings

    async def settings(self, update: Update, context: Context) -> None:
        chat_id = await self._chat(update)
        if chat_id is None or not await self._admin(update):
            return
        args = context.args or []
        try:
            if args:
                if len(args) < 2:
                    raise ValueError("יש להזין שם הגדרה וערך.\n" + SETTINGS_HELP)
                settings = await self._change(chat_id, args[0], "".join(args[1:]))
            else:
                settings = await self.store.settings(chat_id)
                await self.store.save_settings(settings)
            text, markup = self.settings_view(settings)
            await self.bot.send_message(
                chat_id, text, parse_mode=ParseMode.HTML, reply_markup=markup
            )
        except ValueError as exc:
            await self.send(chat_id, [Message(str(exc))])

    async def unsubscribe(self, update: Update, context: Context) -> None:
        chat_id = await self._chat(update)
        if chat_id is not None and await self._admin(update):
            await self._change(chat_id, "enabled", "off")
            await self.send(
                chat_id, [Message("הפרסום היומי בוטל. בקשות לפי דרישה נשארות לפי ההגדרות.")]
            )

    async def forget(self, update: Update, context: Context) -> None:
        chat_id = await self._chat(update)
        if chat_id is not None and await self._admin(update):
            await self.store.forget_group(chat_id)
            await self.send(
                chat_id, [Message("ההגדרות וזמני ההמתנה בצ׳אט נמחקו. הפרסום היומי כבוי.")]
            )

    async def callback(self, update: Update, context: Context) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        chat_id = await self._chat(update)
        if chat_id is None:
            return
        data = query.data
        if not isinstance(data, str):
            return
        try:
            parts = data.split(":")
            if len(parts) == 4 and parts[0] == "full":
                _, provider, day, sign = parts
                if provider not in PROVIDER_NAMES:
                    raise ValueError("מקור לא מוכר.")
                await self._request(chat_id, exact=(provider, date.fromisoformat(day), Sign(sign)))
            elif len(parts) == 3 and parts[0] == "cfg" and await self._admin(update):
                settings = await self._change(chat_id, parts[1], parts[2])
                text, markup = self.settings_view(settings)
                try:
                    await query.edit_message_text(
                        text, parse_mode=ParseMode.HTML, reply_markup=markup
                    )
                except BadRequest as exc:
                    if "message is not modified" not in str(exc).lower():
                        raise
        except ValueError:
            await self.send(chat_id, [Message("הבחירה אינה תקינה. ודאו שנבחר לפחות מזל אחד.")])

    async def membership(self, update: Update, context: Context) -> None:
        event = update.my_chat_member
        if event is None:
            return
        if event.new_chat_member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.BANNED}:
            await self.store.forget_group(event.chat.id)

    async def error(self, update: object, context: Context) -> None:
        # Exception messages from HTTP libraries can expose Telegram tokens and message bodies.
        logger.error("telegram_handler_failed=%s", type(context.error).__name__)
