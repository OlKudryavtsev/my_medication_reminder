from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import Medicine, Schedule, DoseEvent, Profile, User, AuditLog, TreatmentCourse, MealOverride, InventoryItem, MedicineCourse, Family, FamilyMember, NotificationSetting
from .messages import REMINDER_TEMPLATES, THANKS_TEMPLATES

settings = get_settings()
TZ = ZoneInfo(settings.timezone)


async def ensure_profiles(session: AsyncSession) -> None:
    """Create/migrate default family and legacy profiles without deleting data."""
    # db.init_db() already runs migrate_families(); keep this lightweight guard for runtime calls.
    default_family = (await session.execute(select(Family).where(Family.name == "Семья по умолчанию", Family.active == True))).scalar_one_or_none()
    if not default_family:
        owner_user = None
        if settings.parents:
            owner_user = (await session.execute(select(User).where(User.tg_id == settings.parents[0]))).scalar_one_or_none()
        default_family = Family(name="Семья по умолчанию", owner_user_id=owner_user.id if owner_user else None, active=True)
        session.add(default_family)
        await session.flush()
    # Existing deployments: ensure there is a child profile when CHILD_CHAT_ID is configured.
    child_profile = None
    if settings.child:
        child_profile = (await session.execute(
            select(Profile).where(Profile.family_id == default_family.id, Profile.kind == "child", Profile.active == True).order_by(Profile.id)
        )).scalars().first()
        if not child_profile:
            child_profile = Profile(name="Ребенок", kind="child", owner_tg_id=settings.child, family_id=default_family.id, active=True)
            session.add(child_profile)
            await session.flush()
        elif not child_profile.owner_tg_id:
            child_profile.owner_tg_id = settings.child
    # Existing deployments: personal profiles for configured parents.
    for parent_id in settings.parents:
        parent_profile = (await session.execute(
            select(Profile).where(Profile.family_id == default_family.id, Profile.kind == "personal", Profile.owner_tg_id == parent_id, Profile.active == True)
        )).scalar_one_or_none()
        if not parent_profile:
            session.add(Profile(name="Мой профиль", kind="personal", owner_tg_id=parent_id, family_id=default_family.id, active=True))
    # Attach legacy profiles/schedules.
    for p in (await session.execute(select(Profile).where(Profile.family_id.is_(None)))).scalars().all():
        p.family_id = default_family.id
    if child_profile:
        legacy = (await session.execute(select(Schedule).where(Schedule.profile_id.is_(None)))).scalars().all()
        for sched in legacy:
            sched.profile_id = child_profile.id
    await session.commit()


async def create_private_family_for_user(session: AsyncSession, user: User) -> Profile:
    """Create a personal family/profile for an approved external user. Idempotent."""
    existing_member = (await session.execute(
        select(FamilyMember).where(FamilyMember.user_id == user.id, FamilyMember.active == True).order_by(FamilyMember.id)
    )).scalars().first()
    if existing_member:
        profile = None
        if user.active_profile_id:
            profile = (await session.execute(select(Profile).where(Profile.id == user.active_profile_id, Profile.active == True))).scalar_one_or_none()
        if profile:
            return profile
        profile = (await session.execute(
            select(Profile).where(Profile.family_id == existing_member.family_id, Profile.kind == "personal", Profile.active == True).order_by(Profile.id)
        )).scalars().first()
        if profile:
            user.active_profile_id = profile.id
            await session.flush()
            return profile

    fam_name = f"Семья {user.full_name}" if user.full_name else "Моя семья"
    family = Family(name=fam_name[:255], owner_user_id=user.id, active=True)
    session.add(family)
    await session.flush()
    personal = Profile(name="Мой профиль", kind="personal", owner_tg_id=user.tg_id, family_id=family.id, active=True)
    session.add(personal)
    await session.flush()
    session.add(FamilyMember(family_id=family.id, user_id=user.id, role="owner", linked_profile_id=None, active=True))
    user.active_profile_id = personal.id
    await session.flush()
    return personal


async def approve_user_access(session: AsyncSession, tg_id: int) -> User | None:
    """Approve a pending/rejected user and create their own private family."""
    await ensure_profiles(session)
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not user:
        return None
    user.role = "parent"
    await create_private_family_for_user(session, user)
    await session.commit()
    return user


async def reject_user_access(session: AsyncSession, tg_id: int) -> User | None:
    await ensure_profiles(session)
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not user:
        return None
    user.role = "rejected"
    await session.commit()
    return user


