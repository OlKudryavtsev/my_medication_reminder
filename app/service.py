from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import Medicine, Schedule, DoseEvent
from .messages import REMINDER_TEMPLATES, THANKS_TEMPLATES

settings = get_settings()
TZ = ZoneInfo(settings.timezone)


SKIP_TEXTS = [
    "🟡 Отметил как пропущено. Главное — честная статистика, без нее аптечный штаб слепнет.",
    "📝 Пропуск сохранен. Не ругаемся, фиксируем факт и идем дальше по плану.",
    "👌 Пропуск записан. Честные данные лучше красивой легенды.",
    "🧾 Сохранил пропуск. Аптечный журнал не осуждает, он фиксирует.",
]

def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def parse_date_or_none(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


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


async def add_schedule(
    session: AsyncSession,
    name: str,
    dose: str,
    hhmm: str,
    label: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
) -> Schedule:
    med = await upsert_medicine(session, name, dose)
    item = Schedule(
        medicine_id=med.id,
        dose=dose,
        time_local=hhmm,
        label=label or hhmm,
        start_date=start_date,
        end_date=end_date,
    )
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
    today = datetime.now(TZ).date()
    slots = [
        ("Гимекромон", "1 таб", offset_hhmm(b, -30), "🌅 Утро: за 30 мин до еды"),
        ("Тримедат", "1 таб", offset_hhmm(b, -30), "🌅 Утро: за 30 мин до еды"),
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
        ("Панкреатин", "1 капс", d, "🌙 Вечер: во время еды"),
        ("Аскорутин", "1 таб", offset_hhmm(d, 10), "🌙 Вечер: сразу после еды"),
        ("Феварин", "1 таблетка", "23:15", "23:15"),
    ]
    for name, dose, hhmm, label in slots:
        await add_schedule(session, name, dose, hhmm, label, start_date=today, end_date=None)


async def ensure_events(session: AsyncSession, days_ahead: int = 14) -> None:
    today = datetime.now(TZ).date()
    schedules = (await session.execute(select(Schedule).where(Schedule.active == True))).scalars().all()  # noqa: E712
    for sched in schedules:
        for i in range(days_ahead + 1):
            day = today + timedelta(days=i)
            if sched.start_date and day < sched.start_date:
                continue
            if sched.end_date and day > sched.end_date:
                continue
            due = local_dt(day, sched.time_local)
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
    q = select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(
        Schedule.active == True,  # noqa: E712
        DoseEvent.due_at >= start, DoseEvent.due_at < end
    ).order_by(DoseEvent.due_at)
    return list((await session.execute(q)).scalars().all())


async def get_due_for_reminder(session: AsyncSession) -> list[DoseEvent]:
    now = datetime.now(TZ)
    interval = timedelta(minutes=settings.reminder_interval_minutes)
    q = select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(
        Schedule.active == True,  # noqa: E712
        DoseEvent.status == "pending",
        DoseEvent.due_at <= now,
        or_(DoseEvent.postponed_until.is_(None), DoseEvent.postponed_until <= now),
        or_(DoseEvent.last_reminded_at.is_(None), DoseEvent.last_reminded_at <= now - interval),
    ).order_by(DoseEvent.due_at).limit(20)
    return list((await session.execute(q)).scalars().all())


def event_title(event: DoseEvent) -> str:
    sched = event.schedule
    return f"{sched.medicine.name} — {sched.dose or sched.medicine.default_dose} ({sched.label})"


def reminder_text(event: DoseEvent) -> str:
    postponed = ""
    if event.postponed_until:
        postponed = f"\n⏰ Отложено было до {event.postponed_until.astimezone(TZ).strftime('%H:%M')}."
    idx = (event.id + event.reminder_count) % len(REMINDER_TEMPLATES)
    return REMINDER_TEMPLATES[idx].format(title=event_title(event)) + postponed


def thanks_text(event_id: int | None = None) -> str:
    base = event_id or int(datetime.now(TZ).timestamp())
    return THANKS_TEMPLATES[base % len(THANKS_TEMPLATES)]


def skip_text(event_id: int | None = None) -> str:
    base = event_id or int(datetime.now(TZ).timestamp())
    return SKIP_TEXTS[base % len(SKIP_TEXTS)]


async def get_event(session: AsyncSession, event_id: int) -> DoseEvent | None:
    return (await session.execute(
        select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).where(DoseEvent.id == event_id)
    )).scalar_one_or_none()


async def mark_taken(
    session: AsyncSession,
    event_id: int,
    tg_id: int,
    note: str = "",
    actual_time: str | None = None,
) -> DoseEvent | None:
    event = await get_event(session, event_id)
    if not event:
        return None
    if actual_time:
        actual_dt = local_dt(event.due_at.astimezone(TZ).date(), actual_time)
    else:
        actual_dt = datetime.now(TZ)
    event.status = "taken"
    event.taken_at = actual_dt
    event.taken_by = tg_id
    event.postponed_until = None
    event.note = note
    await session.commit()
    return event


