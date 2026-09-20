"""
Anniversary blast script — sends a one-time notification to all active bot users.
Run once: python blast.py
Respects Telegram flood limits: 30 messages/second max.
"""
import asyncio
import logging
import os
from datetime import datetime
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://t.me/your_bot/app")

BLAST_TEXT = """🎉 <b>5 месяцев на PlayMarket!</b>

Уже 5 месяцев мы проводим один из самых честных социальных экспериментов:
<i>могут ли незнакомые люди найти друг друга просто потому что они рядом?</i>

Спасибо, что ты часть этого. 🙌

Прямо сейчас рядом с тобой могут быть люди — открой карту и посмотри 👇"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AnniversaryBlast")


def build_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🗺️ Посмотреть кто рядом",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])


async def get_all_user_ids() -> list[int]:
    """
    Load all active user Telegram IDs from your database.
    Replace this with your actual DB query.

    Example with asyncpg:
        rows = await conn.fetch("SELECT tg_id FROM users WHERE is_active = true")
        return [r["tg_id"] for r in rows]
    """
    # ── Stub: replace with real DB query ──────────────────────────────────────
    # import asyncpg
    # conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
    # rows = await conn.fetch("SELECT tg_id FROM users WHERE is_active = true")
    # await conn.close()
    # return [r["tg_id"] for r in rows]
    # ─────────────────────────────────────────────────────────────────────────
    raise NotImplementedError(
        "Implement get_all_user_ids() with your DB connection. "
        "See comments in this function."
    )


async def blast(bot: Bot, user_ids: list[int]) -> dict:
    total = len(user_ids)
    sent = 0
    failed = 0
    blocked = 0

    logger.info(f"Starting anniversary blast to {total} users...")
    keyboard = build_keyboard()

    for i, tg_id in enumerate(user_ids, start=1):
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=BLAST_TEXT,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            sent += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "deactivated" in err or "not found" in err:
                blocked += 1
                logger.debug(f"User {tg_id} blocked/inactive: {e}")
            else:
                failed += 1
                logger.warning(f"Failed for user {tg_id}: {e}")

        # Telegram flood limit: 30 msg/sec
        # We do 25/sec to stay safe
        if i % 25 == 0:
            await asyncio.sleep(1.0)
            logger.info(f"Progress: {i}/{total} — sent={sent}, blocked={blocked}, failed={failed}")

    return {"total": total, "sent": sent, "blocked": blocked, "failed": failed}


async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set in .env")

    bot = Bot(token=BOT_TOKEN)

    try:
        user_ids = await get_all_user_ids()
    except NotImplementedError as e:
        logger.error(str(e))
        await bot.session.close()
        return

    start = datetime.now()
    results = await blast(bot, user_ids)
    elapsed = (datetime.now() - start).total_seconds()

    logger.info("=" * 50)
    logger.info(f"Blast complete in {elapsed:.1f}s")
    logger.info(f"  Total users:   {results['total']}")
    logger.info(f"  ✅ Sent:       {results['sent']}")
    logger.info(f"  🚫 Blocked:    {results['blocked']}")
    logger.info(f"  ❌ Failed:     {results['failed']}")
    logger.info("=" * 50)

    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