async def ensure_user_account(session: AsyncSession, tg_id: int, full_name: str = "", role_hint: str = "") -> User:
    """Create/update Telegram user.

    v45 behavior: external users are NOT auto-registered. They become ``pending``
    until an existing configured parent approves them. Configured Railway parents/child
    are still migrated to the default family and keep access.
    """
    await ensure_profiles(session)
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    configured_role = "child" if settings.child and tg_id == settings.child else "parent" if tg_id in settings.parents else "pending"
    if not user:
        user = User(tg_id=tg_id, full_name=full_name or "", role=(role_hint if role_hint in {"parent", "child"} else configured_role))
        session.add(user)
        await session.flush()
    else:
        if full_name:
            user.full_name = full_name
        if tg_id in settings.parents and user.role not in {"parent", "child"}:
            user.role = "parent"
        elif settings.child and tg_id == settings.child and user.role not in {"parent", "child"}:
            user.role = "child"
        elif user.role in {"", "unknown", None}:
            user.role = "pending"

    # Only approved/configured users can have families/profiles created automatically.
    if user.role in {"parent", "child"}:
        existing_member = (await session.execute(
            select(FamilyMember).where(FamilyMember.user_id == user.id, FamilyMember.active == True).order_by(FamilyMember.id)
        )).scalars().first()
        if not existing_member and user.role == "parent" and tg_id not in settings.parents:
            await create_private_family_for_user(session, user)
    await session.commit()
    return user


async def get_child_profile(session: AsyncSession) -> Profile | None:
    await ensure_profiles(session)
    return (await session.execute(
        select(Profile).where(Profile.kind == "child", Profile.active == True).order_by(Profile.id)
    )).scalars().first()


async def profiles_for_user(session: AsyncSession, tg_id: int, role: str) -> list[Profile]:
    user = await ensure_user_account(session, tg_id, role_hint=role)
    rows = (await session.execute(
        select(FamilyMember).where(FamilyMember.user_id == user.id, FamilyMember.active == True)
    )).scalars().all()
    if not rows:
        return []
    profile_ids: set[int] = set()
    family_ids: set[int] = set()
    for m in rows:
        if m.role in {"owner", "parent"}:
            family_ids.add(m.family_id)
        elif m.role == "child" and m.linked_profile_id:
            profile_ids.add(m.linked_profile_id)
        elif m.role == "viewer" and m.linked_profile_id:
            profile_ids.add(m.linked_profile_id)
    conditions = []
    if family_ids:
        conditions.append(Profile.family_id.in_(family_ids))
    if profile_ids:
        conditions.append(Profile.id.in_(profile_ids))
    if not conditions:
        return []
    return list((await session.execute(select(Profile).where(Profile.active == True, or_(*conditions)).order_by(Profile.family_id, Profile.kind, Profile.id))).scalars().all())


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
    user = await ensure_user_account(session, tg_id, role_hint=role)
    profile = (await session.execute(select(Profile).where(Profile.id == profile_id, Profile.active == True))).scalar_one_or_none()
    if not profile:
        return False
    member = (await session.execute(select(FamilyMember).where(
        FamilyMember.user_id == user.id,
        FamilyMember.family_id == profile.family_id,
        FamilyMember.active == True,
        FamilyMember.role.in_(["owner", "parent"]),
    ))).scalar_one_or_none()
    return bool(member)


async def profile_recipients(session: AsyncSession, profile_id: int) -> list[int]:
    profile = (await session.execute(select(Profile).where(Profile.id == profile_id))).scalar_one_or_none()
    if not profile:
        return []
    if profile.kind == "personal" and profile.owner_tg_id:
        return [int(profile.owner_tg_id)]
    # Family-based recipients: child linked to the profile + parents/owners in the family.
    rows = (await session.execute(
        select(FamilyMember, User).join(User, User.id == FamilyMember.user_id).where(
            FamilyMember.family_id == profile.family_id,
            FamilyMember.active == True,
            or_(FamilyMember.role.in_(["owner", "parent"]), FamilyMember.linked_profile_id == profile.id),
        )
    )).all()
    recipients = [int(user.tg_id) for _member, user in rows if user and user.tg_id]
    # Backward-compatible fallback for the migrated default family.
    if not recipients:
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
    user = await ensure_user_account(session, actor_tg_id, role_hint="parent")
    member = (await session.execute(select(FamilyMember).where(FamilyMember.user_id == user.id, FamilyMember.active == True, FamilyMember.role.in_(["owner", "parent"])).order_by(FamilyMember.id))).scalars().first()
    family_id = member.family_id if member else None
    profile = Profile(name=name.strip() or "Ребенок", kind="child", owner_tg_id=None, family_id=family_id, active=True)
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


