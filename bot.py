import asyncio
import sqlite3
import os
from datetime import datetime, timedelta
from calendar import monthrange

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


# =========================================================
# НАЛАШТУВАННЯ
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Telegram ID барбера
BARBER_ID = 7537719778

BARBER_NAME = "Ім'я Барбера"

INSTAGRAM_URL = "https://instagram.com/ВАШ_INSTAGRAM"
TIKTOK_URL = "https://tiktok.com/@ВАШ_TIKTOK"

ADDRESS_TEXT = "Адреса барбершопу"
GOOGLE_MAPS_URL = "https://maps.google.com/"

SERVICES = {
    "Стрижка": 300,
    "Борода": 200,
    "Стрижка + борода": 450,
}

DB_NAME = "/data/zapysbarber.db"


# =========================================================
# БАЗА ДАНИХ
# =========================================================

db = sqlite3.connect(DB_NAME)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    phone TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS work_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT UNIQUE
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS work_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day_id INTEGER,
    time TEXT,
    blocked INTEGER DEFAULT 0,
    UNIQUE(day_id, time),
    FOREIGN KEY(day_id) REFERENCES work_days(id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    day_id INTEGER,
    time_id INTEGER,
    service TEXT,
    phone TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    FOREIGN KEY(day_id) REFERENCES work_days(id),
    FOREIGN KEY(time_id) REFERENCES work_times(id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cooldowns (
    user_id INTEGER PRIMARY KEY,
    until_time TEXT
)
""")

db.commit()


# =========================================================
# BOT
# =========================================================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# КЛАВІАТУРИ
# =========================================================

def client_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📅 Записатися"),
                KeyboardButton(text="📋 Мої записи")
            ],
            [
                KeyboardButton(text="👤 Про барбера"),
                KeyboardButton(text="📍 Адреса")
            ],
            [
                KeyboardButton(text="💰 Вартість послуг")
            ]
        ],
        resize_keyboard=True
    )


def barber_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📥 Нові записи")],
            [KeyboardButton(text="📅 Створити робочий день")],
            [KeyboardButton(text="⏰ Створити час")],
            [KeyboardButton(text="🚫 Заблокувати час")],
            [KeyboardButton(text="🔓 Розблокувати час")],
            [KeyboardButton(text="📋 Дублювати робочі дні")],
            [KeyboardButton(text="📅 Переглянути дати")],
           [KeyboardButton(text="🗑 Видалити робочу дату")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )


def back_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )


def phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📱 Надіслати номер телефону",
                    request_contact=True
                )
            ],
            [
                KeyboardButton(text="🔙 Назад")
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


# =========================================================
# FSM
# =========================================================

class Booking(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    waiting_phone = State()


class CreateDay(StatesGroup):
    waiting_date = State()


class CreateTime(StatesGroup):
    waiting_date = State()
    waiting_time = State()


class BlockTime(StatesGroup):
    waiting_date = State()
    waiting_time = State()


class UnblockTime(StatesGroup):
    waiting_date = State()
    waiting_time = State()


# =========================================================
# ДОПОМІЖНІ ФУНКЦІЇ
# =========================================================

def is_barber(user_id: int) -> bool:
    return user_id == BARBER_ID


def get_day(date_text):
    cursor.execute(
        "SELECT id FROM work_days WHERE date = ?",
        (date_text,)
    )
    return cursor.fetchone()


def get_day_id(date_text):
    result = get_day(date_text)
    return result[0] if result else None


def format_date(date_text):
    try:
        date_obj = datetime.strptime(date_text, "%Y-%m-%d")
        return date_obj.strftime("%d.%m")
    except:
        return date_text


def check_cooldown(user_id):
    cursor.execute(
        "SELECT until_time FROM cooldowns WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    if not result:
        return False

    until_time = datetime.fromisoformat(result[0])

    if datetime.now() < until_time:
        return True

    cursor.execute(
        "DELETE FROM cooldowns WHERE user_id = ?",
        (user_id,)
    )
    db.commit()

    return False


def get_available_dates():
    cursor.execute("""
        SELECT wd.id, wd.date
        FROM work_days wd
        JOIN work_times wt ON wt.day_id = wd.id
        WHERE wt.blocked = 0
        AND NOT EXISTS (
            SELECT 1
            FROM bookings b
            WHERE b.time_id = wt.id
            AND b.status IN ('pending', 'confirmed')
        )
        GROUP BY wd.id, wd.date
        ORDER BY wd.date
    """)

    return cursor.fetchall()


def get_available_times(day_id):
    cursor.execute("""
        SELECT wt.id, wt.time
        FROM work_times wt
        WHERE wt.day_id = ?
        AND wt.blocked = 0
        AND NOT EXISTS (
            SELECT 1
            FROM bookings b
            WHERE b.time_id = wt.id
            AND b.status IN ('pending', 'confirmed')
        )
        ORDER BY wt.time
    """, (day_id,))

    return cursor.fetchall()


def get_all_times(day_id):
    cursor.execute("""
        SELECT id, time, blocked
        FROM work_times
        WHERE day_id = ?
        ORDER BY time
    """, (day_id,))

    return cursor.fetchall()


def has_active_booking(time_id):
    cursor.execute("""
        SELECT id
        FROM bookings
        WHERE time_id = ?
        AND status IN ('pending', 'confirmed')
        LIMIT 1
    """, (time_id,))
    return cursor.fetchone() is not None


def has_active_bookings_for_day(day_id):
    cursor.execute("""
        SELECT id
        FROM bookings
        WHERE day_id = ?
        AND status IN ('pending', 'confirmed')
        LIMIT 1
    """, (day_id,))
    return cursor.fetchone() is not None


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()

    if is_barber(message.from_user.id):
        await message.answer(
            "👋 Вітаю, барбере!\n\n"
            "Це панель керування zapysbarber.",
            reply_markup=barber_menu()
        )
    else:
        await message.answer(
            "👋 Вітаємо у zapysbarber!\n\n"
            "Оберіть потрібну дію:",
            reply_markup=client_menu()
        )


# =========================================================
# НАЗАД
# =========================================================

@dp.message(F.text == "🔙 Назад")
async def back(message: Message, state: FSMContext):
    await state.clear()

    if is_barber(message.from_user.id):
        await message.answer(
            "Головне меню барбера:",
            reply_markup=barber_menu()
        )
    else:
        await message.answer(
            "Головне меню:",
            reply_markup=client_menu()
        )
        
@dp.callback_query(F.data == "back_client")
async def back_client(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()

    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.message.answer(
        "Головне меню:",
        reply_markup=client_menu()
    )


# =========================================================
# ПРО БАРБЕРА
# =========================================================

@dp.message(F.text == "👤 Про барбера")
async def about_barber(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Instagram",
                    url=INSTAGRAM_URL
                )
            ],
            [
                InlineKeyboardButton(
                    text="TikTok",
                    url=TIKTOK_URL
                )
            ]
        ]
    )

    await message.answer(
        f"👤 Барбер: {BARBER_NAME}",
        reply_markup=keyboard
    )

    await message.answer(
        "Оберіть дію:",
        reply_markup=back_keyboard()
    )


# =========================================================
# АДРЕСА
# =========================================================

@dp.message(F.text == "📍 Адреса")
async def address(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📍 Відкрити Google Maps",
                    url=GOOGLE_MAPS_URL
                )
            ]
        ]
    )

    await message.answer(
        f"📍 Адреса:\n{ADDRESS_TEXT}",
        reply_markup=keyboard
    )

    await message.answer(
        "Оберіть дію:",
        reply_markup=back_keyboard()
    )


# =========================================================
# ВАРТІСТЬ
# =========================================================

@dp.message(F.text == "💰 Вартість послуг")
async def prices(message: Message):
    text = "💰 Вартість послуг:\n\n"

    for service, price in SERVICES.items():
        text += f"✂️ {service} — {price} грн\n"

    await message.answer(
        text,
        reply_markup=back_keyboard()
    )


# =========================================================
# ЗАПИСАТИСЯ
# =========================================================

@dp.message(F.text == "📅 Записатися")
async def booking_start(message: Message, state: FSMContext):
    if is_barber(message.from_user.id):
        return

    if check_cooldown(message.from_user.id):
        await message.answer(
            "⏳ Зачекайте 1 хвилину перед створенням нового запису.",
            reply_markup=client_menu()
        )
        return

    keyboard = []

    for service, price in SERVICES.items():
        keyboard.append([
            InlineKeyboardButton(
                text=f"{service} — {price} грн",
                callback_data=f"service:{service}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_client"
        )
    ])

    await state.set_state(Booking.choosing_service)

    await message.answer(
        "✂️ Оберіть послугу:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )


# =========================================================
# ВИБІР ПОСЛУГИ
# =========================================================

@dp.callback_query(F.data.startswith("service:"))
async def choose_service(callback: CallbackQuery, state: FSMContext):
    service = callback.data.split(":", 1)[1]

    await state.update_data(service=service)
    await state.set_state(Booking.choosing_date)

    dates = get_available_dates()

    if not dates:
        await callback.message.edit_text(
            "❌ Зараз немає доступних дат."
        )

        await callback.message.answer(
            "Поверніться пізніше.",
            reply_markup=client_menu()
        )

        return

    keyboard = []

    for day_id, date_text in dates:
        keyboard.append([
            InlineKeyboardButton(
                text=format_date(date_text),
                callback_data=f"date:{day_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_client"
        )
    ])

    await callback.message.edit_text(
        "📅 Оберіть дату:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

    await callback.answer()


# =========================================================
# ВИБІР ДАТИ
# =========================================================

@dp.callback_query(F.data.startswith("date:"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    day_id = int(callback.data.split(":")[1])

    times = get_available_times(day_id)

    if not times:
        await callback.answer(
            "На цю дату вже немає вільного часу.",
            show_alert=True
        )
        return

    await state.update_data(day_id=day_id)
    await state.set_state(Booking.choosing_time)

    keyboard = []

    for time_id, time_text in times:
        keyboard.append([
            InlineKeyboardButton(
                text=f"⏰ {time_text}",
                callback_data=f"time:{time_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_client"
        )
    ])

    await callback.message.edit_text(
        "⏰ Оберіть час:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

    await callback.answer()


# =========================================================
# ВИБІР ЧАСУ
# =========================================================

@dp.callback_query(F.data.startswith("time:"))
async def choose_time(callback: CallbackQuery, state: FSMContext):
    time_id = int(callback.data.split(":")[1])

    cursor.execute(
        "SELECT id, day_id, time, blocked FROM work_times WHERE id = ?",
        (time_id,)
    )

    result = cursor.fetchone()

    if not result:
        await callback.answer(
            "Цей час більше недоступний.",
            show_alert=True
        )
        return

    _, day_id, time_text, blocked = result

    if blocked:
        await callback.answer(
            "Цей час заблокований.",
            show_alert=True
        )
        return

    cursor.execute("""
        SELECT id
        FROM bookings
        WHERE time_id = ?
        AND status IN ('pending', 'confirmed')
    """, (time_id,))

    if cursor.fetchone():
        await callback.answer(
            "Цей час вже зайнятий.",
            show_alert=True
        )
        return

    await state.update_data(
        time_id=time_id,
        time_text=time_text
    )

    await state.set_state(Booking.waiting_phone)

    await callback.message.edit_text(
        "📱 Надішліть номер телефону, щоб барбер міг підтвердити запис."
    )

    await callback.message.answer(
        "Натисніть кнопку нижче:",
        reply_markup=phone_keyboard()
    )

    await callback.answer()


# =========================================================
# НОМЕР ТЕЛЕФОНУ
# =========================================================

@dp.message(Booking.waiting_phone, F.contact)
async def receive_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number

    data = await state.get_data()

    day_id = data["day_id"]
    time_id = data["time_id"]
    service = data["service"]

    cursor.execute("""
        SELECT id
        FROM bookings
        WHERE time_id = ?
        AND status IN ('pending', 'confirmed')
    """, (time_id,))

    if cursor.fetchone():
        await state.clear()

        await message.answer(
            "❌ На жаль, цей час щойно зайняли.",
            reply_markup=client_menu()
        )
        return

    cursor.execute("""
        INSERT INTO users (user_id, phone)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET phone = excluded.phone
    """, (message.from_user.id, phone))

    cursor.execute("""
        INSERT INTO bookings
        (
            user_id,
            day_id,
            time_id,
            service,
            phone,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    """, (
        message.from_user.id,
        day_id,
        time_id,
        service,
        phone,
        datetime.now().isoformat()
    ))

    booking_id = cursor.lastrowid

    db.commit()

    await state.clear()

    await message.answer(
        "✅ Запит на запис створено!\n\n"
        "Невдовзі барбер відповість на ваше замовлення.",
        reply_markup=client_menu()
    )

    # Отримуємо дату
    cursor.execute(
        "SELECT date FROM work_days WHERE id = ?",
        (day_id,)
    )

    date_text = cursor.fetchone()[0]

    cursor.execute(
        "SELECT time FROM work_times WHERE id = ?",
        (time_id,)
    )

    time_text = cursor.fetchone()[0]

    # Повідомлення барберу
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Прийняти",
                    callback_data=f"accept:{booking_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Відхилити",
                    callback_data=f"reject:{booking_id}"
                )
            ]
        ]
    )

    await bot.send_message(
        BARBER_ID,
        f"📥 Новий запис!\n\n"
        f"📅 Дата: {format_date(date_text)}\n"
        f"⏰ Час: {time_text}\n"
        f"✂️ Послуга: {service}\n"
        f"📱 Телефон: {phone}",
        reply_markup=keyboard
    )


# =========================================================
# ПЕРЕВІРКА РУЧНОГО ТЕКСТУ ЗАМІСТЬ НОМЕРА
# =========================================================

@dp.message(Booking.waiting_phone)
async def manual_phone(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()

        await message.answer(
            "Головне меню:",
            reply_markup=client_menu()
        )

        return

    await message.answer(
        "📱 Будь ласка, натисніть «Надіслати номер телефону».",
        reply_markup=phone_keyboard()
    )


# =========================================================
# ПРИЙНЯТИ ЗАПИС
# =========================================================

@dp.callback_query(F.data.startswith("accept:"))
async def accept_booking(callback: CallbackQuery):
    if not is_barber(callback.from_user.id):
        return

    booking_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT user_id, day_id, time_id, service, phone, status
        FROM bookings
        WHERE id = ?
    """, (booking_id,))

    booking = cursor.fetchone()

    if not booking:
        await callback.answer(
            "Запис не знайдено.",
            show_alert=True
        )
        return

    user_id, day_id, time_id, service, phone, status = booking

    if status != "pending":
        await callback.answer(
            "Цей запис вже оброблений.",
            show_alert=True
        )
        return

    cursor.execute(
        "UPDATE bookings SET status = 'confirmed' WHERE id = ?",
        (booking_id,)
    )

    db.commit()

    cursor.execute(
        "SELECT date FROM work_days WHERE id = ?",
        (day_id,)
    )

    date_text = cursor.fetchone()[0]

    cursor.execute(
        "SELECT time FROM work_times WHERE id = ?",
        (time_id,)
    )

    time_text = cursor.fetchone()[0]

    await bot.send_message(
        user_id,
        f"✅ Ваш запис підтверджено!\n\n"
        f"✂️ Послуга: {service}\n"
        f"📅 Дата: {format_date(date_text)}\n"
        f"⏰ Час: {time_text}\n"
        f"📍 {ADDRESS_TEXT}"
    )

    await callback.message.edit_text(
        f"✅ Запис підтверджено!\n\n"
        f"📅 {format_date(date_text)}\n"
        f"⏰ {time_text}\n"
        f"✂️ {service}\n"
        f"📱 {phone}"
    )

    await callback.answer("Запис прийнято.")


# =========================================================
# ВІДХИЛИТИ ЗАПИС =========================================================

@dp.callback_query(F.data.startswith("reject:"))
async def reject_booking(callback: CallbackQuery):
    if not is_barber(callback.from_user.id):
        return

    booking_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT user_id, day_id, time_id, service, phone, status
        FROM bookings
        WHERE id = ?
    """, (booking_id,))

    booking = cursor.fetchone()

    if not booking:
        await callback.answer(
            "Запис не знайдено.",
            show_alert=True
        )
        return

    user_id, day_id, time_id, service, phone, status = booking

    if status != "pending":
        await callback.answer(
            "Цей запис вже оброблений.",
            show_alert=True
        )
        return

    cursor.execute(
        "UPDATE bookings SET status = 'rejected' WHERE id = ?",
        (booking_id,)
    )

    db.commit()

    cursor.execute(
        "SELECT date FROM work_days WHERE id = ?",
        (day_id,)
    )

    date_text = cursor.fetchone()[0]

    cursor.execute(
        "SELECT time FROM work_times WHERE id = ?",
        (time_id,)
    )

    time_text = cursor.fetchone()[0]

    await bot.send_message(
        user_id,
        f"❌ Барбер не зміг підтвердити ваш запис.\n\n"
        f"📅 {format_date(date_text)}\n"
        f"⏰ {time_text}\n\n"
        f"Цей час знову доступний для запису.",
        reply_markup=client_menu()
    )

    await callback.message.edit_text(
        f"❌ Запис відхилено.\n\n"
        f"📅 {format_date(date_text)}\n"
        f"⏰ {time_text}\n"
        f"✂️ {service}\n"
        f"📱 {phone}"
    )

    await callback.answer("Запис відхилено.")


# =========================================================
# МОЇ ЗАПИСИ
# =========================================================

@dp.message(F.text == "📋 Мої записи")
async def my_bookings(message: Message):
    if is_barber(message.from_user.id):
        return

    cursor.execute("""
        SELECT
            b.id,
            wd.date,
            wt.time,
            b.service,
            b.status
        FROM bookings b
        JOIN work_days wd ON b.day_id = wd.id
        JOIN work_times wt ON b.time_id = wt.id
        WHERE b.user_id = ?
        AND b.status IN ('pending', 'confirmed')
        ORDER BY wd.date, wt.time
    """, (message.from_user.id,))

    bookings = cursor.fetchall()

    if not bookings:
        await message.answer(
            "📋 У вас немає активних записів.",
            reply_markup=client_menu()
        )
        return

    text = "📋 Ваші записи:\n\n"

    for booking_id, date_text, time_text, service, status in bookings:
        status_text = (
            "⏳ Очікує підтвердження"
            if status == "pending"
            else "✅ Підтверджено"
        )

        text += (
            f"📅 Дата: {format_date(date_text)}\n"
            f"⏰ Час: {time_text}\n"
            f"✂️ Послуга: {service}\n"
            f"{status_text}\n"
            f"────────────\n"
        )

    await message.answer(
        text,
        reply_markup=client_menu()
    )

    for booking_id, date_text, time_text, service, status in bookings:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Скасувати запис",
                        callback_data=f"cancel:{booking_id}"
                    )
                ]
            ]
        )

        await message.answer(
            f"📅 {format_date(date_text)} — {time_text}\n"
            f"✂️ {service}\n"
            f"{'⏳ Очікує підтвердження' if status == 'pending' else '✅ Підтверджено'}",
            reply_markup=keyboard
        )


# =========================================================
# СКАСУВАННЯ ЗАПИСУ
# =========================================================

@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_booking(callback: CallbackQuery):
    booking_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT user_id
        FROM bookings
        WHERE id = ?
        AND status IN ('pending', 'confirmed')
    """, (booking_id,))

    result = cursor.fetchone()

    if not result:
        await callback.answer(
            "Запис уже скасований або не знайдений.",
            show_alert=True
        )
        return

    user_id = result[0]

    if user_id != callback.from_user.id:
        await callback.answer(
            "Це не ваш запис.",
            show_alert=True
        )
        return

    cursor.execute("""
        UPDATE bookings
        SET status = 'cancelled'
        WHERE id = ?
    """, (booking_id,))

    cooldown_until = datetime.now() + timedelta(minutes=1)

    cursor.execute("""
        INSERT INTO cooldowns (user_id, until_time)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET until_time = excluded.until_time
    """, (
        user_id,
        cooldown_until.isoformat()
    ))

    db.commit()

    await callback.message.edit_text(
        "❌ Запис скасовано.\n\n"
        "⏳ Протягом 1 хвилини новий запис створити не можна."
    )

    await callback.answer("Запис скасовано.")


# =========================================================
# НОВІ ЗАПИСИ ДЛЯ БАРБЕРА
# =========================================================

@dp.message(F.text == "📥 Нові записи")
async def new_bookings(message: Message):
    if not is_barber(message.from_user.id):
        return

    cursor.execute("""
        SELECT
            b.id,
            wd.date,
            wt.time,
            b.service,
            b.phone,
            b.status
        FROM bookings b
        JOIN work_days wd ON b.day_id = wd.id
        JOIN work_times wt ON b.time_id = wt.id
        WHERE b.status IN ('pending', 'confirmed')
        ORDER BY wd.date, wt.time
    """)

    bookings = cursor.fetchall()

    if not bookings:
        await message.answer(
            "📥 Активних записів немає.",
            reply_markup=barber_menu()
        )
        return

    await message.answer("📥 Активні записи:")

    for booking_id, date_text, time_text, service, phone, status in bookings:
        if status == "pending":
            status_text = "⏳ Очікує підтвердження"

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Прийняти",
                            callback_data=f"accept:{booking_id}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Відхилити",
                            callback_data=f"reject:{booking_id}"
                        )
                    ]
                ]
            )
        else:
            status_text = "✅ Підтверджено"
            keyboard = None

        await message.answer(
            f"📅 {format_date(date_text)}\n"
            f"⏰ {time_text}\n"
            f"✂️ {service}\n"
            f"📱 {phone}\n"
            f"{status_text}",
            reply_markup=keyboard
        )


