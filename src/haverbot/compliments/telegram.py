"""Hebrew-only private onboarding, settings, random requests and daily delivery."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from html import escape

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from haverbot.compliments.domain import Gender, Profile, validate_name
from haverbot.compliments.repository import ComplimentRepository
from haverbot.domain import israel_now, validate_time

logger = logging.getLogger(__name__)
Context = ContextTypes.DEFAULT_TYPE
NO_CONTENT = "לא נמצאו מחמאות בלשון הפנייה שנבחרה. אפשר לבדוק את הגדרות המחמאות."
SETTINGS_KEYS = {"compliments", "name", "gender", "compliment_time"}


def gender_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(gender.value, callback_data=f"cmp:gender:{gender.value}")
                for gender in Gender
            ]
        ]
    )


class ComplimentController:
    def __init__(
        self,
        repository: ComplimentRepository,
        bot: Bot,
        clock: Callable[[], datetime] = israel_now,
    ) -> None:
        self.repository = repository
        self.bot = bot
        self.clock = clock

    async def private_chat(self, update: Update) -> int | None:
        if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
            return update.effective_chat.id
        if update.effective_message:
            await update.effective_message.reply_text("מחמאות זמינות בשיחה פרטית עם הבוט בלבד.")
        return None

    async def say(
        self, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None
    ) -> None:
        await self.bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )

    async def begin(self, chat_id: int) -> None:
        await self.repository.setup(chat_id, "name")
        await self.say(chat_id, "💝 מה השם שיופיע במחמאות? יש לשלוח שם בעברית.")

    async def start(self, update: Update, context: Context) -> None:
        if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
            if await self.repository.profile(update.effective_chat.id) is None:
                await self.begin(update.effective_chat.id)

    async def compliment(self, update: Update, context: Context) -> None:
        chat_id = await self.private_chat(update)
        if chat_id is None:
            return
        if await self.repository.profile(chat_id) is None:
            await self.begin(chat_id)
            return
        await self.send_compliment(chat_id)

    async def send_compliment(self, chat_id: int) -> None:
        result = await self.repository.draw(chat_id)
        if result is None:
            await self.say(chat_id, NO_CONTENT)
            return
        profile, item = result
        await self.say(chat_id, "💝 " + escape(item.render(profile.name)))

    async def show_settings(self, chat_id: int) -> None:
        profile = await self.repository.profile(chat_id)
        if profile is None:
            await self.begin(chat_id)
            return
        status = "פעילות" if profile.enabled else "כבויות"
        await self.say(
            chat_id,
            "💝 <b>הגדרות מחמאות אישיות</b>\n"
            f"שם: {escape(profile.name)}\nלשון פנייה: {profile.gender.value}\n"
            f"מחמאות יומיות: {status}\nשעה בישראל: {profile.post_time}\n\n"
            "מחמאה אקראית: /compliment\n"
            "שינוי שם: /settings name נועה\n"
            "שינוי לשון פנייה: /settings gender נקבה\n"
            "שינוי שעה: /settings compliment_time 09:00\n"
            "הפעלה: /settings compliments on\nכיבוי: /settings compliments off\n"
            "למחיקת השם וכל ההגדרות בצ׳אט: /forget",
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("שינוי שם", callback_data="cmp:name"),
                        InlineKeyboardButton("לשון פנייה", callback_data="cmp:gender"),
                    ],
                    [
                        InlineKeyboardButton("שינוי שעה", callback_data="cmp:time"),
                        InlineKeyboardButton(
                            "כיבוי יומי" if profile.enabled else "הפעלה יומית",
                            callback_data="cmp:toggle",
                        ),
                    ],
                    [InlineKeyboardButton("מחמאה עכשיו", callback_data="cmp:draw")],
                ]
            ),
        )

    async def settings_command(self, update: Update, context: Context) -> None:
        chat_id = await self.private_chat(update)
        if chat_id is not None:
            await self.show_settings(chat_id)

    async def settings(self, update: Update, context: Context) -> bool:
        args = context.args or []
        if not args or args[0] not in SETTINGS_KEYS:
            return False
        chat_id = await self.private_chat(update)
        if chat_id is None:
            return True
        key, value = args[0], " ".join(args[1:])
        profile = await self.repository.profile(chat_id)
        if profile is None:
            await self.begin(chat_id)
            return True
        try:
            if key == "compliments":
                if value:
                    if value not in {"on", "off"}:
                        raise ValueError("יש לבחור on או off.")
                    if value == "on" and not await self.repository.count(profile.gender):
                        await self.say(chat_id, NO_CONTENT)
                        return True
                    profile = replace(profile, enabled=value == "on")
            elif key == "name":
                profile = replace(profile, name=validate_name(value))
            elif key == "gender":
                profile = replace(profile, gender=Gender(value), enabled=False)
            elif key == "compliment_time":
                profile = replace(profile, post_time=validate_time(value))
            await self.repository.save_profile(profile)
            if not await self.repository.count(profile.gender):
                await self.say(chat_id, NO_CONTENT)
            await self.show_settings(chat_id)
        except ValueError:
            await self.say(chat_id, "הערך אינו תקין. השם ולשון הפנייה בעברית; שעה בפורמט HH:MM.")
        return True

    async def text(self, update: Update, context: Context) -> None:
        if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
            return
        chat_id = update.effective_chat.id
        pending = await self.repository.pending(chat_id)
        if pending is None or not update.effective_message or not update.effective_message.text:
            return
        value = update.effective_message.text.strip()
        try:
            profile = await self.repository.profile(chat_id)
            if pending[0] == "name":
                name = validate_name(value)
                if profile:
                    await self.repository.save_profile(replace(profile, name=name))
                    await self.repository.clear_setup(chat_id)
                    await self.show_settings(chat_id)
                else:
                    await self.repository.setup(chat_id, "gender", name)
                    await self.say(
                        chat_id,
                        "באיזו לשון לפנות? המחמאות יותאמו לבחירה.",
                        gender_buttons(),
                    )
            elif pending[0] == "gender":
                await self.choose_gender(chat_id, Gender(value))
            elif pending[0] == "time" and profile:
                await self.repository.save_profile(replace(profile, post_time=validate_time(value)))
                await self.repository.clear_setup(chat_id)
                await self.show_settings(chat_id)
        except ValueError:
            await self.say(
                chat_id, "הערך אינו תקין. יש להזין שם בעברית, נקבה או זכר, או שעה בפורמט HH:MM."
            )

    async def choose_gender(self, chat_id: int, gender: Gender) -> None:
        profile = await self.repository.profile(chat_id)
        pending = await self.repository.pending(chat_id)
        if profile:
            await self.repository.save_profile(replace(profile, gender=gender, enabled=False))
        elif pending and pending[0] == "gender" and pending[1]:
            await self.repository.save_profile(Profile(chat_id, pending[1], gender))
        else:
            await self.begin(chat_id)
            return
        await self.repository.clear_setup(chat_id)
        if profile is None:
            await self.send_compliment(chat_id)
        elif not await self.repository.count(gender):
            await self.say(chat_id, NO_CONTENT)
        await self.show_settings(chat_id)

    async def callback(self, update: Update, context: Context) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        chat_id = await self.private_chat(update)
        if chat_id is None or not isinstance(query.data, str):
            return
        action = query.data.split(":")
        try:
            if action == ["cmp", "settings"]:
                await self.show_settings(chat_id)
            elif action == ["cmp", "draw"]:
                await self.compliment(update, context)
            elif action == ["cmp", "name"]:
                await self.begin(chat_id)
            elif action == ["cmp", "gender"]:
                await self.say(chat_id, "באיזו לשון לפנות?", gender_buttons())
            elif len(action) == 3 and action[:2] == ["cmp", "gender"]:
                await self.choose_gender(chat_id, Gender(action[2]))
            elif action == ["cmp", "time"]:
                if await self.repository.profile(chat_id) is None:
                    await self.begin(chat_id)
                else:
                    await self.repository.setup(chat_id, "time")
                    await self.say(chat_id, "באיזו שעה בישראל לשלוח מחמאה יומית? למשל 09:00")
            elif action == ["cmp", "toggle"]:
                profile = await self.repository.profile(chat_id)
                if profile is None:
                    await self.begin(chat_id)
                elif not profile.enabled and not await self.repository.count(profile.gender):
                    await self.say(chat_id, NO_CONTENT)
                else:
                    await self.repository.save_profile(
                        replace(profile, enabled=not profile.enabled)
                    )
                    await self.show_settings(chat_id)
        except ValueError:
            await self.say(chat_id, "הבחירה אינה תקינה.")

    async def unsubscribe(self, chat_id: int) -> None:
        profile = await self.repository.profile(chat_id)
        if profile:
            await self.repository.save_profile(replace(profile, enabled=False))

    async def tick(self, now: datetime) -> None:
        for profile in await self.repository.due_profiles(now.strftime("%H:%M")):
            remaining = (
                now.replace(second=0, microsecond=0).timestamp() + 60 - self.clock().timestamp()
            )
            if remaining <= 0:
                return
            if not await self.repository.claim_delivery(profile.chat_id, now.date()):
                continue
            try:
                async with asyncio.timeout(remaining):
                    await self.send_compliment(profile.chat_id)
            except Forbidden:
                await self.repository.store.forget_group(profile.chat_id)
            except (TelegramError, TimeoutError) as exc:
                logger.warning("compliment_delivery_failed=%s", type(exc).__name__)