async def mark_skipped(session: AsyncSession, event_id: int, tg_id: int, note: str = "") -> DoseEvent | None:
    event = await get_event(session, event_id)
    if not event:
        return None
    event.status = "skipped"
    event.skipped_at = datetime.now(TZ)
    event.skipped_by = tg_id
    event.postponed_until = None
    event.note = note
    await session.commit()
    return event


async def snooze_event(session: AsyncSession, event_id: int, tg_id: int, minutes: int | None = None) -> DoseEvent | None:
    event = await get_event(session, event_id)
    if not event:
        return None
    event.status = "pending"
    event.postponed_until = datetime.now(TZ) + timedelta(minutes=minutes or settings.snooze_minutes)
    event.last_reminded_at = event.postponed_until
    event.note = f"Отложено пользователем {tg_id}"
    await session.commit()
    return event



async def update_taken_time(session: AsyncSession, event_id: int, tg_id: int, actual_time: str) -> DoseEvent | None:
    event = await get_event(session, event_id)
    if not event:
        return None
    event.status = "taken"
    event.taken_at = local_dt(event.due_at.astimezone(TZ).date(), actual_time)
    event.taken_by = tg_id
    event.postponed_until = None
    await session.commit()
    return event


async def update_schedule(
    session: AsyncSession,
    schedule_id: int,
    name: str,
    dose: str,
    hhmm: str,
    label: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
) -> Schedule | None:
    sched = (await session.execute(
        select(Schedule).options(selectinload(Schedule.medicine)).where(Schedule.id == schedule_id)
    )).scalar_one_or_none()
    if not sched:
        return None
    med = await upsert_medicine(session, name, dose)
    sched.medicine_id = med.id
    sched.dose = dose
    sched.time_local = hhmm
    sched.label = label or hhmm
    sched.start_date = start_date
    sched.end_date = end_date
    sched.active = True

    # Старые будущие pending-события удаляем, чтобы пересоздать их по новому времени/курсу.
    now = datetime.now(TZ)
    old_pending = (await session.execute(
        select(DoseEvent).where(
            DoseEvent.schedule_id == schedule_id,
            DoseEvent.status == "pending",
            DoseEvent.due_at >= now,
        )
    )).scalars().all()
    for event in old_pending:
        await session.delete(event)
    await session.commit()
    await ensure_events(session)
    return sched

def status_icon(e: DoseEvent) -> str:
    if e.status == "taken":
        return "✅"
    if e.status == "skipped":
        return "⏭️"
    if e.postponed_until and e.postponed_until > datetime.now(TZ):
        return "😴"
    return "⏳"


def format_today(events: list[DoseEvent]) -> str:
    if not events:
        return "На сегодня расписание пустое."
    lines = ["📋 Расписание на сегодня:"]
    for e in events:
        extra = ""
        if e.status == "taken" and e.taken_at:
            extra = f" — принято в {e.taken_at.astimezone(TZ).strftime('%H:%M')}"
        elif e.status == "skipped":
            extra = " — пропущено"
        elif e.postponed_until and e.postponed_until > datetime.now(TZ):
            extra = f" — отложено до {e.postponed_until.astimezone(TZ).strftime('%H:%M')}"
        lines.append(f"{status_icon(e)} {e.due_at.astimezone(TZ).strftime('%H:%M')} — {event_title(e)}{extra}")
    return "\n".join(lines)


async def get_history_for_medicine(session: AsyncSession, medicine_id: int, days: int = 30) -> list[DoseEvent]:
    now = datetime.now(TZ)
    start = now - timedelta(days=days)
    q = select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(
        Schedule.medicine_id == medicine_id,
        DoseEvent.due_at >= start,
        DoseEvent.due_at <= now,
    ).order_by(DoseEvent.due_at.desc())
    return list((await session.execute(q)).scalars().all())


async def get_stats(session: AsyncSession, medicine_id: int | None = None, days: int = 30) -> list[dict]:
    now = datetime.now(TZ)
    start = now - timedelta(days=days)
    q = select(
        Medicine.id,
        Medicine.name,
        func.count(DoseEvent.id).label("total"),
        func.sum(case((DoseEvent.status == "taken", 1), else_=0)).label("taken"),
        func.sum(case((DoseEvent.status == "skipped", 1), else_=0)).label("skipped"),
        func.sum(case((DoseEvent.status == "pending", 1), else_=0)).label("pending"),
    ).join(Schedule, Schedule.medicine_id == Medicine.id).join(DoseEvent, DoseEvent.schedule_id == Schedule.id).where(
        DoseEvent.due_at >= start,
        DoseEvent.due_at <= now
    ).group_by(Medicine.id, Medicine.name).order_by(Medicine.name)
    if medicine_id:
        q = q.where(Medicine.id == medicine_id)
    rows = (await session.execute(q)).all()
    result = []
    for r in rows:
        total = int(r.total or 0)
        taken = int(r.taken or 0)
        skipped = int(r.skipped or 0)
        pending = int(r.pending or 0)
        result.append({
            "medicine_id": r.id,
            "medicine": r.name,
            "total": total,
            "taken": taken,
            "skipped": skipped,
            "pending": pending,
            "taken_percent": round((taken / total) * 100, 1) if total else 0,
        })
    return result
