from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    role: Mapped[str] = mapped_column(String(20), default="parent")  # parent/child/unknown
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
    medicine_id: Mapped[int] = mapped_column(ForeignKey("medicines.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(255), default="")
    dose: Mapped[str] = mapped_column(String(255), default="")
    time_local: Mapped[str] = mapped_column(String(5))  # HH:MM after meal offsets are materialized here
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
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    schedule: Mapped[Schedule] = relationship()


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