async def get_courses(session: AsyncSession, profile_id: int) -> list[TreatmentCourse]:
    return list((await session.execute(select(TreatmentCourse).where(TreatmentCourse.profile_id == profile_id, TreatmentCourse.active == True).order_by(TreatmentCourse.assignment_date.desc().nullslast(), TreatmentCourse.id.desc()))).scalars().all())


async def create_course(session: AsyncSession, profile_id: int, name: str, assignment_date: date | None = None, doctor: str = "", comment: str = "", actor_tg_id: int | None = None) -> TreatmentCourse:
    c = TreatmentCourse(profile_id=profile_id, name=name.strip() or "Назначение", assignment_date=assignment_date, doctor=doctor or "", comment=comment or "", active=True)
    session.add(c)
    await session.flush()
    await log_action(session, profile_id, actor_tg_id, "course_created", "course", c.id, f"Создано назначение: {c.name}")
    await session.commit()
    return c


async def update_course(session: AsyncSession, profile_id: int, course_id: int, name: str, assignment_date: date | None = None, doctor: str = "", comment: str = "", actor_tg_id: int | None = None) -> TreatmentCourse | None:
    c = (await session.execute(select(TreatmentCourse).where(TreatmentCourse.id == course_id, TreatmentCourse.profile_id == profile_id, TreatmentCourse.active == True))).scalar_one_or_none()
    if not c:
        return None
    c.name = name.strip() or c.name
    c.assignment_date = assignment_date
    c.doctor = doctor or ""
    c.comment = comment or ""
    await log_action(session, profile_id, actor_tg_id, "course_updated", "course", c.id, f"Изменено назначение: {c.name}")
    await session.commit()
    return c


async def deactivate_course(session: AsyncSession, profile_id: int, course_id: int, actor_tg_id: int | None = None, disable_schedules: bool = True) -> TreatmentCourse | None:
    c = (await session.execute(select(TreatmentCourse).where(TreatmentCourse.id == course_id, TreatmentCourse.profile_id == profile_id, TreatmentCourse.active == True))).scalar_one_or_none()
    if not c:
        return None
    c.active = False
    if disable_schedules:
        rows = (await session.execute(select(Schedule).where(Schedule.course_id == course_id, Schedule.profile_id == profile_id))).scalars().all()
        for r in rows:
            r.active = False
    await log_action(session, profile_id, actor_tg_id, "course_deleted", "course", c.id, f"Удалено назначение: {c.name}")
    await session.commit()
    return c


async def set_meal_time_for_day(session: AsyncSession, profile_id: int, meal_date: date, meal_name: str, time_local: str, actor_tg_id: int | None = None) -> MealOverride:
    row = (await session.execute(select(MealOverride).where(MealOverride.profile_id == profile_id, MealOverride.meal_date == meal_date, MealOverride.meal_name == meal_name))).scalar_one_or_none()
    if not row:
        row = MealOverride(profile_id=profile_id, meal_date=meal_date, meal_name=meal_name, time_local=time_local)
        session.add(row)
    else:
        row.time_local = time_local
    await log_action(session, profile_id, actor_tg_id, "meal_time_changed", "meal_override", row.id, f"{meal_name} {meal_date.isoformat()} → {time_local}")
    await session.commit()
    return row


async def get_meal_overrides_for_day(session: AsyncSession, profile_id: int, meal_date: date) -> list[MealOverride]:
    return list((await session.execute(select(MealOverride).where(MealOverride.profile_id == profile_id, MealOverride.meal_date == meal_date).order_by(MealOverride.meal_name))).scalars().all())


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


def meal_base_time(meal_name: str) -> str:
    return {
        "breakfast": settings.default_breakfast_time,
        "lunch": settings.default_lunch_time,
        "dinner": settings.default_dinner_time,
    }.get(meal_name or "", settings.default_breakfast_time)


async def schedule_due_hhmm(session: AsyncSession, sched: Schedule, day: date) -> str:
    template = getattr(sched, "timing_template", None) or "fixed"
    if template == "fixed" or not getattr(sched, "meal_name", ""):
        return sched.time_local
    override = (await session.execute(select(MealOverride).where(
        MealOverride.profile_id == sched.profile_id,
        MealOverride.meal_date == day,
        MealOverride.meal_name == sched.meal_name,
    ))).scalar_one_or_none()
    base = override.time_local if override else meal_base_time(sched.meal_name)
    return offset_hhmm(base, int(getattr(sched, "meal_offset_minutes", 0) or 0))


