from __future__ import annotations

import random
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import Medicine, Schedule, DoseEvent

settings = get_settings()
TZ = ZoneInfo(settings.timezone)

FUN_REMINDERS = [
    "🦸 Пора принять лекарство: {title}. Таблетки сами в рот не прыгают, им нужен герой!",
    "⏰ Медицинский будильник не дремлет: {title}. Выпил — нажми кнопку, и все выдохнут.",
    "🚀 Запуск миссии «здоровый живот»: {title}. Осталось подтвердить прием.",
    "🕵️ Напоминание-сыщик нашло непринятое лекарство: {title}. Не дай ему уйти в архив!",
    "🐢 Лекарство ждет уже как черепаха на старте: {title}. Догоним расписание?",
]

THANKS = [
    "💪 Отлично! Прием отмечен. Организм говорит: «Спасибо, капитан!»",
    "🌟 Готово! Еще один пункт лечения закрыт — красиво и уверенно.",
    "🏆 Принято! Маленькая победа в большом курсе лечения.",
    "👏 Спасибо, отметка сохранена. Родители тоже могут спокойно выдохнуть.",
]


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def local_dt(day: date, hhmm: str) -> datetime:
    t = parse_hhmm(hhmm)
    return datetime.combine(day, t, tzinfo=TZ)


def offset_hhmm(base: str, minutes: int) -> str:
    d = datetime.combine(date(2026, 1, 1), parse_hhmm(base)) + timedelta(minutes=minutes)
    return d.strftime("%H:%M")


async def upsert_medicine(session: AsyncSession, name: str, dose: str) -> Medicine:
    med = (await session.execute(select(Medicine).where(Medicine.name == name))).scalar_one_or_none()
    if med:
        med.default_dose = dose or med.default_dose
        med.active = True
        return med
    med = Medicine(name=name, default_dose=dose)
    session.add(med)
    await session.flush()
    return med


async def add_schedule(session: AsyncSession, name: str, dose: str, hhmm: str, label: str = "") -> Schedule:
    med = await upsert_medicine(session, name, dose)
    item = Schedule(medicine_id=med.id, dose=dose, time_local=hhmm, label=label or hhmm)
    session.add(item)
    await session.flush()
    return item


async def seed_default_schedule(session: AsyncSession, replace: bool = False) -> None:
    if replace:
        for table in (DoseEvent, Schedule, Medicine):
            rows = (await session.execute(select(table))).scalars().all()
            for row in rows:
                await session.delete(row)
        await session.flush()
    elif (await session.execute(select(Schedule.id).limit(1))).first():
        return

    b = settings.default_breakfast_time
    l = settings.default_lunch_time
    d = settings.default_dinner_time
    slots = [
        ("Гимекромон", "1 таб", offset_hhmm(b, -30), "🌅 Утро: за 30 мин до еды"),
        ("Тримедат", "1 таб", offset_hhmm(b, -30), "🌅 Утро: за 30 мин до еды"),
        ("Фитомуцил норма", "1 пакет развести в жидкости", b, "🌅 Утро: во время еды"),
        ("Панкреатин", "1 капс", b, "🌅 Утро: во время еды"),
        ("Баксет", "1 капс", b, "🌅 Утро: во время еды"),
        ("Атоминекс", "1 капсула", b, "🌅 Утро: во время еды"),
        ("Аскорутин", "1 таб", offset_hhmm(b, 10), "🌅 Утро: сразу после еды"),
        ("Гимекромон", "1 таб", offset_hhmm(l, -30), "☀️ День: за 30 мин до еды"),
        ("Тримедат", "1 таб", offset_hhmm(l, -30), "☀️ День: за 30 мин до еды"),
        ("Панкреатин", "1 капс", l, "☀️ День: во время еды"),
        ("Аскорутин", "1 таб", offset_hhmm(l, 10), "☀️ День: сразу после еды"),
        ("Феварин", "1 таблетка", "17:30", "17:30"),
        ("Гимекромон", "1 таб", offset_hhmm(d, -30), "🌙 Вечер: за 30 мин до еды"),
        ("Тримедат", "1 таб", offset_hhmm(d, -30), "🌙 Вечер: за 30 мин до еды"),
        ("Фитомуцил норма", "1 пакет", d, "🌙 Вечер: во время еды"),
        ("Панкреатин", "1 капс", d, "🌙 Вечер: во время еды"),
        ("Аскорутин", "1 таб", offset_hhmm(d, 10), "🌙 Вечер: сразу после еды"),
        ("Феварин", "1 таблетка", "23:15", "23:15"),
    ]
    for name, dose, hhmm, label in slots:
        await add_schedule(session, name, dose, hhmm, label)


async def ensure_events(session: AsyncSession, days_ahead: int = 14) -> None:
    today = datetime.now(TZ).date()
    schedules = (await session.execute(select(Schedule).where(Schedule.active == True))).scalars().all()  # noqa: E712
    for sched in schedules:
        for i in range(days_ahead + 1):
            due = local_dt(today + timedelta(days=i), sched.time_local)
            exists = (await session.execute(
                select(DoseEvent.id).where(DoseEvent.schedule_id == sched.id, DoseEvent.due_at == due)
            )).first()
            if not exists:
                session.add(DoseEvent(schedule_id=sched.id, due_at=due))
    await session.commit()


async def get_today_events(session: AsyncSession) -> list[DoseEvent]:
    now = datetime.now(TZ)
    start = datetime.combine(now.date(), time.min, tzinfo=TZ)
    end = start + timedelta(days=1)
    q = select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).where(
        DoseEvent.due_at >= start, DoseEvent.due_at < end
    ).order_by(DoseEvent.due_at)
    return list((await session.execute(q)).scalars().all())


async def get_due_for_reminder(session: AsyncSession) -> list[DoseEvent]:
    now = datetime.now(TZ)
    interval = timedelta(minutes=settings.reminder_interval_minutes)
    q = select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).where(
        DoseEvent.status == "pending",
        DoseEvent.due_at <= now,
        or_(DoseEvent.last_reminded_at.is_(None), DoseEvent.last_reminded_at <= now - interval),
    ).order_by(DoseEvent.due_at).limit(20)
    return list((await session.execute(q)).scalars().all())


def event_title(event: DoseEvent) -> str:
    sched = event.schedule
    return f"{sched.medicine.name} — {sched.dose or sched.medicine.default_dose} ({sched.label})"


def reminder_text(event: DoseEvent) -> str:
    return random.choice(FUN_REMINDERS).format(title=event_title(event))


def thanks_text() -> str:
    return random.choice(THANKS)


async def mark_taken(session: AsyncSession, event_id: int, tg_id: int, note: str = "") -> DoseEvent | None:
    event = (await session.execute(
        select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).where(DoseEvent.id == event_id)
    )).scalar_one_or_none()
    if not event:
        return None
    event.status = "taken"
    event.taken_at = datetime.now(TZ)
    event.taken_by = tg_id
    event.note = note
    await session.commit()
    return event


def format_today(events: list[DoseEvent]) -> str:
    if not events:
        return "На сегодня расписание пустое."
    lines = ["📋 Расписание на сегодня:"]
    for e in events:
        status = "✅" if e.status == "taken" else "⏳"
        lines.append(f"{status} {e.due_at.astimezone(TZ).strftime('%H:%M')} — {event_title(e)}")
    return "\n".join(lines)
