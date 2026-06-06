from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal, User, Schedule, Medicine
from .service import (
    seed_default_schedule,
    ensure_events,
    get_today_events,
    mark_taken,
    mark_skipped,
    snooze_event,
    update_taken_time,
    format_today,
    event_title,
    thanks_text,
    skip_text,
    get_event,
    get_stats,
    get_history_for_medicine,
    parse_date_or_none,
)

settings = get_settings()
bot = Bot(settings.bot_token)
dp = Dispatcher()
TZ = ZoneInfo(settings.timezone)


class TakeStates(StatesGroup):
    waiting_actual_time = State()


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
        role = role_for(tg_id)
        if not user:
            user = User(tg_id=tg_id, full_name=full, role=role)
            session.add(user)
        else:
            user.full_name = full
            user.role = role
        await session.commit()
        return user


def is_known(tg_id: int) -> bool:
    return role_for(tg_id) in {"parent", "child"}


def is_parent(tg_id: int) -> bool:
    return role_for(tg_id) == "parent"


async def deny_unknown_message(message: Message) -> bool:
    if is_known(message.from_user.id):
        return False
    await message.answer(
        "⛔ Доступ закрыт. Ваш Telegram ID не указан ни как CHILD_CHAT_ID, ни в PARENT_CHAT_IDS.\n\n"
        f"Ваш ID: `{message.from_user.id}`\n"
        "Передайте его родителю/администратору и добавьте в Railway Variables.",
        parse_mode="Markdown",
    )
    return True


async def deny_unknown_callback(callback: CallbackQuery) -> bool:
    if is_known(callback.from_user.id):
        return False
    await callback.answer("Доступ закрыт: вы не указаны в настройках семьи.", show_alert=True)
    return True


def take_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Выпил(а)", callback_data=f"takeask:{event_id}"),
            InlineKeyboardButton(text="😴 Отложить 30 мин", callback_data=f"snooze:{event_id}"),
        ],
        [
            InlineKeyboardButton(text="⏭️ Пропущено", callback_data=f"skip:{event_id}"),
            InlineKeyboardButton(text="📋 Сегодня", callback_data="today"),
        ],
    ])


def choose_time_keyboard(event_id: int, scheduled_hhmm: str) -> InlineKeyboardMarkup:
    now_hhmm = datetime.now(TZ).strftime("%H:%M")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Сейчас: {now_hhmm}", callback_data=f"take:{event_id}:now")],
        [InlineKeyboardButton(text=f"По расписанию: {scheduled_hhmm}", callback_data=f"take:{event_id}:scheduled")],
        [InlineKeyboardButton(text="✍️ Ввести время", callback_data=f"takemanual:{event_id}")],
    ])


def app_keyboard(text: str = "💊 Открыть мини-приложение") -> InlineKeyboardMarkup | None:
    if not settings.app_base_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=text, web_app=WebAppInfo(url=f"{settings.app_base_url.rstrip('/')}/app")
    )]])


def admin_keyboard() -> InlineKeyboardMarkup | None:
    if not settings.app_base_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⚙️ Открыть администрирование", web_app=WebAppInfo(url=f"{settings.app_base_url.rstrip('/')}/app#admin")
    )]])


@dp.message(Command("start"))
async def start(message: Message) -> None:
    user = await upsert_user(message)
    if user.role == "unknown":
        await message.answer(
            "Привет! Я семейный бот-напоминалка по лекарствам 💊\n\n"
            f"Ваш Telegram ID: `{message.from_user.id}`\n"
            "Роль сейчас: `unknown`\n\n"
            "⛔ Функции недоступны, пока ваш ID не добавлен в CHILD_CHAT_ID или PARENT_CHAT_IDS в Railway.",
            parse_mode="Markdown",
        )
        return
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
        "/stats — статистика за 30 дней\n"
        "/history — история по препаратам\n"
        "/admin — администрирование расписания для родителей\n"
        "/add Название | Доза | HH:MM | Комментарий | YYYY-MM-DD | YYYY-MM-DD — добавить прием\n"
        "/seed_schedule — заново создать стартовое расписание из ТЗ\n"
        "/help — помощь"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=app_keyboard())


@dp.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await upsert_user(message)
    if await deny_unknown_message(message):
        return
    await message.answer(
        "Как пользоваться:\n"
        "1) Родители и ребенок отправляют /start боту.\n"
        "2) В Railway в переменные PARENT_CHAT_IDS и CHILD_CHAT_ID внесите нужные ID.\n"
        "3) Бот будет напоминать ребенку и родителям, пока прием не отмечен, не отложен или не пропущен.\n"
        "4) В напоминании есть кнопки: Выпил(а), Отложить 30 мин, Пропущено.\n"
        "5) При отметке 'Выпил(а)' можно выбрать фактическое время: сейчас, по расписанию или ввести вручную.\n\n"
        "Добавить прием родителю: /add Аквадетрим | 2 капли | 09:00 | после завтрака | 2026-06-06 | 2026-06-20"
    )