def normalize_recurrence(kind: str | None, interval_days: int | None) -> tuple[str, int]:
    kind = (kind or "daily").strip().lower()
    if kind == "weekly":
        return "weekly", max(7, int(interval_days or 7))
    if kind == "monthly":
        return "monthly", 30
    if kind == "specific_dates":
        return "specific_dates", 1
    return "daily", max(1, int(interval_days or 1))


def parse_dose_amount(dose: str) -> tuple[float, str]:
    """Best-effort parser for dose text. Returns amount and unit.

    Examples: "1 таб" -> (1, "таб"), "1/2 таблетки" -> (0.5, "таблетки"),
    "5 мл" -> (5, "мл"). Ambiguous text falls back to 1 шт.
    """
    import re
    value = (dose or "").strip().replace(",", ".")
    if not value:
        return 1.0, "шт"
    m = re.search(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", value)
    if m:
        try:
            amount = float(m.group(1)) / float(m.group(2))
        except Exception:
            amount = 1.0
        rest = value[m.end():].strip()
        unit = rest.split()[0] if rest else "шт"
        return amount, unit
    m = re.search(r"\d+(?:\.\d+)?", value)
    if m:
        try:
            amount = float(m.group(0))
        except Exception:
            amount = 1.0
        rest = value[m.end():].strip()
        unit = rest.split()[0] if rest else "шт"
        return amount, unit
    return 1.0, "шт"


def count_planned_days_for_schedule(sched: Schedule, horizon_days: int = 365) -> int:
    start = sched.start_date or datetime.now(TZ).date()
    end = sched.end_date or (start + timedelta(days=horizon_days - 1))
    if end < start:
        return 0
    max_end = start + timedelta(days=horizon_days - 1)
    if end > max_end:
        end = max_end
    total = 0
    day = start
    while day <= end:
        if schedule_applies_on_day(sched, day):
            total += 1
        day += timedelta(days=1)
    return total


def refresh_schedule_need_fields(sched: Schedule) -> None:
    # v29: dose is the single source of truth for course/inventory calculations.
    # Fields consume_units_per_dose / consume_unit_name are kept as technical cached columns
    # for backward compatibility with existing DB, but users no longer edit them separately.
    amount, unit = parse_dose_amount(getattr(sched, "dose", ""))
    sched.consume_units_per_dose = float(amount or 1)
    sched.consume_unit_name = unit or "шт"
    days_count = count_planned_days_for_schedule(sched)
    sched.planned_doses_count = days_count
    sched.planned_units_total = round(days_count * float(sched.consume_units_per_dose or 1), 3)



def calc_end_date_from_duration_value(start_date: date | None, duration_value: int | None, duration_unit: str | None) -> date | None:
    """Return inclusive end date for a course duration.

    14 days starting 2026-06-01 ends 2026-06-14.
    1 week ends after 7 calendar days inclusive.
    1 month uses calendar month then minus one day.
    """
    if not start_date or not duration_value:
        return None
    try:
        n = int(duration_value)
    except Exception:
        return None
    if n <= 0:
        return None
    unit = (duration_unit or "days").strip().lower()
    if unit in {"day", "days", "день", "дня", "дней"}:
        return start_date + timedelta(days=n - 1)
    if unit in {"week", "weeks", "неделя", "недели", "недель"}:
        return start_date + timedelta(days=n * 7 - 1)
    if unit in {"month", "months", "месяц", "месяца", "месяцев"}:
        # Avoid dateutil dependency in service; approximate by month roll-forward.
        month = start_date.month - 1 + n
        year = start_date.year + month // 12
        month = month % 12 + 1
        import calendar
        day = min(start_date.day, calendar.monthrange(year, month)[1])
        return date(year, month, day) - timedelta(days=1)
    return None


def sync_medicine_course_need_fields(mc: MedicineCourse) -> None:
    """Calculate cached demand fields from medicine course attributes."""
    amount, unit = parse_dose_amount(getattr(mc, "dose", ""))
    start = mc.start_date or datetime.now(TZ).date()
    end = mc.end_date or calc_end_date_from_duration_value(start, mc.duration_value, mc.duration_unit)
    if not end:
        mc.planned_doses_count = 0
        mc.planned_units_total = 0.0
        return
    # Approximate per-course count by recurrence interval. Exact per schedule row is still in schedules.
    days = 0
    cur = start
    interval = max(1, int(getattr(mc, "recurrence_interval_days", 1) or 1))
    while cur <= end:
        if ((cur - start).days % interval) == 0:
            days += 1
        cur += timedelta(days=1)
    mc.planned_doses_count = days
    mc.planned_units_total = round(days * float(amount or 1), 3)


async def rebuild_future_events_for_schedule(session: AsyncSession, schedule_id: int) -> None:
    """Delete only future pending events for a schedule; keep taken/skipped history."""
    now = datetime.now(TZ)
    rows = (await session.execute(select(DoseEvent).where(
        DoseEvent.schedule_id == schedule_id,
        DoseEvent.status == "pending",
        DoseEvent.due_at >= now,
    ))).scalars().all()
    for event in rows:
        await session.delete(event)


async def complete_expired_medicine_courses(session: AsyncSession) -> int:
    """Automatically complete active medicine courses whose end date passed.

    Completed courses and their schedule rules are deactivated. Already taken/skipped
    dose_events are preserved; future pending events are removed.
    """
    today = datetime.now(TZ).date()
    rows = (await session.execute(select(MedicineCourse).where(
        MedicineCourse.active == True,  # noqa: E712
        MedicineCourse.end_date.is_not(None),
        MedicineCourse.end_date < today,
    ))).scalars().all()
    completed = 0
    for mc in rows:
        mc.active = False
        mc.status = "completed"
        scheds = (await session.execute(select(Schedule).where(Schedule.medicine_course_id == mc.id))).scalars().all()
        for sched in scheds:
            sched.active = False
            await rebuild_future_events_for_schedule(session, sched.id)
        completed += 1
    return completed



async def inventory_item_for_schedule(session: AsyncSession, sched: Schedule) -> InventoryItem | None:
    if getattr(sched, "inventory_item_id", None):
        item = (await session.execute(select(InventoryItem).where(
            InventoryItem.id == sched.inventory_item_id,
            InventoryItem.active == True,
        ))).scalar_one_or_none()
        if item:
            return item
    med = getattr(sched, "medicine", None)
    if not med:
        return None
    return (await session.execute(select(InventoryItem).where(
        InventoryItem.profile_id == sched.profile_id,
        InventoryItem.active == True,
        or_(InventoryItem.medicine_id == med.id, InventoryItem.name == med.name),
    ).order_by(InventoryItem.medicine_id.desc(), InventoryItem.id))).scalars().first()


async def schedule_stock_info(session: AsyncSession, sched: Schedule) -> dict:
    refresh_schedule_need_fields(sched)
    item = await inventory_item_for_schedule(session, sched)
    taken_units = 0.0
    rows = (await session.execute(select(DoseEvent).where(
        DoseEvent.schedule_id == sched.id,
        DoseEvent.status == "taken",
    ))).scalars().all()
    taken_units = len(rows) * float(getattr(sched, "consume_units_per_dose", 1) or 1)
    remaining_need = max(0.0, float(getattr(sched, "planned_units_total", 0) or 0) - taken_units)
    stock = float(item.quantity) if item else None
    shortage = max(0.0, remaining_need - stock) if stock is not None else None
    return {
        "inventory_item_id": item.id if item else None,
        "inventory_item_name": item.name if item else "",
        "inventory_quantity": stock,
        "planned_doses_count": int(getattr(sched, "planned_doses_count", 0) or 0),
        "planned_units_total": float(getattr(sched, "planned_units_total", 0) or 0),
        "taken_units": taken_units,
        "remaining_need_units": remaining_need,
        "shortage_units": shortage,
        "consume_units_per_dose": float(getattr(sched, "consume_units_per_dose", 1) or 1),
        "consume_unit_name": getattr(sched, "consume_unit_name", "") or (item.unit_name if item else "шт"),
    }


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
    course_id: int | None = None,
    medicine_course_id: int | None = None,
    weekdays: str = "",
    specific_dates: str = "",
    timing_template: str = "fixed",
    meal_name: str = "",
    meal_offset_minutes: int = 0,
    inventory_item_id: int | None = None,
    consume_units_per_dose: float | None = None,
    consume_unit_name: str = "",
) -> Schedule:
    med = await upsert_medicine(session, name, dose)
    item = Schedule(
        profile_id=profile_id,
        course_id=course_id,
        medicine_course_id=medicine_course_id,
        medicine_id=med.id,
        dose=dose,
        time_local=hhmm,
        label=label or hhmm,
        start_date=start_date,
        end_date=end_date,
        recurrence_type=recurrence_type or "daily",
        recurrence_interval_days=recurrence_interval_days or 1,
        weekdays=weekdays or "",
        specific_dates=specific_dates or "",
        timing_template=timing_template or "fixed",
        meal_name=meal_name or "",
        meal_offset_minutes=meal_offset_minutes or 0,
        inventory_item_id=inventory_item_id,
        consume_units_per_dose=float(consume_units_per_dose or parse_dose_amount(dose)[0]),
        consume_unit_name=consume_unit_name or parse_dose_amount(dose)[1],
    )
    refresh_schedule_need_fields(item)
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
    if sched.end_date and day > sched.end_date:
        return False
    if day < start:
        return False
    recurrence_type = (getattr(sched, "recurrence_type", None) or "daily")
    if recurrence_type == "specific_dates":
        values = {x.strip() for x in (getattr(sched, "specific_dates", "") or "").split(",") if x.strip()}
        return day.isoformat() in values
    weekdays = (getattr(sched, "weekdays", "") or "").strip()
    if weekdays:
        allowed = {int(x) for x in weekdays.split(",") if x.strip().isdigit()}
        if allowed and day.weekday() not in allowed:
            return False
    if recurrence_type == "monthly":
        return day.day == start.day
    interval = int(getattr(sched, "recurrence_interval_days", None) or (7 if recurrence_type == "weekly" else 1))
    delta_days = (day - start).days
    return delta_days % max(1, interval) == 0