# =========================================================
# СТВОРИТИ РОБОЧИЙ ДЕНЬ
# =========================================================

@dp.message(F.text == "📅 Створити робочий день")
async def create_work_day_start(message: Message, state: FSMContext):
    if not is_barber(message.from_user.id):
        return

    await state.set_state(CreateDay.waiting_date)

    await message.answer(
        "📅 Введіть дату.\n\n"
        "Наприклад: 20.09\n"
        "20 — день\n"
        "09 — місяць",
        reply_markup=back_keyboard()
    )


@dp.message(CreateDay.waiting_date)
async def create_work_day(message: Message, state: FSMContext):

    text = message.text.strip()

    try:
        date_obj = datetime.strptime(
            f"{text}.{datetime.now().year}",
            "%d.%m.%Y"
        )
        date_text = date_obj.strftime("%Y-%m-%d")

    except ValueError:

        await message.answer(
            "❌ Неправильний формат.\n"
            "Введіть, наприклад: 20.09",
            reply_markup=back_keyboard()
        )

        return

    if get_day(date_text):

        await message.answer(
            "⚠️ Такий робочий день уже існує.\n\n"
            "📅 Введіть іншу дату:",
            reply_markup=back_keyboard()
        )

        return

    cursor.execute(
        "INSERT INTO work_days (date) VALUES (?)",
        (date_text,)
    )

    db.commit()

    await state.set_state(CreateDay.waiting_date)

    await message.answer(
        f"✅ Робочий день {text} створено.\n\n"
        "📅 Введіть наступну дату:",
        reply_markup=back_keyboard()
    )
    

