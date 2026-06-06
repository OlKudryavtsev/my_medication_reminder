from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import Medicine, Schedule, DoseEvent, Profile, User, AuditLog
from .messages import REMINDER_TEMPLATES, THANKS_TEMPLATES

settings = get_settings()
TZ = ZoneInfo(settings.timezone)


async def ensure_profiles(session: AsyncSession) -> None:
    """Create child profile and personal parent profiles; attach legacy schedules to child profile."""
    child_profile = None
    if settings.child:
        child_profile = (await session.execute(
            select(Profile).where(Profile.kind == "child", Profile.active == True)
        )).scalar_one_or_none()
        if not child_profile:
            child_profile = Profile(name="Ребенок", kind="child", owner_tg_id=settings.child, active=True)
            session.add(child_profile)
            await session.flush()
        else:
            child_profile.owner_tg_id = settings.child

    for parent_id in settings.parents:
        parent_profile = (await session.execute(
            select(Profile).where(Profile.kind == "personal", Profile.owner_tg_id == parent_id, Profile.active == True)
        )).scalar_one_or_none()
        if not parent_profile:
            session.add(Profile(name="Мой профиль", kind="personal", owner_tg_id=parent_id, active=True))

    if child_profile:
        legacy = (await session.execute(
            select(Schedule).where(Schedule.profile_id.is_(None))
        )).scalars().all()
        for sched in legacy:
            sched.profile_id = child_profile.id

    users = (await session.execute(select(User))).scalars().all()
    for user in users:
        if user.role == "child" and child_profile:
            user.active_profile_id = user.active_profile_id or child_profile.id
        elif user.role == "parent":
            personal = (await session.execute(
                select(Profile).where(Profile.kind == "personal", Profile.owner_tg_id == user.tg_id, Profile.active == True)
            )).scalar_one_or_none()
            user.active_profile_id = user.active_profile_id or (child_profile.id if child_profile else (personal.id if personal else None))
    await session.commit()


async def get_child_profile(session: AsyncSession) -> Profile | None:
    await ensure_profiles(session)
    return (await session.execute(
        select(Profile).where(Profile.kind == "child", Profile.active == True).order_by(Profile.id)
    )).scalars().first()


async def profiles_for_user(session: AsyncSession, tg_id: int, role: str) -> list[Profile]:
    await ensure_profiles(session)
    if role == "parent":
        # Родитель видит все детские профили и свой личный профиль.
        q = select(Profile).where(
            Profile.active == True,
            or_(Profile.kind == "child", Profile.owner_tg_id == tg_id),
        ).order_by(Profile.kind, Profile.id)
    elif role == "child":
        # Ребенок управляет своим детским профилем. Если детских профилей несколько,
        # показываем первый профиль, привязанный к CHILD_CHAT_ID, либо первый активный детский.
        q = select(Profile).where(
            Profile.active == True,
            Profile.kind == "child",
            or_(Profile.owner_tg_id == tg_id, Profile.owner_tg_id.is_(None)),
        ).order_by(Profile.owner_tg_id.desc(), Profile.id)
    else:
        return []
    return list((await session.execute(q)).scalars().all())


async def resolve_profile_id(session: AsyncSession, tg_id: int, role: str, requested_profile_id: int | None = None) -> int:
    profiles = await profiles_for_user(session, tg_id, role)
    if not profiles:
        raise ValueError("No accessible profiles")
    allowed = {p.id for p in profiles}
    if requested_profile_id and requested_profile_id in allowed:
        return requested_profile_id
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user and user.active_profile_id in allowed:
        return int(user.active_profile_id)
    return profiles[0].id


async def set_active_profile(session: AsyncSession, tg_id: int, role: str, profile_id: int) -> Profile | None:
    profiles = await profiles_for_user(session, tg_id, role)
    allowed = {p.id for p in profiles}
    if profile_id not in allowed:
        return None
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user:
        user.active_profile_id = profile_id
    await session.commit()
    return next((p for p in profiles if p.id == profile_id), None)