async def ensure_events(session: AsyncSession, days_ahead: int = 14) -> None:
    await complete_expired_medicine_courses(session)
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
            hhmm = await schedule_due_hhmm(session, sched, day)
            due = local_dt(day, hhmm)
            exists = (await session.execute(
                select(DoseEvent.id).where(DoseEvent.schedule_id == sched.id, DoseEvent.due_at == due)
            )).first()
            if not exists:
                session.add(DoseEvent(schedule_id=sched.id, due_at=due))
    await session.commit()


async def get_today_events(session: AsyncSession, profile_id: int | None = None, target_date: date | None = None) -> list[DoseEvent]:
    now = datetime.now(TZ)
    day = target_date or now.date()
    start = datetime.combine(day, time.min, tzinfo=TZ)
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
        await adjust_inventory_for_event(session, event, -1)
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
    prev_status = event.status
    event.status = "taken"
    event.taken_at = actual_dt
    event.taken_by = tg_id
    event.skipped_at = None
    event.skipped_by = None
    event.postponed_until = None
    event.note = note
    if prev_status != "taken":
        await adjust_inventory_for_event(session, event, -1)
    await session.commit()
    return event


async def mark_skipped(session: AsyncSession, event_id: int, tg_id: int, note: str = "") -> DoseEvent | None:
    event = await get_event(session, event_id)
    if not event:
        return None
    prev_status = event.status
    if prev_status == "taken":
        await adjust_inventory_for_event(session, event, +1)
    event.status = "skipped"
    event.skipped_at = datetime.now(TZ)
    event.skipped_by = tg_id
    event.taken_at = None
    event.taken_by = None
    event.postponed_until = None
    event.note = note
    await session.commit()
    return event