# =========================================================
# СТВОРИТИ ЧАС
# =========================================================

@dp.message(F.text == "⏰ Створити час")
async def create_time_start(message: Message, state: FSMContext):
    if not is_barber(message.from_user.id):
        return

    await state.set_state(CreateTime.waiting_date)

    await message.answer(
        "📅 Введіть дату, до якої хочете додати час.\n\n"
        "Наприклад: 20.09",
        reply_markup=back_keyboard()
    )


@dp.message(CreateTime.waiting_date)
async def create_time_date(message: Message, state: FSMContext):

    text = message.text.strip()

    try:
        date_obj = datetime.strptime(
            f"{text}.{datetime.now().year}",
            "%d.%m.%Y"
        )
        date_text = date_obj.strftime("%Y-%m-%d")

    except ValueError:
        await message.answer(
            "❌ Неправильний формат.\n"
            "Введіть, наприклад: 20.09",
            reply_markup=back_keyboard()
        )
        return

    day_id = get_day_id(date_text)

    if not day_id:
        await message.answer(
            "❌ Цього робочого дня немає.\n"
            "Спочатку створіть робочий день.",
            reply_markup=back_keyboard()
        )
        return

    await state.update_data(day_id=day_id)

    await state.set_state(CreateTime.waiting_time)

    await message.answer(
        "⏰ Введіть час.\n\n"
        "Наприклад: 11:30",
        reply_markup=back_keyboard()
    )


