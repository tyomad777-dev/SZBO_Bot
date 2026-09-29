import asyncio
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ============ НАСТРОЙКИ ============
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
DB_PATH = "appeals.db"

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN не задан в переменных окружения Bothost")
if ADMIN_CHAT_ID == 0:
    raise SystemExit("❌ ADMIN_CHAT_ID не задан в переменных окружения Bothost")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ============ БАЗА ДАННЫХ ============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT,
            text TEXT,
            contact TEXT,
            created_at TEXT
        )
    """)
    # Миграция: добавляем колонки, если старая база ещё без них
    cur.execute("PRAGMA table_info(appeals)")
    columns = {row[1] for row in cur.fetchall()}
    if "file_id" not in columns:
        cur.execute("ALTER TABLE appeals ADD COLUMN file_id TEXT")
    if "file_type" not in columns:
        cur.execute("ALTER TABLE appeals ADD COLUMN file_type TEXT")
    conn.commit()
    conn.close()


def save_appeal(user_id, category, text, contact, file_id=None, file_type=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO appeals "
        "(user_id, category, text, contact, file_id, file_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, category, text, contact, file_id, file_type,
         datetime.now().isoformat(timespec="seconds")),
    )
    appeal_id = cur.lastrowid
    conn.commit()
    conn.close()
    return appeal_id


def get_appeal(appeal_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, category, text FROM appeals WHERE id = ?",
        (appeal_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


# ============ БОТ И ДИСПЕТЧЕР ============
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


# ============ СОСТОЯНИЯ (FSM) ============
class AppealForm(StatesGroup):
    choosing_category = State()
    writing_text = State()
    attaching_file = State()
    writing_contact = State()


class AdminReply(StatesGroup):
    writing_reply = State()


# ============ КАТЕГОРИИ ============
CATEGORIES = {
    "dorm":   "🏠 Общежитие",
    "study":  "📚 Учёба / преподаватель",
    "money":  "💰 Стипендия / оплата",
    "other":  "📝 Другое",
}


def categories_kb():
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"cat:{code}")]
        for code, label in CATEGORIES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============ /start ============
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 <b>Бот приёма обращений</b>\n\n"
        "Здесь вы можете <b>анонимно</b> сообщить о нарушении "
        "ваших прав как студента.\n"
        "Данные не передаются третьим лицам.\n\n"
        "Выберите категорию обращения:",
        reply_markup=categories_kb(),
    )


# ============ ВЫБОР КАТЕГОРИИ ============
@dp.callback_query(F.data.startswith("cat:"))
async def choose_category(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[1]
    category = CATEGORIES.get(code, "Другое")
    await state.update_data(category=category)
    await state.set_state(AppealForm.writing_text)
    await callback.message.edit_text(
        f"Категория: <b>{category}</b>\n\n"
        "Опишите ситуацию как можно подробнее. "
        "Это сообщение увидят только активисты группы."
    )
    await callback.answer()


# ============ ТЕКСТ ОБРАЩЕНИЯ ============
@dp.message(AppealForm.writing_text)
async def get_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(AppealForm.attaching_file)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить вложение",
                              callback_data="skip_file")]
    ])
    await message.answer(
        "📎 Если есть скриншот, фото или документ — пришлите сейчас.\n\n"
        "Это поможет разобраться быстрее. "
        "Если прикладывать нечего — нажмите «Пропустить вложение».",
        reply_markup=kb,
    )


# ============ ПРИЁМ ВЛОЖЕНИЯ ============
@dp.message(AppealForm.attaching_file, F.photo)
async def get_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(file_id=file_id, file_type="photo")
    await ask_contact(message, state)


@dp.message(AppealForm.attaching_file, F.document)
async def get_document(message: Message, state: FSMContext):
    await state.update_data(
        file_id=message.document.file_id,
        file_type="document",
    )
    await ask_contact(message, state)


@dp.message(AppealForm.attaching_file, F.video)
async def get_video(message: Message, state: FSMContext):
    await state.update_data(
        file_id=message.video.file_id,
        file_type="video",
    )
    await ask_contact(message, state)


@dp.message(AppealForm.attaching_file)
async def get_unsupported(message: Message):
    await message.answer(
        "⚠️ Поддерживаются только фото, документы и видео. "
        "Пришлите один из них или нажмите «Пропустить вложение»."
    )


@dp.callback_query(F.data == "skip_file", AppealForm.attaching_file)
async def skip_file(callback: CallbackQuery, state: FSMContext):
    await state.update_data(file_id=None, file_type=None)
    await ask_contact(callback.message, state)
    await callback.answer()


# ============ СПРОСИТЬ КОНТАКТ ============
async def ask_contact(message: Message, state: FSMContext):
    await state.set_state(AppealForm.writing_contact)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_contact")]
    ])
    await message.answer(
        "Хотите оставить контакт для связи (Telegram, email)?\n"
        "Если нет — нажмите «Пропустить», обращение останется анонимным.",
        reply_markup=kb,
    )


# ============ КОНТАКТ (ввод или пропуск) ============
@dp.message(AppealForm.writing_contact)
async def get_contact(message: Message, state: FSMContext):
    await finish_appeal(message, state, contact=message.text)


@dp.callback_query(F.data == "skip_contact", AppealForm.writing_contact)
async def skip_contact(callback: CallbackQuery, state: FSMContext):
    await finish_appeal(callback.message, state, contact=None)
    await callback.answer()


# ============ ЗАВЕРШЕНИЕ ПРИЁМА ============
async def finish_appeal(message: Message, state: FSMContext, contact):
    data = await state.get_data()
    user_id = message.chat.id
    file_id = data.get("file_id")
    file_type = data.get("file_type")

    try:
        appeal_id = save_appeal(
            user_id=user_id,
            category=data.get("category", "Не указана"),
            text=data.get("text", ""),
            contact=contact,
            file_id=file_id,
            file_type=file_type,
        )
    except Exception as e:
        logging.exception("Ошибка сохранения обращения: %s", e)
        await message.answer(
            "⚠️ Не удалось сохранить обращение. Попробуйте ещё раз "
            "или напишите нам напрямую."
        )
        await state.clear()
        return

    await state.clear()

    try:
        await message.answer(
            f"✅ <b>Обращение №{appeal_id} принято.</b>\n\n"
            "Мы рассмотрим его. Если вы оставили контакт — свяжемся с вами.\n"
            "Спасибо, что не остаётесь в стороне!"
        )
    except Exception as e:
        logging.exception("Не удалось ответить пользователю: %s", e)

    header = (
        f"📩 <b>Новое обращение №{appeal_id}</b>\n\n"
        f"<b>Категория:</b> {data.get('category', 'Не указана')}\n"
        f"<b>Контакт:</b> {contact or 'анонимно'}\n\n"
        f"<b>Текст:</b>\n{data.get('text', '')}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить",
                              callback_data=f"reply:{appeal_id}")]
    ])

    try:
        if file_type == "photo":
            await bot.send_photo(
                ADMIN_CHAT_ID, file_id,
                caption=caption_safe(header), reply_markup=kb,
            )
        elif file_type == "document":
            await bot.send_document(
                ADMIN_CHAT_ID, file_id,
                caption=caption_safe(header), reply_markup=kb,
            )
        elif file_type == "video":
            await bot.send_video(
                ADMIN_CHAT_ID, file_id,
                caption=caption_safe(header), reply_markup=kb,
            )
        else:
            await bot.send_message(ADMIN_CHAT_ID, header, reply_markup=kb)
    except Exception as e:
        logging.exception("Не удалось отправить в админ-чат: %s", e)
        await message.answer(
            "✅ Обращение сохранено, но уведомить активистов не удалось. "
            "Попробуйте написать нам ещё раз через пару минут."
        )


def caption_safe(text: str) -> str:
    """Telegram ограничивает подписи к медиа 1024 символами."""
    return text if len(text) <= 1024 else text[:1020] + "…"


# ============ ОТВЕТ АДМИНИСТРАТОРА ============
@dp.callback_query(F.data.startswith("reply:"))
async def start_reply(callback: CallbackQuery, state: FSMContext):
    appeal_id = int(callback.data.split(":")[1])
    await state.update_data(reply_to=appeal_id)
    await state.set_state(AdminReply.writing_reply)
    await callback.message.answer(
        f"Введите текст ответа для обращения №{appeal_id}:"
    )
    await callback.answer()


@dp.message(AdminReply.writing_reply)
async def send_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    appeal_id = data["reply_to"]
    row = get_appeal(appeal_id)
    if not row:
        await message.answer("Обращение не найдено.")
        await state.clear()
        return

    user_id, _category, _text = row
    try:
        await bot.send_message(
            user_id,
            f"📬 <b>Ответ на ваше обращение №{appeal_id}</b>\n\n"
            f"{message.text}",
        )
        await message.answer("✅ Ответ отправлен пользователю.")
    except Exception as e:
        await message.answer(f"⚠️ Не удалось отправить: {e}")
    await state.clear()


# ============ СТАТИСТИКА ============
@dp.message(Command("stats"))
async def stats(message: Message):
    if message.chat.id != ADMIN_CHAT_ID:
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM appeals")
    total = cur.fetchone()[0]
    cur.execute("SELECT category, COUNT(*) FROM appeals GROUP BY category")
    by_cat = cur.fetchall()
    conn.close()

    lines = [f"📊 <b>Всего обращений:</b> {total}", ""]
    for cat, cnt in by_cat:
        lines.append(f"• {cat}: {cnt}")
    await message.answer("\n".join(lines))


# ============ ЗАПУСК ============
async def main():
    init_db()
    logging.info("Бот запущен. Ожидаю сообщения...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())