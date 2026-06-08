from __future__ import annotations

from datetime import date, datetime
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Float, Integer, LargeBinary, String, Text, UniqueConstraint, func, text, select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload

from .config import get_settings


def normalize_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


settings = get_settings()
engine = create_async_engine(normalize_db_url(settings.database_url), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(20), default="unknown")  # parent/child/unknown
    active_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Family(Base):
    __tablename__ = "families"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="Семья")
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FamilyMember(Base):
    __tablename__ = "family_members"
    __table_args__ = (UniqueConstraint("family_id", "user_id", name="uq_family_user"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="parent")  # owner/parent/child/viewer
    linked_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FamilyInvite(Base):
    __tablename__ = "family_invites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="parent")
    target_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationSetting(Base):
    __tablename__ = "notification_settings"
    __table_args__ = (UniqueConstraint("family_member_id", "profile_id", name="uq_member_profile_notifications"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_member_id: Mapped[int] = mapped_column(ForeignKey("family_members.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int] = mapped_column(Integer, index=True)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    taken_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    skipped_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    overdue_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    low_stock_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(20), default="personal")  # child/personal
    owner_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TreatmentCourse(Base):
    __tablename__ = "treatment_courses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    doctor: Mapped[str] = mapped_column(String(255), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    assignment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TreatmentAttachment(Base):
    __tablename__ = "treatment_attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("treatment_courses.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    medicine_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    unit_name: Mapped[str] = mapped_column(String(50), default="шт")
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    low_threshold: Mapped[int] = mapped_column(Integer, default=5)
    photo_filename: Mapped[str] = mapped_column(String(255), default="")
    photo_content_type: Mapped[str] = mapped_column(String(120), default="")
    photo_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    purchase_alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MealOverride(Base):
    __tablename__ = "meal_overrides"
    __table_args__ = (UniqueConstraint("profile_id", "meal_date", "meal_name", name="uq_profile_meal_day"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    meal_date: Mapped[date] = mapped_column(Date, index=True)
    meal_name: Mapped[str] = mapped_column(String(20), default="lunch")  # breakfast/lunch/dinner
    time_local: Mapped[str] = mapped_column(String(5))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Medicine(Base):
    __tablename__ = "medicines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    default_dose: Mapped[str] = mapped_column(String(255), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class MedicineCourse(Base):
    __tablename__ = "medicine_courses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    assignment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    medicine_id: Mapped[int] = mapped_column(ForeignKey("medicines.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    dose: Mapped[str] = mapped_column(String(255), default="")
    dosage_form: Mapped[str] = mapped_column(String(100), default="")
    administration_route: Mapped[str] = mapped_column(String(255), default="")
    analogs: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    duration_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_unit: Mapped[str] = mapped_column(String(20), default="")
    recurrence_type: Mapped[str] = mapped_column(String(20), default="daily")
    recurrence_interval_days: Mapped[int] = mapped_column(Integer, default=1)
    weekdays: Mapped[str] = mapped_column(String(40), default="")
    specific_dates: Mapped[str] = mapped_column(Text, default="")
    timing_template: Mapped[str] = mapped_column(String(40), default="fixed")
    planned_doses_count: Mapped[int] = mapped_column(Integer, default=0)
    planned_units_total: Mapped[float] = mapped_column(Float, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/active/completed/cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    medicine: Mapped[Medicine] = relationship()


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    course_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    medicine_course_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    medicine_id: Mapped[int] = mapped_column(ForeignKey("medicines.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(255), default="")
    dose: Mapped[str] = mapped_column(String(255), default="")
    time_local: Mapped[str] = mapped_column(String(5))  # HH:MM
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    recurrence_type: Mapped[str] = mapped_column(String(20), default="daily")  # daily/weekly/monthly
    recurrence_interval_days: Mapped[int] = mapped_column(Integer, default=1)
    weekdays: Mapped[str] = mapped_column(String(40), default="")  # 0,1,2 for Mon..Sun
    specific_dates: Mapped[str] = mapped_column(Text, default="")  # YYYY-MM-DD,YYYY-MM-DD
    timing_template: Mapped[str] = mapped_column(String(40), default="fixed")  # fixed/before_meal/with_meal/after_meal
    meal_name: Mapped[str] = mapped_column(String(20), default="")  # breakfast/lunch/dinner
    meal_offset_minutes: Mapped[int] = mapped_column(Integer, default=0)
    inventory_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    consume_units_per_dose: Mapped[float] = mapped_column(Float, default=1.0)
    consume_unit_name: Mapped[str] = mapped_column(String(50), default="")
    planned_doses_count: Mapped[int] = mapped_column(Integer, default=0)
    planned_units_total: Mapped[float] = mapped_column(Float, default=0.0)
    dosage_form: Mapped[str] = mapped_column(String(100), default="")
    administration_route: Mapped[str] = mapped_column(String(255), default="")
    analogs: Mapped[str] = mapped_column(Text, default="")
    duration_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_unit: Mapped[str] = mapped_column(String(20), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    medicine: Mapped[Medicine] = relationship()


class DoseEvent(Base):
    __tablename__ = "dose_events"
    __table_args__ = (UniqueConstraint("schedule_id", "due_at", name="uq_schedule_due"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/taken/skipped
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    taken_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skipped_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    postponed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overdue_alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    schedule: Mapped[Schedule] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), default="")
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def _column_exists(conn, table: str, column: str) -> bool:
    result = await conn.execute(text("""
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_name = :table AND column_name = :column
    """), {"table": table, "column": column})
    try:
        return bool(result.scalar())
    except Exception:
        return False


async def _sqlite_column_exists(conn, table: str, column: str) -> bool:
    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
    return any(row[1] == column for row in rows)


async def _add_column_if_missing(conn, table: str, column: str, ddl_type: str) -> None:
    dialect = conn.dialect.name
    exists = await (_sqlite_column_exists(conn, table, column) if dialect == "sqlite" else _column_exists(conn, table, column))
    if not exists:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


async def run_light_migrations(conn) -> None:
    dialect = conn.dialect.name
    date_type = "DATE"
    dt_type = "TIMESTAMP WITH TIME ZONE" if dialect != "sqlite" else "DATETIME"
    await _add_column_if_missing(conn, "users", "active_profile_id", "INTEGER")
    await _add_column_if_missing(conn, "profiles", "family_id", "INTEGER")
    await _add_column_if_missing(conn, "schedules", "profile_id", "INTEGER")
    await _add_column_if_missing(conn, "schedules", "course_id", "INTEGER")

    await _add_column_if_missing(conn, "schedules", "medicine_course_id", "INTEGER")
    await _add_column_if_missing(conn, "treatment_courses", "assignment_date", date_type)
    await _add_column_if_missing(conn, "schedules", "start_date", date_type)
    await _add_column_if_missing(conn, "schedules", "end_date", date_type)
    await _add_column_if_missing(conn, "schedules", "recurrence_type", "VARCHAR(20) DEFAULT 'daily'")
    await _add_column_if_missing(conn, "schedules", "recurrence_interval_days", "INTEGER DEFAULT 1")
    await _add_column_if_missing(conn, "schedules", "weekdays", "VARCHAR(40) DEFAULT ''")
    await _add_column_if_missing(conn, "schedules", "specific_dates", "TEXT DEFAULT ''")
    await _add_column_if_missing(conn, "schedules", "timing_template", "VARCHAR(40) DEFAULT 'fixed'")
    await _add_column_if_missing(conn, "schedules", "meal_name", "VARCHAR(20) DEFAULT ''")
    await _add_column_if_missing(conn, "schedules", "meal_offset_minutes", "INTEGER DEFAULT 0")
    await _add_column_if_missing(conn, "schedules", "inventory_item_id", "INTEGER")
    await _add_column_if_missing(conn, "schedules", "consume_units_per_dose", "DOUBLE PRECISION DEFAULT 1")
    await _add_column_if_missing(conn, "schedules", "consume_unit_name", "VARCHAR(50) DEFAULT ''")
    await _add_column_if_missing(conn, "schedules", "planned_doses_count", "INTEGER DEFAULT 0")
    await _add_column_if_missing(conn, "schedules", "planned_units_total", "DOUBLE PRECISION DEFAULT 0")
    await _add_column_if_missing(conn, "schedules", "dosage_form", "VARCHAR(100) DEFAULT ''")
    await _add_column_if_missing(conn, "schedules", "administration_route", "VARCHAR(255) DEFAULT ''")
    await _add_column_if_missing(conn, "schedules", "analogs", "TEXT DEFAULT ''")
    await _add_column_if_missing(conn, "schedules", "duration_value", "INTEGER")
    await _add_column_if_missing(conn, "schedules", "duration_unit", "VARCHAR(20) DEFAULT ''")
    await _add_column_if_missing(conn, "medicine_courses", "status", "VARCHAR(20) DEFAULT 'draft'")
    await _add_column_if_missing(conn, "dose_events", "skipped_at", dt_type)
    await _add_column_if_missing(conn, "dose_events", "skipped_by", "BIGINT")
    await _add_column_if_missing(conn, "dose_events", "postponed_until", dt_type)
    await _add_column_if_missing(conn, "dose_events", "overdue_alert_sent_at", dt_type)


async def _data_score_for_profile(session: AsyncSession, profile_id: int) -> int:
    """Small heuristic used only for safe legacy de-duplication."""
    total = 0
    for model in (TreatmentCourse, Schedule, InventoryItem, MedicineCourse):
        try:
            total += int((await session.execute(select(func.count(model.id)).where(model.profile_id == profile_id))).scalar() or 0)
        except Exception:
            pass
    try:
        total += int((await session.execute(
            select(func.count(DoseEvent.id)).join(Schedule, Schedule.id == DoseEvent.schedule_id).where(Schedule.profile_id == profile_id)
        )).scalar() or 0)
    except Exception:
        pass
    return total


async def _move_profile_data(session: AsyncSession, src_id: int, dst_id: int) -> None:
    if src_id == dst_id:
        return
    await session.execute(update(TreatmentCourse).where(TreatmentCourse.profile_id == src_id).values(profile_id=dst_id))
    await session.execute(update(Schedule).where(Schedule.profile_id == src_id).values(profile_id=dst_id))
    await session.execute(update(InventoryItem).where(InventoryItem.profile_id == src_id).values(profile_id=dst_id))
    await session.execute(update(MealOverride).where(MealOverride.profile_id == src_id).values(profile_id=dst_id))
    await session.execute(update(AuditLog).where(AuditLog.profile_id == src_id).values(profile_id=dst_id))
    await session.execute(update(MedicineCourse).where(MedicineCourse.profile_id == src_id).values(profile_id=dst_id))
    await session.execute(update(FamilyMember).where(FamilyMember.linked_profile_id == src_id).values(linked_profile_id=dst_id))
    await session.execute(update(User).where(User.active_profile_id == src_id).values(active_profile_id=dst_id))
    prof = (await session.execute(select(Profile).where(Profile.id == src_id))).scalar_one_or_none()
    if prof:
        prof.active = False


async def _merge_family(session: AsyncSession, src: Family, dst: Family) -> None:
    """Move everything from duplicate family to target family without deleting rows."""
    if not src or not dst or src.id == dst.id:
        return
    # Profiles are moved as-is first; a separate profile consolidation step may merge safe duplicates.
    for p in (await session.execute(select(Profile).where(Profile.family_id == src.id))).scalars().all():
        p.family_id = dst.id
    for inv in (await session.execute(select(FamilyInvite).where(FamilyInvite.family_id == src.id))).scalars().all():
        inv.family_id = dst.id
    src_members = (await session.execute(select(FamilyMember).where(FamilyMember.family_id == src.id))).scalars().all()
    for m in src_members:
        existing = (await session.execute(select(FamilyMember).where(
            FamilyMember.family_id == dst.id,
            FamilyMember.user_id == m.user_id,
            FamilyMember.active == True,
        ).order_by(FamilyMember.id))).scalars().first()
        if existing:
            order = {"viewer": 0, "child": 1, "parent": 2, "owner": 3}
            if order.get(m.role, 0) > order.get(existing.role, 0):
                existing.role = m.role
            if not existing.linked_profile_id and m.linked_profile_id:
                existing.linked_profile_id = m.linked_profile_id
            # Move notification settings if possible; ignore duplicates.
            for st in (await session.execute(select(NotificationSetting).where(NotificationSetting.family_member_id == m.id))).scalars().all():
                dup = (await session.execute(select(NotificationSetting).where(
                    NotificationSetting.family_member_id == existing.id,
                    NotificationSetting.profile_id == st.profile_id,
                ).order_by(NotificationSetting.id))).scalars().first()
                if not dup:
                    st.family_member_id = existing.id
            m.active = False
        else:
            m.family_id = dst.id
            m.active = True
    src.active = False


async def _configured_users(session: AsyncSession) -> dict[int, User]:
    configured = []
    configured.extend((tg, "parent") for tg in settings.parents)
    if settings.child:
        configured.append((settings.child, "child"))
    user_by_tg: dict[int, User] = {}
    for tg_id, role in configured:
        user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        if not user:
            user = User(tg_id=tg_id, full_name="", role=role)
            session.add(user)
            await session.flush()
        else:
            if role == "parent" and user.role not in {"parent", "child"}:
                user.role = "parent"
            elif role == "child" and user.role not in {"parent", "child"}:
                user.role = "child"
        user_by_tg[tg_id] = user
    return user_by_tg


async def _find_or_create_configured_family(session: AsyncSession, user_by_tg: dict[int, User]) -> Family:
    """Find the migrated family even if the user renamed it; do not recreate by name."""
    candidates: dict[int, tuple[Family, int]] = {}

    def add_candidate(fam: Family | None, score: int) -> None:
        if not fam or not fam.active:
            return
        old = candidates.get(fam.id, (fam, 0))[1]
        candidates[fam.id] = (fam, old + score)

    # Families where configured users are members are the strongest signal.
    if user_by_tg:
        ids = [u.id for u in user_by_tg.values()]
        rows = (await session.execute(
            select(Family, FamilyMember).join(FamilyMember, FamilyMember.family_id == Family.id).where(
                Family.active == True,
                FamilyMember.active == True,
                FamilyMember.user_id.in_(ids),
            )
        )).all()
        for fam, mem in rows:
            add_candidate(fam, 100 + (20 if mem.role == "owner" else 0))

    # Families owning legacy profiles also count.
    profile_filters = []
    if settings.child:
        profile_filters.append(Profile.owner_tg_id == settings.child)
    if settings.parents:
        profile_filters.append(Profile.owner_tg_id.in_(settings.parents))
    if profile_filters:
        rows = (await session.execute(select(Profile, Family).join(Family, Family.id == Profile.family_id).where(
            Profile.active == True,
            Family.active == True,
            or_(*profile_filters),
        ))).all()
        for prof, fam in rows:
            add_candidate(fam, 30)

    # A renamed family should beat a freshly-created duplicate named "Семья по умолчанию".
    for fam, score in list(candidates.values()):
        if fam.name != "Семья по умолчанию":
            candidates[fam.id] = (fam, score + 50)

    if candidates:
        return sorted(candidates.values(), key=lambda x: (x[1], -x[0].id), reverse=True)[0][0]

    # Last fallback: existing active default by name.
    fam = (await session.execute(select(Family).where(Family.name == "Семья по умолчанию", Family.active == True).order_by(Family.id))).scalars().first()
    if fam:
        return fam

    owner_user = user_by_tg.get(settings.parents[0]) if settings.parents else None
    fam = Family(name="Семья по умолчанию", owner_user_id=owner_user.id if owner_user else None, active=True)
    session.add(fam)
    await session.flush()
    return fam


async def _consolidate_duplicate_profiles(session: AsyncSession, family: Family) -> None:
    """Safely remove profiles auto-created by buggy family migrations.

    Non-destructive: data from duplicates is moved into the kept profile.
    For child profiles, only default/empty duplicates are merged into the named child profile.
    """
    profiles = list((await session.execute(select(Profile).where(Profile.family_id == family.id, Profile.active == True))).scalars().all())

    # Personal profiles: one active personal profile per owner_tg_id inside a family.
    by_owner: dict[int, list[Profile]] = {}
    for p in profiles:
        if p.kind == "personal" and p.owner_tg_id:
            by_owner.setdefault(int(p.owner_tg_id), []).append(p)
    for owner, group in by_owner.items():
        if len(group) <= 1:
            continue
        scored = [(await _data_score_for_profile(session, p.id), p) for p in group]
        scored.sort(key=lambda x: (x[0], -x[1].id), reverse=True)
        keep = scored[0][1]
        for _, loser in scored[1:]:
            await _move_profile_data(session, loser.id, keep.id)

    await session.flush()
    profiles = list((await session.execute(select(Profile).where(Profile.family_id == family.id, Profile.active == True))).scalars().all())
    child_profiles = [p for p in profiles if p.kind == "child"]
    if len(child_profiles) > 1:
        non_default = [p for p in child_profiles if (p.name or "").strip().lower() not in {"ребенок", "ребёнок", "child"}]
        if non_default:
            # Keep the named child profile (e.g. "Максюша") and merge auto-created "Ребенок" profiles into it.
            scored = [(await _data_score_for_profile(session, p.id), p) for p in non_default]
            scored.sort(key=lambda x: (x[0], -x[1].id), reverse=True)
            keep = scored[0][1]
            for p in child_profiles:
                if p.id == keep.id:
                    continue
                if (p.name or "").strip().lower() in {"ребенок", "ребёнок", "child"} or await _data_score_for_profile(session, p.id) == 0:
                    await _move_profile_data(session, p.id, keep.id)
            # Ensure configured child member points to the kept child profile.
            if settings.child:
                user = (await session.execute(select(User).where(User.tg_id == settings.child))).scalar_one_or_none()
                if user:
                    member = (await session.execute(select(FamilyMember).where(
                        FamilyMember.family_id == family.id,
                        FamilyMember.user_id == user.id,
                        FamilyMember.active == True,
                    ).order_by(FamilyMember.id))).scalars().first()
                    if member:
                        member.linked_profile_id = keep.id


async def migrate_families() -> None:
    """Migrate/fix family model idempotently and non-destructively.

    v48 fixes a v46/v47 bug: if the default family was renamed, runtime guards
    created another active "Семья по умолчанию" and duplicate profiles. This
    migration finds the already-renamed configured family, merges duplicate
    default families into it, moves data, and deactivates safe duplicate profiles.
    """
    async with SessionLocal() as session:
        user_by_tg = await _configured_users(session)
        target_family = await _find_or_create_configured_family(session, user_by_tg)

        # Merge active duplicate "Семья по умолчанию" families that contain configured users/profiles.
        all_active = list((await session.execute(select(Family).where(Family.active == True).order_by(Family.id))).scalars().all())
        configured_user_ids = {u.id for u in user_by_tg.values()}
        for fam in all_active:
            if fam.id == target_family.id:
                continue
            should_merge = fam.name == "Семья по умолчанию"
            if not should_merge and configured_user_ids:
                member_cnt = int((await session.execute(select(func.count(FamilyMember.id)).where(
                    FamilyMember.family_id == fam.id,
                    FamilyMember.user_id.in_(configured_user_ids),
                    FamilyMember.active == True,
                ))).scalar() or 0)
                should_merge = member_cnt > 0
            if should_merge:
                await _merge_family(session, fam, target_family)

        # Attach legacy profiles without family_id to target.
        for p in (await session.execute(select(Profile).where(Profile.family_id.is_(None)))).scalars().all():
            p.family_id = target_family.id
        await session.flush()

        # Create/repair configured memberships in the target family.
        for tg_id, user in user_by_tg.items():
            role = "child" if settings.child and tg_id == settings.child else "parent"
            linked_profile = None
            if role == "child":
                linked_profile = (await session.execute(select(Profile).where(
                    Profile.family_id == target_family.id,
                    Profile.kind == "child",
                    Profile.active == True,
                    Profile.owner_tg_id == tg_id,
                ).order_by(Profile.id))).scalars().first()
                if not linked_profile:
                    # Prefer a named child if it exists; otherwise create one only if none exists.
                    linked_profile = (await session.execute(select(Profile).where(
                        Profile.family_id == target_family.id,
                        Profile.kind == "child",
                        Profile.active == True,
                        Profile.name.notin_(["Ребенок", "Ребёнок", "child"]),
                    ).order_by(Profile.id))).scalars().first()
                if not linked_profile:
                    linked_profile = (await session.execute(select(Profile).where(
                        Profile.family_id == target_family.id,
                        Profile.kind == "child",
                        Profile.active == True,
                    ).order_by(Profile.id))).scalars().first()
                if not linked_profile:
                    linked_profile = Profile(name="Ребенок", kind="child", owner_tg_id=tg_id, family_id=target_family.id, active=True)
                    session.add(linked_profile)
                    await session.flush()
                if not linked_profile.owner_tg_id:
                    linked_profile.owner_tg_id = tg_id
            member = (await session.execute(select(FamilyMember).where(
                FamilyMember.family_id == target_family.id,
                FamilyMember.user_id == user.id,
            ).order_by(FamilyMember.id))).scalars().first()
            desired_role = "child" if role == "child" else "owner" if tg_id == (settings.parents[0] if settings.parents else None) else "parent"
            if not member:
                session.add(FamilyMember(
                    family_id=target_family.id,
                    user_id=user.id,
                    role=desired_role,
                    linked_profile_id=(linked_profile.id if linked_profile else None),
                    active=True,
                ))
            else:
                member.active = True
                member.role = desired_role if member.role not in {"owner"} else member.role
                if linked_profile:
                    member.linked_profile_id = linked_profile.id

        # Create missing personal profiles for configured parents only if none exists in target family.
        for parent_id in settings.parents:
            parent_profile = (await session.execute(select(Profile).where(
                Profile.family_id == target_family.id,
                Profile.kind == "personal",
                Profile.owner_tg_id == parent_id,
                Profile.active == True,
            ).order_by(Profile.id))).scalars().first()
            if not parent_profile:
                session.add(Profile(name="Мой профиль", kind="personal", owner_tg_id=parent_id, family_id=target_family.id, active=True))

        await session.flush()
        await _consolidate_duplicate_profiles(session, target_family)
        await session.commit()


async def migrate_schedule_courses() -> None:
    """One-time/lightweight migration from old model: schedules doubled as medicine courses.

    We create a medicine_courses row for every logical course and attach existing
    schedules to it. Existing dose_events keep schedule_id, so completed intakes
    are preserved. This migration is idempotent.
    """
    async with SessionLocal() as session:
        rows = (await session.execute(select(Schedule).options(selectinload(Schedule.medicine)).where(Schedule.medicine_course_id.is_(None)))).scalars().all()
        for sched in rows:
            med = sched.medicine
            # If schedule belongs to an assignment, one medicine may occur only once inside it.
            # For legacy rows without assignment, group only identical medicine+dose+period+rules.
            q = select(MedicineCourse).where(
                MedicineCourse.profile_id == sched.profile_id,
                MedicineCourse.assignment_id == sched.course_id,
                MedicineCourse.medicine_id == sched.medicine_id,
                MedicineCourse.dose == (sched.dose or ""),
                MedicineCourse.start_date == sched.start_date,
                MedicineCourse.end_date == sched.end_date,
                MedicineCourse.recurrence_type == (sched.recurrence_type or "daily"),
                MedicineCourse.recurrence_interval_days == (sched.recurrence_interval_days or 1),
                MedicineCourse.weekdays == (sched.weekdays or ""),
                MedicineCourse.specific_dates == (sched.specific_dates or ""),
                MedicineCourse.timing_template == (sched.timing_template or "fixed"),
            )
            mc = (await session.execute(q.order_by(MedicineCourse.id))).scalars().first()
            if not mc:
                mc = MedicineCourse(
                    profile_id=sched.profile_id,
                    assignment_id=sched.course_id,
                    medicine_id=sched.medicine_id,
                    name=med.name if med else "",
                    dose=sched.dose or "",
                    dosage_form=getattr(sched, "dosage_form", "") or "",
                    administration_route=getattr(sched, "administration_route", "") or "",
                    analogs=getattr(sched, "analogs", "") or "",
                    start_date=sched.start_date,
                    end_date=sched.end_date,
                    duration_value=getattr(sched, "duration_value", None),
                    duration_unit=getattr(sched, "duration_unit", "") or "",
                    recurrence_type=sched.recurrence_type or "daily",
                    recurrence_interval_days=sched.recurrence_interval_days or 1,
                    weekdays=sched.weekdays or "",
                    specific_dates=sched.specific_dates or "",
                    timing_template=sched.timing_template or "fixed",
                    planned_doses_count=getattr(sched, "planned_doses_count", 0) or 0,
                    planned_units_total=getattr(sched, "planned_units_total", 0) or 0,
                    active=bool(sched.active),
                    status="active" if sched.active else "draft",
                )
                session.add(mc)
                await session.flush()
            if not getattr(mc, "status", None):
                mc.status = "active" if bool(mc.active) else "draft"
            sched.medicine_course_id = mc.id
        await session.commit()


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_light_migrations(conn)
    await migrate_families()
    await migrate_schedule_courses()