@dp.message(CreateTime.waiting_time)
async def create_time_value(message: Message, state: FSMContext):

    time_text = message.text.strip()

    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        await message.answer(
            "❌ Неправильний формат.\n"
            "Введіть, наприклад: 11:30",
            reply_markup=back_keyboard()
        )
        return

    data = await state.get_data()
    day_id = data.get("day_id")

    if not day_id:
        await message.answer(
            "❌ Помилка: дату не знайдено.\n"
            "Почніть створення часу ще раз.",
            reply_markup=barber_menu()
        )
        await state.clear()
        return

    cursor.execute("""
        SELECT id
        FROM work_times
        WHERE day_id = ? AND time = ?
    """, (day_id, time_text))

    if cursor.fetchone():
        await message.answer(
            f"⚠️ Час {time_text} вже створений для цієї дати.\n\n"
            "⏰ Введіть інший час:",
            reply_markup=back_keyboard()
        )
        return

    cursor.execute("""
        INSERT INTO work_times
        (day_id, time, blocked)
        VALUES (?, ?, 0)
    """, (day_id, time_text))

    db.commit()

    await state.set_state(CreateTime.waiting_time)

    await message.answer(
        f"✅ Час {time_text} створено.\n\n"
        "⏰ Введіть наступний час:",
        reply_markup=back_keyboard()
    )