async def is_profile_manager(session: AsyncSession, tg_id: int, role: str, profile_id: int) -> bool:
    return profile_id in {p.id for p in await profiles_for_user(session, tg_id, role)}


async def profile_recipients(session: AsyncSession, profile_id: int) -> list[int]:
    profile = (await session.execute(select(Profile).where(Profile.id == profile_id))).scalar_one_or_none()
    if not profile:
        return []
    if profile.kind == "personal" and profile.owner_tg_id:
        return [int(profile.owner_tg_id)]
    recipients = []
    if settings.child:
        recipients.append(settings.child)
    recipients.extend(settings.parents)
    return list(dict.fromkeys(recipients))


async def log_action(
    session: AsyncSession,
    profile_id: int | None,
    actor_tg_id: int | None,
    action: str,
    entity_type: str = "",
    entity_id: int | None = None,
    details: str = "",
    commit: bool = False,
) -> AuditLog:
    row = AuditLog(
        profile_id=profile_id,
        actor_tg_id=actor_tg_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details[:2000] if details else "",
    )
    session.add(row)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return row


async def create_child_profile(session: AsyncSession, name: str, actor_tg_id: int) -> Profile:
    profile = Profile(name=name.strip() or "Ребенок", kind="child", owner_tg_id=None, active=True)
    session.add(profile)
    await session.flush()
    await log_action(session, profile.id, actor_tg_id, "profile_created", "profile", profile.id, f"Создан детский профиль: {profile.name}")
    await session.commit()
    return profile


async def update_profile_name(session: AsyncSession, profile_id: int, name: str, actor_tg_id: int) -> Profile | None:
    profile = (await session.execute(select(Profile).where(Profile.id == profile_id, Profile.active == True))).scalar_one_or_none()
    if not profile:
        return None
    old = profile.name
    profile.name = name.strip() or profile.name
    await log_action(session, profile.id, actor_tg_id, "profile_renamed", "profile", profile.id, f"{old} → {profile.name}")
    await session.commit()
    return profile


async def deactivate_profile(session: AsyncSession, profile_id: int, actor_tg_id: int) -> Profile | None:
    profile = (await session.execute(select(Profile).where(Profile.id == profile_id, Profile.active == True))).scalar_one_or_none()
    if not profile:
        return None
    profile.active = False
    await log_action(session, profile.id, actor_tg_id, "profile_deleted", "profile", profile.id, f"Профиль отключен: {profile.name}")
    await session.commit()
    return profile


async def get_audit_log(session: AsyncSession, profile_id: int, limit: int = 50) -> list[AuditLog]:
    rows = (await session.execute(
        select(AuditLog).where(AuditLog.profile_id == profile_id).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    )).scalars().all()
    return list(rows)


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
    profile_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    recurrence_type: str = "daily",
    recurrence_interval_days: int = 1,
) -> Schedule:
    med = await upsert_medicine(session, name, dose)
    item = Schedule(
        profile_id=profile_id,
        medicine_id=med.id,
        dose=dose,
        time_local=hhmm,
        label=label or hhmm,
        start_date=start_date,
        end_date=end_date,
        recurrence_type=recurrence_type or "daily",
        recurrence_interval_days=recurrence_interval_days or 1,
    )
    session.add(item)
    await session.flush()
    return item


async def seed_default_schedule(session: AsyncSession, replace: bool = False) -> None:
    await ensure_profiles(session)
    child_profile = await get_child_profile(session)
    profile_id = child_profile.id if child_profile else None
    if replace:
        rows = (await session.execute(select(Schedule).where(Schedule.profile_id == profile_id))).scalars().all()
        for row in rows:
            events = (await session.execute(select(DoseEvent).where(DoseEvent.schedule_id == row.id))).scalars().all()
            for ev in events:
                await session.delete(ev)
            await session.delete(row)
        await session.flush()
    elif (await session.execute(select(Schedule.id).where(Schedule.profile_id == profile_id).limit(1))).first():
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
        await add_schedule(session, name, dose, hhmm, label, profile_id=profile_id, start_date=today, end_date=None)