async def snooze_event(session: AsyncSession, event_id: int, tg_id: int, minutes: int | None = None) -> DoseEvent | None:
    event = await get_event(session, event_id)
    if not event:
        return None
    if event.status == "taken":
        await adjust_inventory_for_event(session, event, +1)
    event.status = "pending"
    event.taken_at = None
    event.taken_by = None
    event.skipped_at = None
    event.skipped_by = None
    event.postponed_until = datetime.now(TZ) + timedelta(minutes=minutes or settings.snooze_minutes)
    event.last_reminded_at = event.postponed_until
    event.note = f"Отложено пользователем {tg_id}"
    await session.commit()
    return event



async def update_taken_time(session: AsyncSession, event_id: int, tg_id: int, actual_time: str) -> DoseEvent | None:
    event = await get_event(session, event_id)
    if not event:
        return None
    prev_status = event.status
    event.status = "taken"
    event.taken_at = local_dt(event.due_at.astimezone(TZ).date(), actual_time)
    event.taken_by = tg_id
    event.skipped_at = None
    event.skipped_by = None
    event.postponed_until = None
    if prev_status != "taken":
        await adjust_inventory_for_event(session, event, -1)
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
    prev_status = event.status

    # Keep inventory idempotent when user changes status back/forth.
    if prev_status == "taken" and status != "taken":
        await adjust_inventory_for_event(session, event, +1)
    elif prev_status != "taken" and status == "taken":
        await adjust_inventory_for_event(session, event, -1)

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
    course_id: int | None = None,
    medicine_course_id: int | None = None,
    weekdays: str | None = None,
    specific_dates: str | None = None,
    timing_template: str | None = None,
    meal_name: str | None = None,
    meal_offset_minutes: int | None = None,
    inventory_item_id: int | None = None,
    consume_units_per_dose: float | None = None,
    consume_unit_name: str | None = None,
    dosage_form: str | None = None,
    administration_route: str | None = None,
    analogs: str | None = None,
    duration_value: int | None = None,
    duration_unit: str | None = None,
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
    if start_date and duration_value:
        computed_end = calc_end_date_from_duration_value(start_date, duration_value, duration_unit)
        sched.end_date = computed_end or end_date
    else:
        sched.end_date = end_date
    if recurrence_type is not None:
        sched.recurrence_type = recurrence_type or "daily"
    if recurrence_interval_days is not None:
        sched.recurrence_interval_days = recurrence_interval_days or 1
    sched.course_id = course_id
    if medicine_course_id is not None:
        sched.medicine_course_id = medicine_course_id
    sched.weekdays = weekdays or ""
    sched.specific_dates = specific_dates or ""
    sched.timing_template = timing_template or "fixed"
    sched.meal_name = meal_name or ""
    sched.meal_offset_minutes = meal_offset_minutes or 0
    sched.dosage_form = dosage_form or ""
    sched.administration_route = administration_route or ""
    sched.analogs = analogs or ""
    sched.duration_value = duration_value
    sched.duration_unit = duration_unit or ""
    # inventory_item_id is intentionally not exposed in UI anymore; inventory is matched
    # by the selected medicine from "Лекарства" / medicine_id and then by name.
    sched.inventory_item_id = None
    refresh_schedule_need_fields(sched)
    if getattr(sched, "medicine_course_id", None):
        mc = (await session.execute(select(MedicineCourse).where(MedicineCourse.id == sched.medicine_course_id))).scalar_one_or_none()
        if mc:
            mc.assignment_id = course_id
            mc.medicine_id = med.id
            mc.name = med.name
            mc.dose = dose
            mc.start_date = start_date
            mc.end_date = sched.end_date
            mc.recurrence_type = sched.recurrence_type
            mc.recurrence_interval_days = sched.recurrence_interval_days
            mc.weekdays = sched.weekdays or ""
            mc.specific_dates = sched.specific_dates or ""
            mc.timing_template = sched.timing_template or "fixed"
            mc.dosage_form = sched.dosage_form or ""
            mc.administration_route = sched.administration_route or ""
            mc.analogs = sched.analogs or ""
            mc.duration_value = duration_value
            mc.duration_unit = duration_unit or ""
            mc.active = bool(sched.active)
            mc.status = "active" if sched.active else ("completed" if mc.end_date and mc.end_date < datetime.now(TZ).date() else "draft")
            sync_medicine_course_need_fields(mc)
    # A schedule that belongs to an assignment is a course row. It becomes active
    # automatically only when its start date is today/past; otherwise it is started
    # manually by the "Начать курс" action.
    if course_id:
        today = datetime.now(TZ).date()
        sched.active = bool(start_date and start_date <= today and not (sched.end_date and sched.end_date < today))
    else:
        sched.active = True
    if getattr(sched, "medicine_course_id", None):
        mc = (await session.execute(select(MedicineCourse).where(MedicineCourse.id == sched.medicine_course_id))).scalar_one_or_none()
        if mc:
            mc.active = bool(sched.active)
            mc.status = "active" if sched.active else ("completed" if mc.end_date and mc.end_date < datetime.now(TZ).date() else "draft")
            sync_medicine_course_need_fields(mc)

    # Старые будущие pending-события удаляем, чтобы пересоздать их по новому времени/курсу.
    await rebuild_future_events_for_schedule(session, schedule_id)
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