# =========================================================
# ЗАБЛОКУВАТИ ЧАС
# =========================================================

@dp.message(F.text == "🚫 Заблокувати час")
async def block_time_start(message: Message, state: FSMContext):
    if not is_barber(message.from_user.id):
        return

    await state.set_state(BlockTime.waiting_date)

    await message.answer(
        "🚫 Введіть дату, для якої хочете заблокувати час.\n\n"
        "Наприклад: 20.09",
        reply_markup=back_keyboard()
    )


@dp.message(BlockTime.waiting_date)
async def block_time_date(message: Message, state: FSMContext):
    text = message.text.strip()

    try:
        date_obj = datetime.strptime(
            f"{text}.{datetime.now().year}",
            "%d.%m.%Y"
        )
        date_text = date_obj.strftime("%Y-%m-%d")
    except ValueError:
        await message.answer(
            "❌ Неправильний формат дати.\n"
            "Введіть, наприклад: 20.09",
            reply_markup=back_keyboard()
        )
        return

    day_id = get_day_id(date_text)

    if not day_id:
        await message.answer(
            "❌ Такого робочого дня немає.\n\n"
            "Введіть іншу дату:",
            reply_markup=back_keyboard()
        )
        return

    times = get_all_times(day_id)

    if not times:
        await message.answer(
            "❌ Для цієї дати ще не створено жодного часу.",
            reply_markup=back_keyboard()
        )
        return

    await state.update_data(day_id=day_id)
    await state.set_state(BlockTime.waiting_time)

    text_answer = "⏰ Робочі часи на цю дату:\n\n"

    for time_id, time_text, blocked in times:
        if has_active_booking(time_id):
            status = "👤 є запис"
        elif blocked:
            status = "🚫 заблокований"
        else:
            status = "🟢 доступний"

        text_answer += f"• {time_text} — {status}\n"

    text_answer += "\nВведіть час, який потрібно заблокувати:"

    await message.answer(
        text_answer,
        reply_markup=back_keyboard()
    )