def schedule_applies_on_day(sched: Schedule, day: date) -> bool:
    start = sched.start_date or datetime.now(TZ).date()
    recurrence_type = (getattr(sched, "recurrence_type", None) or "daily")
    interval = int(getattr(sched, "recurrence_interval_days", None) or 1)
    if recurrence_type == "monthly":
        return day.day == start.day
    delta_days = (day - start).days
    if delta_days < 0:
        return False
    return delta_days % max(1, interval) == 0

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
            if not schedule_applies_on_day(sched, day):
                continue
            due = local_dt(day, sched.time_local)
            exists = (await session.execute(
                select(DoseEvent.id).where(DoseEvent.schedule_id == sched.id, DoseEvent.due_at == due)
            )).first()
            if not exists:
                session.add(DoseEvent(schedule_id=sched.id, due_at=due))
    await session.commit()


async def get_today_events(session: AsyncSession, profile_id: int | None = None) -> list[DoseEvent]:
    now = datetime.now(TZ)
    start = datetime.combine(now.date(), time.min, tzinfo=TZ)
    end = start + timedelta(days=1)
    q = select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(
        Schedule.active == True,  # noqa: E712
        DoseEvent.due_at >= start, DoseEvent.due_at < end
    ).order_by(DoseEvent.due_at)
    if profile_id is not None:
        q = q.where(Schedule.profile_id == profile_id)
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




def group_key_for_due(due_at: datetime, profile_id: int | None = None) -> str:
    """Stable callback key for a dose time group scoped by profile."""
    pid = int(profile_id or 0)
    return f"{pid}:{int(due_at.astimezone(TZ).timestamp())}"


def parse_group_key(group_key: str | int) -> tuple[int | None, datetime]:
    raw = str(group_key)
    if ":" in raw:
        pid_s, ts_s = raw.split(":", 1)
        pid = int(pid_s) or None
        ts = int(ts_s)
    else:
        pid = None
        ts = int(raw)
    return pid, datetime.fromtimestamp(ts, TZ)


def group_reminder_text(events: list[DoseEvent]) -> str:
    """One compact reminder for all pending medicines scheduled for the same time."""
    events = sorted(events, key=lambda e: (e.schedule.medicine.name, e.schedule.dose or ""))
    first = events[0]
    hhmm = first.due_at.astimezone(TZ).strftime("%H:%M")
    lines = [f"💊 Пора принять лекарства на {hhmm}:"]
    for event in events:
        sched = event.schedule
        dose = sched.dose or sched.medicine.default_dose
        label = f" — {sched.label}" if sched.label and sched.label != sched.time_local else ""
        lines.append(f"• {sched.medicine.name} — {dose}{label}")
    if any(e.postponed_until for e in events):
        postponed_to = max(e.postponed_until for e in events if e.postponed_until)
        lines.append(f"\n⏰ Группа была отложена до {postponed_to.astimezone(TZ).strftime('%H:%M')}.")
    idx = (sum(e.id for e in events) + sum(e.reminder_count for e in events)) % len(REMINDER_TEMPLATES)
    joke = REMINDER_TEMPLATES[idx].format(title="этот аптечный набор")
    return joke + "\n\n" + "\n".join(lines)


async def get_pending_group_by_key(session: AsyncSession, group_key: str | int) -> list[DoseEvent]:
    profile_id, due = parse_group_key(group_key)
    start = due - timedelta(seconds=1)
    end = due + timedelta(seconds=1)
    q = select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(
        Schedule.active == True,  # noqa: E712
        DoseEvent.status == "pending",
        DoseEvent.due_at >= start,
        DoseEvent.due_at <= end,
    ).order_by(DoseEvent.due_at, DoseEvent.id)
    if profile_id is not None:
        q = q.where(Schedule.profile_id == profile_id)
    return list((await session.execute(q)).scalars().all())