@dp.message(Command("today"))
async def today(message: Message) -> None:
    await upsert_user(message)
    if await deny_unknown_message(message):
        return
    async with SessionLocal() as session:
        await ensure_events(session)
        events = await get_today_events(session)
    await message.answer(format_today(events), reply_markup=app_keyboard())


@dp.callback_query(F.data == "today")
async def today_cb(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    async with SessionLocal() as session:
        await ensure_events(session)
        events = await get_today_events(session)
    # В уведомлениях у родителей callback.message может быть неудачной точкой ответа,
    # поэтому отправляем расписание напрямую тому, кто нажал кнопку.
    await bot.send_message(callback.from_user.id, format_today(events), reply_markup=app_keyboard())
    await callback.answer("Отправил расписание на сегодня")


@dp.message(Command("app"))
async def app_cmd(message: Message) -> None:
    await upsert_user(message)
    if await deny_unknown_message(message):
        return
    kb = app_keyboard()
    if not kb:
        await message.answer("Мини-приложение включится после заполнения APP_BASE_URL в Railway.")
        return
    await message.answer("Открывай семейную аптечку 👇", reply_markup=kb)


@dp.message(Command("admin"))
async def admin_cmd(message: Message) -> None:
    await upsert_user(message)
    if await deny_unknown_message(message):
        return
    if not is_parent(message.from_user.id):
        await message.answer("⛔ Администрирование доступно только родителям.")
        return
    await message.answer("⚙️ Раздел администрирования расписания для родителей.", reply_markup=admin_keyboard())


@dp.message(Command("seed_schedule"))
async def seed(message: Message) -> None:
    await upsert_user(message)
    if await deny_unknown_message(message):
        return
    if not is_parent(message.from_user.id):
        await message.answer("Эта команда доступна только родителям.")
        return
    async with SessionLocal() as session:
        await seed_default_schedule(session, replace=True)
        await ensure_events(session)
    await message.answer("Стартовое расписание пересоздано без Фитомуцила ✅")


@dp.message(Command("add"))
async def add(message: Message) -> None:
    await upsert_user(message)
    if await deny_unknown_message(message):
        return
    if not is_parent(message.from_user.id):
        await message.answer("Добавлять приемы могут только родители.")
        return
    raw = message.text.replace("/add", "", 1).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        await message.answer("Формат: /add Название | Доза | HH:MM | Комментарий | YYYY-MM-DD | YYYY-MM-DD")
        return
    name, dose, hhmm = parts[:3]
    label = parts[3] if len(parts) > 3 else hhmm
    start_date = parse_date_or_none(parts[4]) if len(parts) > 4 else None
    end_date = parse_date_or_none(parts[5]) if len(parts) > 5 else None
    async with SessionLocal() as session:
        med = (await session.execute(select(Medicine).where(Medicine.name == name))).scalar_one_or_none()
        if not med:
            med = Medicine(name=name, default_dose=dose)
            session.add(med)
            await session.flush()
        item = Schedule(medicine_id=med.id, dose=dose, time_local=hhmm, label=label, start_date=start_date, end_date=end_date)
        session.add(item)
        await session.commit()
        await ensure_events(session)
    await message.answer(f"Добавил: {name} — {dose} в {hhmm} ✅")


@dp.callback_query(F.data.startswith("takeask:"))
async def take_ask(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    event_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        event = await get_event(session, event_id)
    if not event:
        await callback.answer("Не нашел прием", show_alert=True)
        return
    scheduled = event.due_at.astimezone(TZ).strftime("%H:%M")
    await callback.message.answer(
        f"Когда фактически принято?\n{event_title(event)}",
        reply_markup=choose_time_keyboard(event_id, scheduled),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("take:"))
async def take(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    _, event_id_raw, mode = callback.data.split(":", 2)
    event_id = int(event_id_raw)
    actual_time = None
    async with SessionLocal() as session:
        event_before = await get_event(session, event_id)
        if not event_before:
            await callback.answer("Не нашел прием", show_alert=True)
            return
        if mode == "scheduled":
            actual_time = event_before.due_at.astimezone(TZ).strftime("%H:%M")
        event = await mark_taken(session, event_id, callback.from_user.id, actual_time=actual_time)
    if not event:
        await callback.answer("Не нашел прием", show_alert=True)
        return
    thanks = thanks_text(event.id)
    edit_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ Изменить время приема", callback_data=f"takemanual:{event.id}")]])
    await callback.message.answer(thanks, reply_markup=edit_kb)
    who = callback.from_user.full_name or str(callback.from_user.id)
    actual = event.taken_at.astimezone(TZ).strftime("%H:%M") if event.taken_at else datetime.now(TZ).strftime("%H:%M")
    notify = f"✅ {who} отметил прием: {event_title(event)}\nФактическое время: {actual}"
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


@dp.callback_query(F.data.startswith("takemanual:"))
async def take_manual(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_unknown_callback(callback):
        return
    event_id = int(callback.data.split(":", 1)[1])
    await state.set_state(TakeStates.waiting_actual_time)
    await state.update_data(event_id=event_id)
    await callback.message.answer("Введите фактическое время приема в формате HH:MM, например 08:17")
    await callback.answer()


@dp.message(TakeStates.waiting_actual_time)
async def take_manual_time(message: Message, state: FSMContext) -> None:
    await upsert_user(message)
    if await deny_unknown_message(message):
        await state.clear()
        return
    hhmm = message.text.strip()
    try:
        h, m = hhmm.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError
    except Exception:
        await message.answer("Нужен формат HH:MM, например 08:17")
        return
    data = await state.get_data()
    event_id = int(data["event_id"])
    async with SessionLocal() as session:
        event = await mark_taken(session, event_id, message.from_user.id, actual_time=hhmm)
    await state.clear()
    if not event:
        await message.answer("Не нашел прием.")
        return
    thanks = thanks_text(event.id)
    edit_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ Изменить время приема", callback_data=f"takemanual:{event.id}")]])
    await message.answer(thanks, reply_markup=edit_kb)
    who = message.from_user.full_name or str(message.from_user.id)
    notify = f"✅ {who} отметил прием: {event_title(event)}\nФактическое время: {hhmm}"
    for parent_id in settings.parents:
        if parent_id != message.from_user.id:
            try:
                await bot.send_message(parent_id, notify)
            except Exception:
                pass


@dp.callback_query(F.data.startswith("skip:"))
async def skip(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    event_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        event = await mark_skipped(session, event_id, callback.from_user.id)
    if not event:
        await callback.answer("Не нашел прием", show_alert=True)
        return
    text = skip_text(event.id)
    await callback.message.answer(text)
    who = callback.from_user.full_name or str(callback.from_user.id)
    notify = f"⏭️ {who} отметил пропуск: {event_title(event)}"
    for parent_id in settings.parents:
        if parent_id != callback.from_user.id:
            try:
                await bot.send_message(parent_id, notify)
            except Exception:
                pass
    await callback.answer("Отмечено как пропущено")


@dp.callback_query(F.data.startswith("snooze:"))
async def snooze(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    event_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        event = await snooze_event(session, event_id, callback.from_user.id, settings.snooze_minutes)
    if not event:
        await callback.answer("Не нашел прием", show_alert=True)
        return
    until = event.postponed_until.astimezone(TZ).strftime("%H:%M") if event.postponed_until else "позже"
    await callback.message.answer(f"😴 Отложено до {until}. Будильник ушел пить чай на {settings.snooze_minutes} минут.")
    await callback.answer("Отложено")


@dp.message(Command("stats"))
async def stats_cmd(message: Message) -> None:
    await upsert_user(message)
    if await deny_unknown_message(message):
        return
    async with SessionLocal() as session:
        rows = await get_stats(session, days=30)
    if not rows:
        await message.answer("Статистики пока нет.")
        return
    lines = ["📊 Статистика за 30 дней:"]
    for r in rows:
        lines.append(f"• {r['medicine']}: ✅ {r['taken']} / ⏭️ {r['skipped']} / ⏳ {r['pending']} из {r['total']} ({r['taken_percent']}%)")
    await message.answer("\n".join(lines))


@dp.message(Command("history"))
async def history_cmd(message: Message) -> None:
    await upsert_user(message)
    if await deny_unknown_message(message):
        return
    async with SessionLocal() as session:
        meds = (await session.execute(select(Medicine).where(Medicine.active == True).order_by(Medicine.name))).scalars().all()  # noqa: E712
    if not meds:
        await message.answer("Препаратов пока нет.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=m.name, callback_data=f"hist:{m.id}")] for m in meds[:50]
    ])
    await message.answer("Выберите препарат для истории за 30 дней:", reply_markup=kb)


@dp.callback_query(F.data.startswith("hist:"))
async def history_cb(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    medicine_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        events = await get_history_for_medicine(session, medicine_id, days=30)
    if not events:
        await callback.message.answer("Истории по препарату пока нет.")
        await callback.answer()
        return
    med_name = events[0].schedule.medicine.name
    lines = [f"🧾 История за 30 дней: {med_name}"]
    for e in events[:30]:
        due = e.due_at.astimezone(TZ).strftime("%d.%m %H:%M")
        if e.status == "taken":
            fact = e.taken_at.astimezone(TZ).strftime("%H:%M") if e.taken_at else "?"
            status = f"✅ принято {fact}"
        elif e.status == "skipped":
            status = "⏭️ пропущено"
        else:
            status = "⏳ ожидает"
        lines.append(f"• {due}: {status} — {e.schedule.dose}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()