@dp.message(BlockTime.waiting_time)
async def block_time_value(message: Message, state: FSMContext):
    time_text = message.text.strip()

    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        await message.answer(
            "❌ Неправильний формат часу.\n"
            "Введіть, наприклад: 11:30",
            reply_markup=back_keyboard()
        )
        return

    data = await state.get_data()
    day_id = data.get("day_id")

    if not day_id:
        await message.answer(
            "❌ Помилка: дату не знайдено.",
            reply_markup=barber_menu()
        )
        await state.clear()
        return

    cursor.execute("""
        SELECT id, blocked
        FROM work_times
        WHERE day_id = ? AND time = ?
    """, (day_id, time_text))

    result = cursor.fetchone()

    if not result:
        await message.answer(
            "❌ Такого часу для цієї дати немає.\n\n"
            "Введіть час зі списку:",
            reply_markup=back_keyboard()
        )
        return

    time_id, blocked = result

    if has_active_booking(time_id):
        await message.answer(
            f"⚠️ Час {time_text} не можна заблокувати.\n\n"
            "На цей час уже є активний запис.",
            reply_markup=back_keyboard()
        )
        return

    if blocked:
        await message.answer(
            f"⚠️ Час {time_text} вже заблокований.\n\n"
            "Введіть інший час:",
            reply_markup=back_keyboard()
        )
        return

    cursor.execute(
        "UPDATE work_times SET blocked = 1 WHERE id = ?",
        (time_id,)
    )
    db.commit()

    await state.set_state(BlockTime.waiting_time)

    await message.answer(
        f"🚫 Час {time_text} заблоковано.\n\n"
        "⏰ Введіть наступний час для блокування:",
        reply_markup=back_keyboard()
    )


