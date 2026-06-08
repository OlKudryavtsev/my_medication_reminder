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
from .db import SessionLocal, User, Schedule, Medicine, FamilyInvite, FamilyMember, Profile
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
    group_key_for_due,
    get_pending_group_by_key,
    mark_group_taken,
    mark_group_skipped,
    snooze_group,
    get_stats,
    get_history_for_medicine,
    parse_date_or_none,
    ensure_profiles,
    ensure_user_account,
    resolve_profile_id,
    profile_recipients,
    approve_user_access,
    reject_user_access,
    create_private_family_for_user,
)

settings = get_settings()
bot = Bot(settings.bot_token)
dp = Dispatcher()
TZ = ZoneInfo(settings.timezone)


class TakeStates(StatesGroup):
    waiting_actual_time = State()
    waiting_group_actual_time = State()


def role_for(tg_id: int) -> str:
    if settings.child and tg_id == settings.child:
        return "child"
    if tg_id in settings.parents:
        return "parent"
    return "pending"


async def upsert_user(message: Message) -> User:
    async with SessionLocal() as session:
        tg_id = message.from_user.id
        full = message.from_user.full_name or ""
        role = role_for(tg_id)
        user = await ensure_user_account(session, tg_id, full, role_hint=role)
        return user


def is_known(tg_id: int) -> bool:
    return role_for(tg_id) in {"parent", "child"}


def is_parent(tg_id: int) -> bool:
    return role_for(tg_id) == "parent"


async def runtime_role(tg_id: int) -> str:
    if settings.child and tg_id == settings.child:
        return "child"
    if tg_id in settings.parents:
        return "parent"
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        return (user.role if user else "pending") or "pending"


async def deny_unknown_message(message: Message) -> bool:
    role = await runtime_role(message.from_user.id)
    if role in {"parent", "child"}:
        return False
    await message.answer(
        "⏳ Ваша заявка на доступ ожидает подтверждения администратора. "
        "После подтверждения бот напишет вам, и можно будет открыть приложение."
    )
    return True


async def deny_unknown_callback(callback: CallbackQuery) -> bool:
    role = await runtime_role(callback.from_user.id)
    if role in {"parent", "child"}:
        return False
    await callback.answer("Доступ пока не подтвержден администратором", show_alert=True)
    return True


def access_request_keyboard(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"access_approve:{tg_id}"),
        InlineKeyboardButton(text="🚫 Отклонить", callback_data=f"access_reject:{tg_id}"),
    ]])


async def notify_access_request(user: User) -> None:
    if not settings.parents:
        return
    text = (
        "👤 Новая заявка на доступ к боту\n\n"
        f"Имя: {user.full_name or '—'}\n"
        f"Telegram ID: `{user.tg_id}`\n\n"
        "Подтвердить доступ? После подтверждения пользователю будет создана собственная семья и личный профиль."
    )
    for admin_id in settings.parents:
        try:
            await bot.send_message(admin_id, text, parse_mode="Markdown", reply_markup=access_request_keyboard(user.tg_id))
        except Exception:
            pass


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



def group_take_keyboard(group_key: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Выпили все", callback_data=f"takegrpask:{group_key}"),
            InlineKeyboardButton(text="😴 Отложить все 30 мин", callback_data=f"snoozegrp:{group_key}"),
        ],
        [
            InlineKeyboardButton(text="⏭️ Пропущено все", callback_data=f"skipgrp:{group_key}"),
            InlineKeyboardButton(text="📋 Сегодня", callback_data="today"),
        ],
    ])


def choose_group_time_keyboard(group_key: int, scheduled_hhmm: str) -> InlineKeyboardMarkup:
    now_hhmm = datetime.now(TZ).strftime("%H:%M")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Сейчас: {now_hhmm}", callback_data=f"takegrp:{group_key}:now")],
        [InlineKeyboardButton(text=f"По расписанию: {scheduled_hhmm}", callback_data=f"takegrp:{group_key}:scheduled")],
        [InlineKeyboardButton(text="✍️ Ввести время", callback_data=f"takemanualgrp:{group_key}")],
    ])

