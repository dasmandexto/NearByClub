"""
NearByClub Telegram Bot — aiogram 3
Handles /start, location sharing, anonymous messaging.
"""
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, Location as TgLocation
)
from dotenv import load_dotenv

from db.models import (
    async_session, get_or_create_user, upsert_location,
    find_nearby_user_ids, count_nearby, Message as DbMessage,
    async_session
)
from sqlalchemy import select

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com")
MAX_RADIUS_M = 1000
DEFAULT_RADIUS_M = 500
RATE_LIMIT_PER_HOUR = 5  # max broadcast messages per user per hour

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("NearByBot")

router = Router()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Поделиться локацией", request_location=True)],
            [KeyboardButton(text="🗺️ Открыть карту", web_app=WebAppInfo(url=WEBAPP_URL))],
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )


@router.message(CommandStart())
async def cmd_start(message: Message):
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            tg_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name
        )

    await message.answer(
        f"Привет, <b>{user.animal_emoji} {user.animal_name}</b>!\n\n"
        "Это социальный эксперимент:\n"
        "<i>Могут ли незнакомые люди найти связь, просто потому что они рядом?</i>\n\n"
        "📍 Поделись локацией — и увидишь, кто в радиусе <b>500м</b>.\n"
        "💬 Напиши что-нибудь — и твоё сообщение получат все рядом анонимно.\n\n"
        "<b>Ты — просто твой зверь. Никаких имён.</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


@router.message(F.location)
async def handle_location(message: Message):
    loc: TgLocation = message.location
    tg_id = message.from_user.id

    async with async_session() as session:
        user = await get_or_create_user(
            session,
            tg_id=tg_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name
        )
        await upsert_location(session, user.id, loc.latitude, loc.longitude)
        count = await count_nearby(
            session, loc.latitude, loc.longitude,
            radius_m=DEFAULT_RADIUS_M,
            exclude_user_id=user.id
        )

    if count == 0:
        nearby_text = "Пока никого нет рядом 🌑\nПопробуй позже или расширь радиус на карте."
    elif count == 1:
        nearby_text = "Рядом <b>1 человек</b> 🔴"
    else:
        nearby_text = f"Рядом <b>{count} {'человек' if count < 5 else 'людей'}</b> 🔴"

    await message.answer(
        f"📍 Локация сохранена на 2 часа.\n\n{nearby_text}\n\n"
        "Напиши что-нибудь — и они получат твоё сообщение анонимно 👇",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message):
    tg_id = message.from_user.id
    text = message.text.strip()

    if len(text) > 500:
        await message.answer("Сообщение слишком длинное (макс. 500 символов).")
        return

    async with async_session() as session:
        user = await get_or_create_user(
            session,
            tg_id=tg_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name
        )

        # Find user's last known location
        from db.models import Location
        loc_result = await session.execute(
            select(Location).where(Location.user_id == user.id)
        )
        loc = loc_result.scalar_one_or_none()

        if not loc:
            await message.answer(
                "Сначала поделись локацией 📍, чтобы мы знали, кому отправить сообщение.",
                reply_markup=main_keyboard()
            )
            return

        # Get lat/lon from PostGIS point
        from geoalchemy2.shape import to_shape
        pt = to_shape(loc.point)
        lat, lon = pt.y, pt.x

        nearby_ids = await find_nearby_user_ids(
            session, lat, lon,
            radius_m=DEFAULT_RADIUS_M,
            exclude_user_id=user.id,
            limit=100
        )

        if not nearby_ids:
            await message.answer(
                "Рядом никого нет сейчас 🌑\nТвоё сообщение никуда не ушло.",
                reply_markup=main_keyboard()
            )
            return

        # Get recipient tg_ids
        from db.models import User as DbUser
        result = await session.execute(
            select(DbUser.tg_id, DbUser.animal_emoji, DbUser.animal_name).where(
                DbUser.id.in_(nearby_ids),
                DbUser.is_active == True,
                DbUser.is_banned == False
            )
        )
        recipients = result.fetchall()

        # Log message
        db_msg = DbMessage(
            sender_id=user.id,
            text=text,
            recipient_count=len(recipients),
            radius_m=DEFAULT_RADIUS_M
        )
        session.add(db_msg)
        await session.commit()

    # Broadcast anonymously
    bot: Bot = message.bot
    sent = 0
    outgoing_text = (
        f"{user.animal_emoji} <b>{user.animal_name}</b> рядом с тобой пишет:\n\n"
        f"<i>{text}</i>\n\n"
        "<code>— анонимное сообщение от незнакомца поблизости</code>"
    )

    for i, (recipient_tg_id, _, _) in enumerate(recipients):
        try:
            await bot.send_message(
                chat_id=recipient_tg_id,
                text=outgoing_text,
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            logger.debug(f"Failed to deliver to {recipient_tg_id}: {e}")

        if i % 25 == 0 and i > 0:
            await asyncio.sleep(1.0)  # Respect Telegram rate limits

    await message.answer(
        f"✅ Сообщение отправлено <b>{sent}</b> {'человеку' if sent == 1 else 'людям'} рядом.",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not set in .env")

    bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("NearByClub bot starting...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