# =========================================================
# РОЗБЛОКУВАТИ ЧАС
# =========================================================

@dp.message(F.text == "🔓 Розблокувати час")
async def unblock_time_start(message: Message, state: FSMContext):
    if not is_barber(message.from_user.id):
        return

    await state.set_state(UnblockTime.waiting_date)

    await message.answer(
        "🔓 Введіть дату, для якої хочете розблокувати час.\n\n"
        "Наприклад: 20.09",
        reply_markup=back_keyboard()
    )


@dp.message(UnblockTime.waiting_date)
async def unblock_time_date(message: Message, state: FSMContext):
    text = message.text.strip()

    try:
        date_obj = datetime.strptime(
            f"{text}.{datetime.now().year}",
            "%d.%m.%Y"
        )
        date_text = date_obj.strftime("%Y-%m-%d")
    except ValueError:
        await message.answer(
            "❌ Неправильний формат дати.\n"
            "Введіть, наприклад: 20.09",
            reply_markup=back_keyboard()
        )
        return

    day_id = get_day_id(date_text)

    if not day_id:
        await message.answer(
            "❌ Такого робочого дня немає.\n\n"
            "Введіть іншу дату:",
            reply_markup=back_keyboard()
        )
        return

    times = get_all_times(day_id)

    if not times:
        await message.answer(
            "❌ Для цієї дати ще не створено жодного часу.",
            reply_markup=back_keyboard()
        )
        return

    await state.update_data(day_id=day_id)
    await state.set_state(UnblockTime.waiting_time)

    text_answer = "⏰ Робочі часи на цю дату:\n\n"

    for time_id, time_text, blocked in times:
        if has_active_booking(time_id):
            status = "👤 є запис"
        elif blocked:
            status = "🚫 заблокований"
        else:
            status = "🟢 доступний"

        text_answer += f"• {time_text} — {status}\n"

    text_answer += "\nВведіть час, який потрібно розблокувати:"

    await message.answer(
        text_answer,
        reply_markup=back_keyboard()
    )


@dp.message(UnblockTime.waiting_time)
async def unblock_time_value(message: Message, state: FSMContext):
    time_text = message.text.strip()

    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        await message.answer(
            "❌ Неправильний формат часу.\n"
            "Введіть, наприклад: 11:30",
            reply_markup=back_keyboard()
        )
        return

    data = await state.get_data()
    day_id = data.get("day_id")

    if not day_id:
        await message.answer(
            "❌ Помилка: дату не знайдено.",
            reply_markup=barber_menu()
        )
        await state.clear()
        return

    cursor.execute("""
        SELECT id, blocked
        FROM work_times
        WHERE day_id = ? AND time = ?
    """, (day_id, time_text))

    result = cursor.fetchone()

    if not result:
        await message.answer(
            "❌ Такого часу для цієї дати немає.\n\n"
            "Введіть час зі списку:",
            reply_markup=back_keyboard()
        )
        return

    time_id, blocked = result

    if not blocked:
        await message.answer(
            f"⚠️ Час {time_text} вже доступний.\n\n"
            "Введіть інший час:",
            reply_markup=back_keyboard()
        )
        return

    await state.set_state(UnblockTime.waiting_time)

    cursor.execute(
        "UPDATE work_times SET blocked = 0 WHERE id = ?",
        (time_id,)
    )
    db.commit()

    await message.answer(
        f"🔓 Час {time_text} розблоковано.\n\n"
        "⏰ Введіть наступний час для розблокування:",
        reply_markup=back_keyboard()
    )


# =========================================================
# ДУБЛЮВАТИ РОБОЧІ ДНІ
# =========================================================