async def mark_group_taken(
    session: AsyncSession,
    group_key: str | int,
    tg_id: int,
    actual_time: str | None = None,
) -> list[DoseEvent]:
    events = await get_pending_group_by_key(session, group_key)
    if not events:
        return []
    actual_dt = local_dt(events[0].due_at.astimezone(TZ).date(), actual_time) if actual_time else datetime.now(TZ)
    for event in events:
        event.status = "taken"
        event.taken_at = actual_dt
        event.taken_by = tg_id
        event.skipped_at = None
        event.skipped_by = None
        event.postponed_until = None
        event.note = f"Групповая отметка пользователем {tg_id}"
    await session.commit()
    return events


async def mark_group_skipped(session: AsyncSession, group_key: str | int, tg_id: int) -> list[DoseEvent]:
    events = await get_pending_group_by_key(session, group_key)
    if not events:
        return []
    now = datetime.now(TZ)
    for event in events:
        event.status = "skipped"
        event.skipped_at = now
        event.skipped_by = tg_id
        event.taken_at = None
        event.taken_by = None
        event.postponed_until = None
        event.note = f"Групповой пропуск пользователем {tg_id}"
    await session.commit()
    return events


async def snooze_group(session: AsyncSession, group_key: str | int, tg_id: int, minutes: int | None = None) -> list[DoseEvent]:
    events = await get_pending_group_by_key(session, group_key)
    if not events:
        return []
    until = datetime.now(TZ) + timedelta(minutes=minutes or settings.snooze_minutes)
    for event in events:
        event.status = "pending"
        event.postponed_until = until
        event.last_reminded_at = until
        event.note = f"Групповое отложение пользователем {tg_id}"
    await session.commit()
    return events

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




async def set_event_status(
    session: AsyncSession,
    event_id: int,
    tg_id: int,
    status: str,
    actual_time: str | None = None,
) -> DoseEvent | None:
    event = await get_event(session, event_id)
    if not event:
        return None
    if status == "pending":
        event.status = "pending"
        event.taken_at = None
        event.taken_by = None
        event.skipped_at = None
        event.skipped_by = None
        event.postponed_until = None
        event.note = f"Статус изменен пользователем {tg_id}"
    elif status == "taken":
        actual_dt = local_dt(event.due_at.astimezone(TZ).date(), actual_time) if actual_time else datetime.now(TZ)
        event.status = "taken"
        event.taken_at = actual_dt
        event.taken_by = tg_id
        event.skipped_at = None
        event.skipped_by = None
        event.postponed_until = None
        event.note = f"Статус изменен пользователем {tg_id}"
    elif status == "skipped":
        event.status = "skipped"
        event.skipped_at = datetime.now(TZ)
        event.skipped_by = tg_id
        event.taken_at = None
        event.taken_by = None
        event.postponed_until = None
        event.note = f"Статус изменен пользователем {tg_id}"
    else:
        return None
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
    recurrence_type: str | None = None,
    recurrence_interval_days: int | None = None,
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
    if recurrence_type is not None:
        sched.recurrence_type = recurrence_type or "daily"
    if recurrence_interval_days is not None:
        sched.recurrence_interval_days = recurrence_interval_days or 1
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


async def get_history_for_medicine(session: AsyncSession, medicine_id: int, days: int = 30, profile_id: int | None = None) -> list[DoseEvent]:
    now = datetime.now(TZ)
    start = now - timedelta(days=days)
    q = select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(
        Schedule.medicine_id == medicine_id,
        DoseEvent.due_at >= start,
        DoseEvent.due_at <= now,
    ).order_by(DoseEvent.due_at.desc())
    if profile_id is not None:
        q = q.where(Schedule.profile_id == profile_id)
    return list((await session.execute(q)).scalars().all())


async def get_stats(session: AsyncSession, medicine_id: int | None = None, days: int = 30, profile_id: int | None = None) -> list[dict]:
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
    if profile_id is not None:
        q = q.where(Schedule.profile_id == profile_id)
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
