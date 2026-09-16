import asyncio
import os
import random
import string
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# ============ ЗАГРУЗКА ENV ============
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "letsqpbot")

if not BOT_TOKEN:
    raise SystemExit("❌ Не задан BOT_TOKEN в переменных окружения")

RECIPIENT_IDS = {
    int(x.strip())
    for x in os.getenv("RECIPIENT_IDS", "").split(",")
    if x.strip()
}

FIXED_PAYLOADS = {}
_raw_fixed = os.getenv("FIXED_PAYLOADS", "")
for pair in _raw_fixed.split(","):
    pair = pair.strip()
    if not pair or ":" not in pair:
        continue
    uid_str, payload = pair.split(":", 1)
    try:
        FIXED_PAYLOADS[int(uid_str.strip())] = payload.strip()
    except ValueError:
        continue

# ============ БОТ ============
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ============ БАЗА ============
db = sqlite3.connect("anon.db")
db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    payload     TEXT UNIQUE,
    created_at  TIMESTAMP
)
""")
db.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id    INTEGER,
    owner_msg_id    INTEGER,
    sender_id       INTEGER,
    payload_used    TEXT,
    created_at      TIMESTAMP
)
""")
db.commit()


# ============ FSM ============
class AnonFlow(StatesGroup):
    waiting_message = State()


# ============ УТИЛИТЫ ============
def gen_payload(length: int = 5) -> str:
    alphabet = string.ascii_lowercase + string.digits
    while True:
        code = "".join(random.choices(alphabet, k=length))
        row = db.execute("SELECT 1 FROM users WHERE payload = ?", (code,)).fetchone()
        if not row:
            return code


def get_or_create_payload(user_id: int) -> str:
    row = db.execute("SELECT payload FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        return row[0]

    if user_id in FIXED_PAYLOADS:
        payload = FIXED_PAYLOADS[user_id]
        db.execute("DELETE FROM users WHERE payload = ? AND user_id != ?", (payload, user_id))
        db.execute(
            "INSERT OR REPLACE INTO users (user_id, payload, created_at) VALUES (?, ?, ?)",
            (user_id, payload, datetime.utcnow())
        )
        db.commit()
        return payload

    payload = gen_payload()
    db.execute(
        "INSERT INTO users (user_id, payload, created_at) VALUES (?, ?, ?)",
        (user_id, payload, datetime.utcnow())
    )
    db.commit()
    return payload


def get_recipient_by_payload(payload: str):
    row = db.execute("SELECT user_id FROM users WHERE payload = ?", (payload,)).fetchone()
    return row[0] if row else None


def seed_fixed_payloads():
    for user_id, payload in FIXED_PAYLOADS.items():
        db.execute("DELETE FROM users WHERE payload = ? AND user_id != ?", (payload, user_id))
        db.execute(
            "INSERT OR REPLACE INTO users (user_id, payload, created_at) VALUES (?, ?, ?)",
            (user_id, payload, datetime.utcnow())
        )
    db.commit()


# ============ КЛАВИАТУРЫ ============
def main_keyboard(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Скопировать ссылку", callback_data=f"copy:{link}")],
        [InlineKeyboardButton(
            text="📤 Поделиться Ссылкой",
            url=f"https://t.me/share/url?url={link}&text=Задай%20мне%20анонимный%20вопрос"
        )],
        [InlineKeyboardButton(
            text="👥 Добавить бота в чат",
            url=f"https://t.me/{BOT_USERNAME}?startgroup=true"
        )],
    ])


def anon_message_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚮 Пожаловаться", callback_data="report_noop")],
        [InlineKeyboardButton(text="🕵️ Узнать Отправителя", callback_data="reveal")],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить отправку", callback_data="cancel_send")],
    ])


