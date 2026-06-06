from __future__ import annotations

from datetime import date, datetime
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

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


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    course_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
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
    await _add_column_if_missing(conn, "schedules", "profile_id", "INTEGER")
    await _add_column_if_missing(conn, "schedules", "course_id", "INTEGER")
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
    await _add_column_if_missing(conn, "dose_events", "skipped_at", dt_type)
    await _add_column_if_missing(conn, "dose_events", "skipped_by", "BIGINT")
    await _add_column_if_missing(conn, "dose_events", "postponed_until", dt_type)
    await _add_column_if_missing(conn, "dose_events", "overdue_alert_sent_at", dt_type)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_light_migrations(conn)