async def adjust_inventory_for_event(session: AsyncSession, event: DoseEvent, delta: int = -1) -> None:
    """Adjust stock by configured units per dose when inventory item exists."""
    if not event or not event.schedule or not event.schedule.medicine:
        return
    item = await inventory_item_for_schedule(session, event.schedule)
    if item:
        amount = float(getattr(event.schedule, "consume_units_per_dose", 1) or 1)
        # Current stock column is integer in earlier deployments, so keep safe integer math.
        item.quantity = max(0, int(round(float(item.quantity or 0) + float(delta) * amount)))


async def get_overdue_for_parent_alert(session: AsyncSession) -> list[DoseEvent]:
    now = datetime.now(TZ)
    cutoff = now - timedelta(minutes=settings.overdue_alert_minutes)
    q = select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(
        Schedule.active == True,
        DoseEvent.status == "pending",
        DoseEvent.due_at <= cutoff,
        or_(DoseEvent.postponed_until.is_(None), DoseEvent.postponed_until <= now),
        DoseEvent.overdue_alert_sent_at.is_(None),
    ).order_by(DoseEvent.due_at).limit(50)
    return list((await session.execute(q)).scalars().all())


def overdue_alert_text(events: list[DoseEvent]) -> str:
    if not events:
        return ""
    due = events[0].due_at.astimezone(TZ).strftime("%H:%M")
    if len(events) == 1:
        return f"⚠️ Прием просрочен больше чем на {settings.overdue_alert_minutes} мин:\n{event_title(events[0])}\nПлан: {due}"
    lines = [f"⚠️ Группа приемов на {due} просрочена больше чем на {settings.overdue_alert_minutes} мин:"]
    lines += [f"• {event_title(e)}" for e in events]
    return "\n".join(lines)