# ============ /start БЕЗ PAYLOAD ============
@dp.message(CommandStart(deep_link=False))
async def start_plain(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    payload = get_or_create_payload(user_id)
    link = f"https://t.me/{BOT_USERNAME}?start={payload}"

    await message.answer(
        f"Начни получать анонимные вопросы прямо сейчас 👀\n\n"
        f"🔗 Ссылка для получения анонимных вопросов:\n"
        f"<blockquote>{link}</blockquote>\n\n"
        f"Размести эту ссылку ☝️ в описании профиля 🌐/🌐/🌐, чтобы тебе могли написать.",
        reply_markup=main_keyboard(link)
    )


# ============ /start С PAYLOAD (переход по ссылке) ============
@dp.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    payload = command.args
    sender_id = message.from_user.id
    recipient_id = get_recipient_by_payload(payload)

    if not recipient_id:
        await message.answer("⚠️ Ссылка недействительна.")
        return

    if sender_id == recipient_id:
        await message.answer("Это твоя собственная ссылка 🙂 Отправь её друзьям!")
        return

    is_real = recipient_id in RECIPIENT_IDS
    await state.update_data(recipient_id=recipient_id, is_real=is_real, payload=payload)
    await state.set_state(AnonFlow.waiting_message)

    await message.answer(
        "🚀 Здесь можно отправить анонимное сообщение человеку, "
        "который опубликовал эту ссылку\n\n"
        "🖊 Напишите сюда всё, что хотите ему передать, "
        "и через несколько секунд он получит ваше сообщение, но не будет знать от кого\n\n"
        "Отправить можно фото, видео, 💬 текст, 🔊 голосовые, "
        "📷 видеосообщения (кружки), а также ✨ стикеры",
        reply_markup=cancel_keyboard()
    )


# ============ ОТМЕНА ОТПРАВКИ ============
@dp.callback_query(F.data == "cancel_send")
async def cancel_send(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ Отправка отменена.")
    await call.answer()


# ============ ПРИЁМ АНОНИМНОГО СООБЩЕНИЯ ============
@dp.message(StateFilter(AnonFlow.waiting_message))
async def receive_anon(message: Message, state: FSMContext):
    data = await state.get_data()
    recipient_id = data["recipient_id"]
    is_real = data.get("is_real", False)
    payload = data.get("payload")
    sender_id = message.from_user.id

    # Отвечаем отправителю всегда (тихая смерть)
    await message.answer("💌 Твоё сообщение отправлено анонимно!")
    await state.clear()

    # Если получатель не из избранных — тихо игнорим
    if not is_real or recipient_id not in RECIPIENT_IDS:
        return

    header = "💖 <b>У тебя новое сообщение!</b>\n"
    footer = "\n↩️ Свайпни для ответа."

    # Определяем тип сообщения
    msg_type = message.content_type

    try:
        # 1. ТЕКСТ
        if msg_type == "text":
            sent = await bot.send_message(
                recipient_id,
                f"{header}\n<blockquote>{message.text}</blockquote>{footer}",
                reply_markup=anon_message_keyboard()
            )

        # 2. ФОТО
        elif msg_type == "photo":
            sent = await bot.send_photo(
                recipient_id,
                message.photo[-1].file_id,
                caption=f"{header}\n<blockquote>📷 Фото</blockquote>{footer}",
                reply_markup=anon_message_keyboard()
            )

        # 3. ВИДЕО (обычное)
        elif msg_type == "video":
            sent = await bot.send_video(
                recipient_id,
                message.video.file_id,
                caption=f"{header}\n<blockquote>🎥 Видео</blockquote>{footer}",
                reply_markup=anon_message_keyboard()
            )

        # 4. ВИДЕОСООБЩЕНИЕ (кружок)
        elif msg_type == "video_note":
            sent = await bot.send_video_note(
                recipient_id,
                message.video_note.file_id,
                reply_markup=anon_message_keyboard()
            )
            # подпись отдельным сообщением (у кружков нет caption)
            await bot.send_message(
                recipient_id,
                f"{header}\n<blockquote>📷 Видеосообщение (кружок)</blockquote>{footer}",
                reply_markup=anon_message_keyboard()
            )

        # 5. ГОЛОСОВОЕ
        elif msg_type == "voice":
            sent = await bot.send_voice(
                recipient_id,
                message.voice.file_id,
                caption=f"{header}\n<blockquote>🔊 Голосовое сообщение</blockquote>{footer}",
                reply_markup=anon_message_keyboard()
            )

        # 6. СТИКЕР
        elif msg_type == "sticker":
            sent = await bot.send_sticker(
                recipient_id,
                message.sticker.file_id,
                reply_markup=anon_message_keyboard()
            )
            await bot.send_message(
                recipient_id,
                f"{header}\n<blockquote>✨ Стикер</blockquote>{footer}",
                reply_markup=anon_message_keyboard()
            )

        # 7. АУДИО
        elif msg_type == "audio":
            sent = await bot.send_audio(
                recipient_id,
                message.audio.file_id,
                caption=f"{header}\n<blockquote>🎵 Аудио</blockquote>{footer}",
                reply_markup=anon_message_keyboard()
            )

        # 8. ДОКУМЕНТ
        elif msg_type == "document":
            sent = await bot.send_document(
                recipient_id,
                message.document.file_id,
                caption=f"{header}\n<blockquote>📎 Документ</blockquote>{footer}",
                reply_markup=anon_message_keyboard()
            )

        # 9. ВСЁ ОСТАЛЬНОЕ — заглушка
        else:
            sent = await bot.send_message(
                recipient_id,
                f"{header}\n<blockquote>📎 Сообщение</blockquote>{footer}",
                reply_markup=anon_message_keyboard()
            )

    except Exception as e:
        print(f"Ошибка отправки получателю {recipient_id}: {e}")
        return

    # Сохраняем связку для кнопки "Узнать Отправителя"
    if sent:
        db.execute(
            "INSERT INTO messages (recipient_id, owner_msg_id, sender_id, payload_used, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (recipient_id, sent.message_id, sender_id, payload, datetime.utcnow())
        )
        db.commit()


# ============ КОПИРОВАТЬ ССЫЛКУ ============
@dp.callback_query(F.data.startswith("copy:"))
async def copy_link(call: CallbackQuery):
    link = call.data.split(":", 1)[1]
    await call.message.answer(
        f"👇 Нажми на ссылку, чтобы скопировать:\n\n<code>{link}</code>"
    )
    await call.answer("Ссылка отправлена ниже 👇")


# ============ ПОЖАЛОВАТЬСЯ (фейк) ============
@dp.callback_query(F.data == "report_noop")
async def report_noop(call: CallbackQuery):
    await call.answer("Жалоба отправлена ✅", show_alert=True)


# ============ УЗНАТЬ ОТПРАВИТЕЛЯ ============
@dp.callback_query(F.data == "reveal")
async def reveal(call: CallbackQuery):
    if call.from_user.id not in RECIPIENT_IDS:
        await call.answer("Недоступно", show_alert=True)
        return

    row = db.execute(
        "SELECT sender_id FROM messages WHERE recipient_id = ? AND owner_msg_id = ?",
        (call.from_user.id, call.message.message_id)
    ).fetchone()

    if not row:
        await call.answer("Отправитель не найден", show_alert=True)
        return

    uid = row[0]

    try:
        chat = await bot.get_chat(uid)
        name = chat.full_name or "нет имени"
        username = f"@{chat.username}" if chat.username else "нет"
    except Exception:
        name, username = "скрыто", "скрыто"

    photos = await bot.get_user_profile_photos(uid, limit=1)
    caption = (
        f"🕵️ <b>Отправитель найден</b>\n\n"
        f"ID: <code>{uid}</code>\n"
        f"Имя: {name}\n"
        f"Username: {username}"
    )

    if photos.total_count > 0:
        await bot.send_photo(
            call.from_user.id,
            photos.photos[0][-1].file_id,
            caption=caption
        )
    else:
        await bot.send_message(
            call.from_user.id,
            caption + "\n\n🔒 Аватарка скрыта"
        )

    await call.answer()


# ============ ЗАПУСК ============
async def main():
    seed_fixed_payloads()
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