@dp.message(F.text == "📋 Дублювати робочі дні")
async def duplicate_month(message: Message):
    if not is_barber(message.from_user.id):
        return

    now = datetime.now()

    source_year = now.year
    source_month = now.month

    if source_month == 12:
        target_year = source_year + 1
        target_month = 1
    else:
        target_year = source_year
        target_month = source_month + 1

    cursor.execute("""
        SELECT id, date
        FROM work_days
        WHERE date LIKE ?
        ORDER BY date
    """, (
        f"{source_year:04d}-{source_month:02d}-%",
    ))

    source_days = cursor.fetchall()

    if not source_days:
        await message.answer(
            "❌ У поточному місяці немає створених робочих днів.",
            reply_markup=barber_menu()
        )
        return

    days_in_target = monthrange(
        target_year,
        target_month
    )[1]

    copied_days = 0
    copied_times = 0

    for source_day_id, source_date in source_days:

        day_number = int(source_date.split("-")[2])

        # Якщо такого дня немає в наступному місяці
        if day_number > days_in_target:
            continue

        target_date = (
            f"{target_year:04d}-"
            f"{target_month:02d}-"
            f"{day_number:02d}"
        )

        cursor.execute("""
            INSERT OR IGNORE INTO work_days (date)
            VALUES (?)
        """, (target_date,))

        cursor.execute(
            "SELECT id FROM work_days WHERE date = ?",
            (target_date,)
        )

        target_day_id = cursor.fetchone()[0]

        cursor.execute("""
            SELECT time, blocked
            FROM work_times
            WHERE day_id = ?
        """, (source_day_id,))

        source_times = cursor.fetchall()

        for time_text, blocked in source_times:

            cursor.execute("""
                INSERT OR IGNORE INTO work_times
                (day_id, time, blocked)
                VALUES (?, ?, ?)
            """, (
                target_day_id,
                time_text,
                blocked
            ))

            copied_times += 1

        copied_days += 1

    db.commit()

    await message.answer(
        f"✅ Робочі дні продубльовано!\n\n"
        f"📅 Створено/перевірено днів: {copied_days}\n"
        f"⏰ Скопійовано часів: {copied_times}\n\n"
        f"Наступний місяць: "
        f"{target_month:02d}.{target_year}",
        reply_markup=barber_menu()
    )
    
    
    # =========================================================
# ПЕРЕГЛЯНУТИ ВСІ СТВОРЕНІ ДАТИ
# =========================================================

@dp.message(F.text == "📅 Переглянути дати")
async def view_work_days(message: Message):
    if not is_barber(message.from_user.id):
        return

    cursor.execute("""
        SELECT date
        FROM work_days
        ORDER BY date
    """)

    days = cursor.fetchall()

    if not days:
        await message.answer(
            "📅 Створених робочих дат поки немає."
        )
        return

    text = "📅 Усі створені робочі дати:\n\n"

    for (date_text,) in days:
        text += f"• {format_date(date_text)}\n"

    await message.answer(text)


# =========================================================
# ВИДАЛИТИ РОБОЧУ ДАТУ
# =========================================================

class DeleteDay(StatesGroup):
    waiting_date = State()


@dp.message(F.text == "🗑 Видалити робочу дату")
async def delete_work_day_start(message: Message, state: FSMContext):
    if not is_barber(message.from_user.id):
        return

    await state.set_state(DeleteDay.waiting_date)

    await message.answer(
        "🗑 Введіть дату, яку потрібно видалити.\n\n"
        "Наприклад: 20.09",
        reply_markup=back_keyboard()
    )


@dp.message(DeleteDay.waiting_date)
async def delete_work_day(message: Message, state: FSMContext):
    text = message.text.strip()

    try:
        date_obj = datetime.strptime(
            f"{text}.{datetime.now().year}",
            "%d.%m.%Y"
        )
        date_text = date_obj.strftime("%Y-%m-%d")
    except ValueError:
        await message.answer(
            "❌ Неправильний формат.\n"
            "Введіть, наприклад: 20.09",
            reply_markup=back_keyboard()
        )
        return

    day_id = get_day_id(date_text)

    if not day_id:
        await message.answer(
            "❌ Такої робочої дати немає.\n\n"
            "Введіть іншу дату:",
            reply_markup=back_keyboard()
        )
        return

    if has_active_bookings_for_day(day_id):
        await message.answer(
            f"❌ Дату {text} не можна видалити.\n\n"
            "На цей день уже є запис клієнта.",
            reply_markup=back_keyboard()
        )
        return

    cursor.execute(
        "DELETE FROM work_times WHERE day_id = ?",
        (day_id,)
    )

    cursor.execute(
        "DELETE FROM work_days WHERE id = ?",
        (day_id,)
    )

    db.commit()

    await state.set_state(DeleteDay.waiting_date)

    await message.answer(
        f"✅ Робочу дату {text} видалено.\n\n"
        "🗑 Введіть наступну дату для видалення:",
        reply_markup=back_keyboard()
    )


# =========================================================
# ЗАПУСК
# =========================================================

async def main():
    print("zapysbarber запущено!")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())