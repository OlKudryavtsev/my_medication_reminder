from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal, User, Schedule, Medicine
from .service import (
    seed_default_schedule,
    ensure_events,
    get_today_events,
    mark_taken,
    format_today,
    event_title,
    thanks_text,
)

settings = get_settings()
bot = Bot(settings.bot_token)
dp = Dispatcher()
TZ = ZoneInfo(settings.timezone)


def role_for(tg_id: int) -> str:
    if settings.child and tg_id == settings.child:
        return "child"
    if tg_id in settings.parents:
        return "parent"
    return "unknown"


async def upsert_user(message: Message) -> User:
    async with SessionLocal() as session:
        tg_id = message.from_user.id
        user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        full = message.from_user.full_name or ""
        if not user:
            user = User(tg_id=tg_id, full_name=full, role=role_for(tg_id))
            session.add(user)
        else:
            user.full_name = full
            if user.role == "unknown":
                user.role = role_for(tg_id)
        await session.commit()
        return user


def take_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выпил(а)", callback_data=f"take:{event_id}"),
        InlineKeyboardButton(text="📋 Сегодня", callback_data="today"),
    ]])


def app_keyboard() -> InlineKeyboardMarkup | None:
    if not settings.app_base_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="💊 Открыть мини-приложение", web_app=WebAppInfo(url=f"{settings.app_base_url.rstrip('/')}/app")
    )]])


@dp.message(Command("start"))
async def start(message: Message) -> None:
    user = await upsert_user(message)
    async with SessionLocal() as session:
        await seed_default_schedule(session)
        await ensure_events(session)
    text = (
        "Привет! Я семейный бот-напоминалка по лекарствам 💊\n\n"
        f"Твой Telegram ID: `{message.from_user.id}`\n"
        f"Роль сейчас: `{user.role}`\n\n"
        "Команды:\n"
        "/today — расписание и отметки на сегодня\n"
        "/app — открыть мини-приложение\n"
        "/add Название | Доза | HH:MM | Комментарий — добавить прием\n"
        "/seed_schedule — заново создать стартовое расписание из ТЗ\n"
        "/help — помощь"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=app_keyboard())


@dp.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(
        "Как пользоваться:\n"
        "1) Родители и ребенок отправляют /start боту.\n"
        "2) В Railway в переменные PARENT_CHAT_IDS и CHILD_CHAT_ID внесите нужные ID.\n"
        "3) Бот будет напоминать ребенку и родителям каждые N минут, пока прием не отмечен.\n"
        "4) Отметить прием можно кнопкой в Telegram или в мини-приложении.\n\n"
        "Добавить прием: /add Аквадетрим | 2 капли | 09:00 | после завтрака"
    )


@dp.message(Command("today"))
async def today(message: Message) -> None:
    await upsert_user(message)
    async with SessionLocal() as session:
        await ensure_events(session)
        events = await get_today_events(session)
    await message.answer(format_today(events), reply_markup=app_keyboard())


@dp.callback_query(F.data == "today")
async def today_cb(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        await ensure_events(session)
        events = await get_today_events(session)
    await callback.message.answer(format_today(events), reply_markup=app_keyboard())
    await callback.answer()


@dp.message(Command("app"))
async def app_cmd(message: Message) -> None:
    kb = app_keyboard()
    if not kb:
        await message.answer("Мини-приложение включится после заполнения APP_BASE_URL в Railway.")
        return
    await message.answer("Открывай семейную аптечку 👇", reply_markup=kb)


@dp.message(Command("seed_schedule"))
async def seed(message: Message) -> None:
    if message.from_user.id not in settings.parents and settings.parents:
        await message.answer("Эта команда доступна только родителям из PARENT_CHAT_IDS.")
        return
    async with SessionLocal() as session:
        await seed_default_schedule(session, replace=True)
        await ensure_events(session)
    await message.answer("Стартовое расписание пересоздано ✅")


@dp.message(Command("add"))
async def add(message: Message) -> None:
    if message.from_user.id not in settings.parents and settings.parents:
        await message.answer("Добавлять приемы могут только родители из PARENT_CHAT_IDS.")
        return
    raw = message.text.replace("/add", "", 1).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        await message.answer("Формат: /add Название | Доза | HH:MM | Комментарий")
        return
    name, dose, hhmm = parts[:3]
    label = parts[3] if len(parts) > 3 else hhmm
    async with SessionLocal() as session:
        med = (await session.execute(select(Medicine).where(Medicine.name == name))).scalar_one_or_none()
        if not med:
            med = Medicine(name=name, default_dose=dose)
            session.add(med)
            await session.flush()
        item = Schedule(medicine_id=med.id, dose=dose, time_local=hhmm, label=label)
        session.add(item)
        await session.commit()
        await ensure_events(session)
    await message.answer(f"Добавил: {name} — {dose} в {hhmm} ✅")


@dp.callback_query(F.data.startswith("take:"))
async def take(callback: CallbackQuery) -> None:
    event_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        event = await mark_taken(session, event_id, callback.from_user.id)
    if not event:
        await callback.answer("Не нашел прием", show_alert=True)
        return
    thanks = thanks_text()
    await callback.message.answer(thanks)
    who = callback.from_user.full_name or str(callback.from_user.id)
    notify = f"✅ {who} отметил прием: {event_title(event)}\nВремя отметки: {datetime.now(TZ).strftime('%H:%M')}"
    for parent_id in settings.parents:
        if parent_id != callback.from_user.id:
            try:
                await bot.send_message(parent_id, notify)
            except Exception:
                pass
    if settings.child and settings.child != callback.from_user.id:
        try:
            await bot.send_message(settings.child, thanks)
        except Exception:
            pass
    await callback.answer("Отмечено!")