def choose_time_keyboard(event_id: int, scheduled_hhmm: str) -> InlineKeyboardMarkup:
    now_hhmm = datetime.now(TZ).strftime("%H:%M")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Сейчас: {now_hhmm}", callback_data=f"take:{event_id}:now")],
        [InlineKeyboardButton(text=f"По расписанию: {scheduled_hhmm}", callback_data=f"take:{event_id}:scheduled")],
        [InlineKeyboardButton(text="✍️ Ввести время", callback_data=f"takemanual:{event_id}")],
    ])


def app_keyboard(text: str = "💊 Открыть мини-приложение") -> InlineKeyboardMarkup | None:
    if not settings.app_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=text, web_app=WebAppInfo(url=settings.app_url)
    )]])


def admin_keyboard() -> InlineKeyboardMarkup | None:
    if not settings.admin_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⚙️ Открыть администрирование", web_app=WebAppInfo(url=settings.admin_url)
    )]])


@dp.message(Command("start"))
async def start(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    start_arg = parts[1].strip() if len(parts) > 1 else ""
    if start_arg.startswith("invite_"):
        token = start_arg[len("invite_"):].strip()
        async with SessionLocal() as session:
            tg_id = message.from_user.id
            full = message.from_user.full_name or ""
            user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
            if not user:
                user = User(tg_id=tg_id, full_name=full, role="pending")
                session.add(user)
                await session.flush()
            elif full:
                user.full_name = full
            inv = (await session.execute(select(FamilyInvite).where(FamilyInvite.token == token, FamilyInvite.active == True))).scalar_one_or_none()
            if not inv or inv.used_count >= inv.max_uses:
                await message.answer("Ссылка-приглашение недействительна или уже использована.")
                await session.commit()
                return
            member = (await session.execute(select(FamilyMember).where(FamilyMember.family_id == inv.family_id, FamilyMember.user_id == user.id))).scalar_one_or_none()
            if not member:
                member = FamilyMember(family_id=inv.family_id, user_id=user.id, role=inv.role, linked_profile_id=inv.target_profile_id, active=True)
                session.add(member)
            else:
                member.role = inv.role
                member.linked_profile_id = inv.target_profile_id
                member.active = True
            user.role = "child" if inv.role == "child" else "parent"
            if inv.role in {"parent", "owner"}:
                personal = (await session.execute(select(Profile).where(Profile.family_id == inv.family_id, Profile.kind == "personal", Profile.owner_tg_id == tg_id, Profile.active == True))).scalar_one_or_none()
                if not personal:
                    personal = Profile(name="Мой профиль", kind="personal", owner_tg_id=tg_id, family_id=inv.family_id, active=True)
                    session.add(personal)
                    await session.flush()
                user.active_profile_id = personal.id
            elif inv.target_profile_id:
                user.active_profile_id = inv.target_profile_id
            inv.used_count += 1
            if inv.used_count >= inv.max_uses:
                inv.active = False
            await session.commit()
        await message.answer("✅ Вы присоединились к семье. Откройте приложение командой /app.", reply_markup=app_keyboard())
        return

    user = await upsert_user(message)
    if user.role in {"pending", "unknown", "rejected"}:
        if user.role != "rejected":
            await notify_access_request(user)
            await message.answer(
                "Привет! Я семейный бот-напоминалка по лекарствам 💊\n\n"
                f"Ваш Telegram ID: `{message.from_user.id}`\n"
                "Статус: заявка на доступ отправлена администратору.\n\n"
                "После подтверждения вам будет создана собственная семья и личный профиль.",
                parse_mode="Markdown",
            )
        else:
            await message.answer(
                "⛔ Доступ к этому боту отклонен администратором. "
                "Если это ошибка, попросите администратора подтвердить доступ повторно."
            )
        return
    async with SessionLocal() as session:
        await ensure_profiles(session)
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




@dp.callback_query(F.data.startswith("access_approve:"))
async def access_approve(callback: CallbackQuery) -> None:
    if callback.from_user.id not in settings.parents:
        await callback.answer("Только администратор может подтверждать доступ", show_alert=True)
        return
    tg_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        user = await approve_user_access(session, tg_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"✅ Доступ подтвержден\n\nИмя: {user.full_name or '—'}\nTelegram ID: `{user.tg_id}`",
        parse_mode="Markdown",
    )
    try:
        await bot.send_message(
            user.tg_id,
            "✅ Доступ подтвержден! Вам создана собственная семья и личный профиль. Откройте приложение командой /app.",
            reply_markup=app_keyboard(),
        )
    except Exception:
        pass
    await callback.answer("Доступ подтвержден")


@dp.callback_query(F.data.startswith("access_reject:"))
async def access_reject(callback: CallbackQuery) -> None:
    if callback.from_user.id not in settings.parents:
        await callback.answer("Только администратор может отклонять доступ", show_alert=True)
        return
    tg_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        user = await reject_user_access(session, tg_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"🚫 Доступ отклонен\n\nИмя: {user.full_name or '—'}\nTelegram ID: `{user.tg_id}`",
        parse_mode="Markdown",
    )
    try:
        await bot.send_message(user.tg_id, "⛔ Администратор отклонил доступ к боту.")
    except Exception:
        pass
    await callback.answer("Доступ отклонен")


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
        await ensure_profiles(session)
        profile_id = await resolve_profile_id(session, message.from_user.id, role_for(message.from_user.id))
        await ensure_events(session)
        events = await get_today_events(session, profile_id=profile_id)
    await message.answer(format_today(events), reply_markup=app_keyboard())


@dp.callback_query(F.data == "today")
async def today_cb(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    async with SessionLocal() as session:
        await ensure_profiles(session)
        profile_id = await resolve_profile_id(session, callback.from_user.id, role_for(callback.from_user.id))
        await ensure_events(session)
        events = await get_today_events(session, profile_id=profile_id)
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
    await message.answer("⚙️ Раздел управления расписанием доступного профиля.", reply_markup=admin_keyboard())


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
        await ensure_profiles(session)
        profile_id = await resolve_profile_id(session, message.from_user.id, role_for(message.from_user.id))
        med = (await session.execute(select(Medicine).where(Medicine.name == name))).scalar_one_or_none()
        if not med:
            med = Medicine(name=name, default_dose=dose)
            session.add(med)
            await session.flush()
        item = Schedule(profile_id=profile_id, medicine_id=med.id, dose=dose, time_local=hhmm, label=label, start_date=start_date, end_date=end_date)
        session.add(item)
        await session.commit()
        await ensure_events(session)
    await message.answer(f"Добавил: {name} — {dose} в {hhmm} ✅")



@dp.callback_query(F.data.startswith("takegrpask:"))
async def take_group_ask(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    group_key = callback.data.split(":", 1)[1]
    async with SessionLocal() as session:
        events = await get_pending_group_by_key(session, group_key)
    if not events:
        await callback.answer("В этой группе уже нет непринятых приемов", show_alert=True)
        return
    scheduled = events[0].due_at.astimezone(TZ).strftime("%H:%M")
    meds = "\n".join(f"• {event_title(e)}" for e in events)
    await callback.message.answer(
        f"Когда фактически приняты лекарства?\n{meds}",
        reply_markup=choose_group_time_keyboard(group_key, scheduled),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("takegrp:"))
async def take_group(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    payload = callback.data.split(":", 1)[1]
    group_key, mode = payload.rsplit(":", 1)
    actual_time = None
    async with SessionLocal() as session:
        events_before = await get_pending_group_by_key(session, group_key)
        if not events_before:
            await callback.answer("В этой группе уже нет непринятых приемов", show_alert=True)
            return
        if mode == "scheduled":
            actual_time = events_before[0].due_at.astimezone(TZ).strftime("%H:%M")
        events = await mark_group_taken(session, group_key, callback.from_user.id, actual_time=actual_time)
    if not events:
        await callback.answer("В этой группе уже нет непринятых приемов", show_alert=True)
        return
    thanks = thanks_text(sum(e.id for e in events))
    actual = events[0].taken_at.astimezone(TZ).strftime("%H:%M") if events[0].taken_at else datetime.now(TZ).strftime("%H:%M")
    await callback.message.answer(f"{thanks}\n\n✅ Отмечено приемов: {len(events)}. Фактическое время: {actual}")
    who = callback.from_user.full_name or str(callback.from_user.id)
    meds = "\n".join(f"• {event_title(e)}" for e in events)
    notify = f"✅ {who} отметил групповой прием ({len(events)}):\n{meds}\nФактическое время: {actual}"
    async with SessionLocal() as session:
        recipients = await profile_recipients(session, events[0].schedule.profile_id)
    for parent_id in recipients:
        if parent_id != callback.from_user.id:
            try:
                await bot.send_message(parent_id, notify if parent_id in settings.parents else thanks)
            except Exception:
                pass
    await callback.answer("Группа отмечена")


@dp.callback_query(F.data.startswith("takemanualgrp:"))
async def take_group_manual(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_unknown_callback(callback):
        return
    group_key = callback.data.split(":", 1)[1]
    await state.set_state(TakeStates.waiting_group_actual_time)
    await state.update_data(group_key=group_key)
    await callback.message.answer("Введите фактическое время группового приема в формате HH:MM, например 08:17")
    await callback.answer()


@dp.message(TakeStates.waiting_group_actual_time)
async def take_group_manual_time(message: Message, state: FSMContext) -> None:
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
    group_key = data["group_key"]
    async with SessionLocal() as session:
        events = await mark_group_taken(session, group_key, message.from_user.id, actual_time=hhmm)
    await state.clear()
    if not events:
        await message.answer("В этой группе уже нет непринятых приемов.")
        return
    thanks = thanks_text(sum(e.id for e in events))
    await message.answer(f"{thanks}\n\n✅ Отмечено приемов: {len(events)}. Фактическое время: {hhmm}")
    who = message.from_user.full_name or str(message.from_user.id)
    meds = "\n".join(f"• {event_title(e)}" for e in events)
    notify = f"✅ {who} отметил групповой прием ({len(events)}):\n{meds}\nФактическое время: {hhmm}"
    async with SessionLocal() as session:
        recipients = await profile_recipients(session, events[0].schedule.profile_id)
    for parent_id in recipients:
        if parent_id != message.from_user.id:
            try:
                await bot.send_message(parent_id, notify)
            except Exception:
                pass


@dp.callback_query(F.data.startswith("skipgrp:"))
async def skip_group(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    group_key = callback.data.split(":", 1)[1]
    async with SessionLocal() as session:
        events = await mark_group_skipped(session, group_key, callback.from_user.id)
    if not events:
        await callback.answer("В этой группе уже нет непринятых приемов", show_alert=True)
        return
    text = skip_text(sum(e.id for e in events))
    await callback.message.answer(f"{text}\n\n⏭️ Пропущено приемов: {len(events)}")
    who = callback.from_user.full_name or str(callback.from_user.id)
    meds = "\n".join(f"• {event_title(e)}" for e in events)
    notify = f"⏭️ {who} отметил групповой пропуск ({len(events)}):\n{meds}"
    async with SessionLocal() as session:
        recipients = await profile_recipients(session, events[0].schedule.profile_id)
    for parent_id in recipients:
        if parent_id != callback.from_user.id:
            try:
                await bot.send_message(parent_id, notify)
            except Exception:
                pass
    await callback.answer("Группа отмечена как пропущенная")


@dp.callback_query(F.data.startswith("snoozegrp:"))
async def snooze_group_cb(callback: CallbackQuery) -> None:
    if await deny_unknown_callback(callback):
        return
    group_key = callback.data.split(":", 1)[1]
    async with SessionLocal() as session:
        events = await snooze_group(session, group_key, callback.from_user.id, settings.snooze_minutes)
    if not events:
        await callback.answer("В этой группе уже нет непринятых приемов", show_alert=True)
        return
    until = events[0].postponed_until.astimezone(TZ).strftime("%H:%M") if events[0].postponed_until else "позже"
    await callback.message.answer(f"😴 Отложено приемов: {len(events)} до {until}. Аптечный будильник ушел на паузу.")
    await callback.answer("Группа отложена")

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
    async with SessionLocal() as session:
        recipients = await profile_recipients(session, event.schedule.profile_id)
    for parent_id in recipients:
        if parent_id != callback.from_user.id:
            try:
                await bot.send_message(parent_id, notify if parent_id in settings.parents else thanks)
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