def skipped_notification_text(events: list[DoseEvent]) -> str:
    if not events:
        return ""
    due = events[0].due_at.astimezone(TZ).strftime("%H:%M")
    if len(events) == 1:
        return f"⏭️ Прием отмечен как пропущенный:\n{event_title(events[0])}\nПлан: {due}"
    return "\n".join([f"⏭️ Группа приемов на {due} отмечена как пропущенная:"] + [f"• {event_title(e)}" for e in events])


async def get_evening_summary(session: AsyncSession, profile_id: int, day: date | None = None) -> dict:
    day = day or datetime.now(TZ).date()
    start = datetime.combine(day, time.min, tzinfo=TZ)
    end = start + timedelta(days=1)
    rows = list((await session.execute(
        select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(
            Schedule.profile_id == profile_id,
            DoseEvent.due_at >= start,
            DoseEvent.due_at < end,
        ).order_by(DoseEvent.due_at, DoseEvent.id)
    )).scalars().all())
    return {
        "date": day,
        "events": rows,
        "total": len(rows),
        "taken": sum(1 for e in rows if e.status == "taken"),
        "skipped": sum(1 for e in rows if e.status == "skipped"),
        "pending": sum(1 for e in rows if e.status == "pending"),
    }


def evening_summary_text(profile: Profile, summary: dict) -> str:
    rows = summary["events"]
    date_s = summary["date"].strftime("%d.%m.%Y")
    lines = [f"🌙 Итог дня · {profile.name} · {date_s}", f"✅ Принято: {summary['taken']}  ⏭️ Пропущено: {summary['skipped']}  ⏳ Не отмечено: {summary['pending']}  Всего: {summary['total']}"]
    if rows:
        missed = [e for e in rows if e.status != "taken"]
        if missed:
            lines.append("\nЧто осталось не принято/пропущено:")
            for e in missed[:10]:
                lines.append(f"• {e.due_at.astimezone(TZ).strftime('%H:%M')} — {event_title(e)} — {'пропущено' if e.status=='skipped' else 'не отмечено'}")
    return "\n".join(lines)


async def get_inventory(session: AsyncSession, profile_id: int, search: str = "") -> list[InventoryItem]:
    q = select(InventoryItem).where(InventoryItem.profile_id == profile_id, InventoryItem.active == True).order_by(InventoryItem.name)
    if search:
        q = q.where(InventoryItem.name.ilike(f"%{search}%"))
    return list((await session.execute(q)).scalars().all())


async def low_stock_items(session: AsyncSession) -> list[InventoryItem]:
    today_start = datetime.combine(datetime.now(TZ).date(), time.min, tzinfo=TZ)
    q = select(InventoryItem).where(
        InventoryItem.active == True,
        InventoryItem.quantity <= InventoryItem.low_threshold,
        or_(InventoryItem.purchase_alert_sent_at.is_(None), InventoryItem.purchase_alert_sent_at < today_start),
    ).order_by(InventoryItem.profile_id, InventoryItem.name)
    return list((await session.execute(q)).scalars().all())


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


async def get_stats(session: AsyncSession, medicine_id: int | None = None, days: int = 30, profile_id: int | None = None, course_id: int | None = None) -> list[dict]:
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
    if course_id:
        q = q.where(Schedule.course_id == course_id)
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
