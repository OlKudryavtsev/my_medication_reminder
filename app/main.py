from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .bot import bot, dp, take_keyboard, group_take_keyboard
from .config import get_settings
from .db import init_db, SessionLocal, DoseEvent, Schedule, Medicine
from .service import (
    seed_default_schedule,
    ensure_events,
    get_due_for_reminder,
    get_today_events,
    reminder_text,
    event_title,
    mark_taken,
    mark_skipped,
    snooze_event,
    update_taken_time,
    set_event_status,
    update_schedule,
    thanks_text,
    get_stats,
    get_history_for_medicine,
    parse_date_or_none,
    group_key_for_due,
    group_reminder_text,
)

settings = get_settings()
TZ = ZoneInfo(settings.timezone)
app = FastAPI(title="MedKid Telegram Bot")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
scheduler = AsyncIOScheduler(timezone=settings.timezone)
polling_task: asyncio.Task | None = None


class ScheduleEntryPayload(BaseModel):
    time_local: str
    label: str = ""


class AddSchedulePayload(BaseModel):
    name: str
    dose: str
    time_local: str | None = None
    label: str = ""
    start_date: str | None = None
    end_date: str | None = None
    recurrence_type: str = "daily"
    recurrence_interval_days: int = 1
    entries: list[ScheduleEntryPayload] | None = None


class TakePayload(BaseModel):
    actual_time: str | None = None


class EventActionPayload(BaseModel):
    note: str = ""


class StatusPayload(BaseModel):
    status: str
    actual_time: str | None = None


def role_for_tg_id(tg_id: int | None) -> str:
    if tg_id is None:
        return "unknown"
    if settings.child and tg_id == settings.child:
        return "child"
    if tg_id in settings.parents:
        return "parent"
    return "unknown"


def validate_init_data(init_data: str) -> int | None:
    """Return Telegram user_id when initData is valid. Empty initData is allowed only in dev mode."""
    if not init_data:
        if settings.allow_dev_initdata:
            return settings.parents[0] if settings.parents else settings.child
        raise HTTPException(status_code=401, detail="Telegram initData is required")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing Telegram hash")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(status_code=401, detail="Bad Telegram initData")
    user_raw = pairs.get("user")
    if not user_raw:
        return None
    return int(json.loads(user_raw)["id"])


def require_known(request: Request) -> tuple[int, str]:
    tg_id = validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    role = role_for_tg_id(tg_id)
    if role == "unknown" or tg_id is None:
        raise HTTPException(status_code=403, detail="Access denied for unknown user")
    return tg_id, role


def require_parent(request: Request) -> int:
    tg_id, role = require_known(request)
    if role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can use administration")
    return tg_id


async def reminder_tick() -> None:
    async with SessionLocal() as session:
        await ensure_events(session)
        due = await get_due_for_reminder(session)
        groups: dict[datetime, list] = {}
        for event in due:
            groups.setdefault(event.due_at, []).append(event)

        recipients = []
        if settings.child:
            recipients.append(settings.child)
        recipients.extend(settings.parents)
        recipients = list(dict.fromkeys(recipients))

        for due_at, events in groups.items():
            if len(events) == 1:
                event = events[0]
                text = reminder_text(event)
                keyboard = take_keyboard(event.id)
            else:
                group_key = group_key_for_due(due_at)
                text = group_reminder_text(events)
                keyboard = group_take_keyboard(group_key)

            for chat_id in recipients:
                try:
                    await bot.send_message(chat_id, text, reply_markup=keyboard)
                except Exception:
                    pass

            now = datetime.now(TZ)
            for event in events:
                event.reminder_count += 1
                event.last_reminded_at = now
        await session.commit()


@app.on_event("startup")
async def startup() -> None:
    global polling_task
    await init_db()
    async with SessionLocal() as session:
        await seed_default_schedule(session)
        await ensure_events(session)
    scheduler.add_job(reminder_tick, "interval", minutes=1, id="reminder_tick", replace_existing=True)
    scheduler.start()
    polling_task = asyncio.create_task(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()))


@app.on_event("shutdown")
async def shutdown() -> None:
    if polling_task:
        polling_task.cancel()
    scheduler.shutdown(wait=False)
    await bot.session.close()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/app")
async def mini_app() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/api/me", response_class=ORJSONResponse)
async def api_me(request: Request):
    tg_id, role = require_known(request)
    return {"tg_id": tg_id, "role": role, "is_parent": role == "parent", "is_child": role == "child"}


@app.get("/api/today", response_class=ORJSONResponse)
async def api_today(request: Request):
    require_known(request)
    async with SessionLocal() as session:
        await ensure_events(session)
        events = await get_today_events(session)
        return [
            {
                "id": e.id,
                "time": e.due_at.astimezone(TZ).strftime("%H:%M"),
                "title": event_title(e),
                "medicine": e.schedule.medicine.name,
                "dose": e.schedule.dose,
                "label": e.schedule.label,
                "status": e.status,
                "taken_at": e.taken_at.astimezone(TZ).strftime("%H:%M") if e.taken_at else None,
                "skipped_at": e.skipped_at.astimezone(TZ).strftime("%H:%M") if e.skipped_at else None,
                "postponed_until": e.postponed_until.astimezone(TZ).strftime("%H:%M") if e.postponed_until else None,
            }
            for e in events
        ]


@app.post("/api/events/{event_id}/take")
async def api_take(event_id: int, payload: TakePayload, request: Request):
    tg_id, _ = require_known(request)
    async with SessionLocal() as session:
        event = await mark_taken(session, event_id, tg_id, actual_time=payload.actual_time)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    text = thanks_text(event.id)
    actual = event.taken_at.astimezone(TZ).strftime("%H:%M") if event.taken_at else ""
    for parent_id in settings.parents:
        if parent_id != tg_id:
            try:
                await bot.send_message(parent_id, f"✅ В мини-приложении отмечен прием: {event_title(event)}\nФактическое время: {actual}")
            except Exception:
                pass
    return {"ok": True, "message": text}


@app.patch("/api/events/{event_id}/taken-time")
async def api_update_taken_time(event_id: int, payload: TakePayload, request: Request):
    tg_id, _ = require_known(request)
    if not payload.actual_time:
        raise HTTPException(status_code=400, detail="actual_time is required")
    async with SessionLocal() as session:
        event = await update_taken_time(session, event_id, tg_id, payload.actual_time)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    actual = event.taken_at.astimezone(TZ).strftime("%H:%M") if event.taken_at else payload.actual_time
    for parent_id in settings.parents:
        if parent_id != tg_id:
            try:
                await bot.send_message(parent_id, f"✏️ В мини-приложении изменено фактическое время: {event_title(event)}\nНовое время: {actual}")
            except Exception:
                pass
    return {"ok": True, "message": f"Время изменено на {actual}"}




@app.patch("/api/events/{event_id}/status")
async def api_update_event_status(event_id: int, payload: StatusPayload, request: Request):
    tg_id, _ = require_known(request)
    status = (payload.status or "").strip().lower()
    if status not in {"pending", "taken", "skipped"}:
        raise HTTPException(status_code=400, detail="status must be pending, taken or skipped")
    if status == "taken" and payload.actual_time and not payload.actual_time.count(":") == 1:
        raise HTTPException(status_code=400, detail="actual_time must be HH:MM")
    async with SessionLocal() as session:
        event = await set_event_status(session, event_id, tg_id, status, payload.actual_time)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    label = {"pending": "не принято", "taken": "принято", "skipped": "пропущено"}[status]
    actual = event.taken_at.astimezone(TZ).strftime("%H:%M") if event.taken_at else ""
    for parent_id in settings.parents:
        if parent_id != tg_id:
            try:
                extra = f"\nФактическое время: {actual}" if status == "taken" and actual else ""
                await bot.send_message(parent_id, f"✏️ В мини-приложении изменен статус: {event_title(event)}\nНовый статус: {label}{extra}")
            except Exception:
                pass
    return {"ok": True, "message": f"Статус изменен: {label}"}


@app.post("/api/events/{event_id}/skip")
async def api_skip(event_id: int, payload: EventActionPayload, request: Request):
    tg_id, _ = require_known(request)
    async with SessionLocal() as session:
        event = await mark_skipped(session, event_id, tg_id, note=payload.note)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    for parent_id in settings.parents:
        if parent_id != tg_id:
            try:
                await bot.send_message(parent_id, f"⏭️ В мини-приложении отмечен пропуск: {event_title(event)}")
            except Exception:
                pass
    return {"ok": True, "message": "Пропуск сохранен"}


@app.post("/api/events/{event_id}/snooze")
async def api_snooze(event_id: int, payload: EventActionPayload, request: Request):
    tg_id, _ = require_known(request)
    async with SessionLocal() as session:
        event = await snooze_event(session, event_id, tg_id, settings.snooze_minutes)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    until = event.postponed_until.astimezone(TZ).strftime("%H:%M") if event.postponed_until else ""
    return {"ok": True, "message": f"Отложено до {until}"}


@app.get("/api/schedules", response_class=ORJSONResponse)
async def api_schedules(request: Request):
    require_parent(request)
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Schedule).options(selectinload(Schedule.medicine)).where(Schedule.active == True).order_by(Schedule.time_local)  # noqa: E712
        )).scalars().all()
        return [
            {
                "id": r.id,
                "name": r.medicine.name,
                "dose": r.dose,
                "time_local": r.time_local,
                "label": r.label,
                "start_date": r.start_date.isoformat() if r.start_date else "",
                "end_date": r.end_date.isoformat() if r.end_date else "",
                "recurrence_type": r.recurrence_type or "daily",
                "recurrence_interval_days": r.recurrence_interval_days or 1,
                "active": r.active,
            }
            for r in rows
        ]


@app.post("/api/schedules")
async def api_add_schedule(payload: AddSchedulePayload, request: Request):
    require_parent(request)
    start_date = parse_date_or_none(payload.start_date)
    end_date = parse_date_or_none(payload.end_date)
    recurrence_type = (payload.recurrence_type or "daily").strip().lower()
    recurrence_interval_days = int(payload.recurrence_interval_days or 1)
    if recurrence_type not in {"daily", "weekly", "monthly"}:
        raise HTTPException(status_code=400, detail="Unsupported recurrence_type")
    entries = payload.entries or []
    if not entries and payload.time_local:
        entries = [ScheduleEntryPayload(time_local=payload.time_local, label=payload.label or payload.time_local)]
    if not payload.name.strip() or not payload.dose.strip() or not entries:
        raise HTTPException(status_code=400, detail="name, dose and at least one time are required")
    async with SessionLocal() as session:
        med = (await session.execute(select(Medicine).where(Medicine.name == payload.name))).scalar_one_or_none()
        if not med:
            med = Medicine(name=payload.name, default_dose=payload.dose)
            session.add(med)
            await session.flush()
        else:
            med.default_dose = payload.dose
            med.active = True
        created_ids: list[int] = []
        seen_entries: set[tuple[str, str]] = set()
        for entry in entries:
            hhmm = (entry.time_local or "").strip()
            if not hhmm or ":" not in hhmm:
                continue
            label = (entry.label or hhmm).strip()
            entry_key = (hhmm, label)
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)

            existing = (await session.execute(
                select(Schedule.id).where(
                    Schedule.active == True,  # noqa: E712
                    Schedule.medicine_id == med.id,
                    Schedule.dose == payload.dose,
                    Schedule.time_local == hhmm,
                    Schedule.label == label,
                    Schedule.start_date == start_date,
                    Schedule.end_date == end_date,
                    Schedule.recurrence_type == recurrence_type,
                    Schedule.recurrence_interval_days == recurrence_interval_days,
                )
            )).scalar_one_or_none()
            if existing:
                created_ids.append(existing)
                continue

            sched = Schedule(
                medicine_id=med.id,
                dose=payload.dose,
                time_local=hhmm,
                label=label,
                start_date=start_date,
                end_date=end_date,
                recurrence_type=recurrence_type,
                recurrence_interval_days=recurrence_interval_days,
            )
            session.add(sched)
            await session.flush()
            created_ids.append(sched.id)
        if not created_ids:
            raise HTTPException(status_code=400, detail="No valid time entries")
        await session.commit()
        await ensure_events(session)
        return {"ok": True, "ids": created_ids, "count": len(created_ids)}


@app.put("/api/schedules/{schedule_id}")
async def api_update_schedule(schedule_id: int, payload: AddSchedulePayload, request: Request):
    require_parent(request)
    start_date = parse_date_or_none(payload.start_date)
    end_date = parse_date_or_none(payload.end_date)
    async with SessionLocal() as session:
        sched = await update_schedule(
            session,
            schedule_id=schedule_id,
            name=payload.name,
            dose=payload.dose,
            hhmm=payload.time_local,
            label=payload.label or payload.time_local,
            start_date=start_date,
            end_date=end_date,
            recurrence_type=payload.recurrence_type,
            recurrence_interval_days=payload.recurrence_interval_days,
        )
        if not sched:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return {"ok": True}


@app.delete("/api/schedules/{schedule_id}")
async def api_delete_schedule(schedule_id: int, request: Request):
    require_parent(request)
    async with SessionLocal() as session:
        sched = (await session.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
        if not sched:
            raise HTTPException(status_code=404, detail="Schedule not found")
        sched.active = False
        await session.commit()
        return {"ok": True}


@app.get("/api/stats", response_class=ORJSONResponse)
async def api_stats(request: Request, medicine_id: int | None = None, days: int = 30):
    require_known(request)
    async with SessionLocal() as session:
        return await get_stats(session, medicine_id=medicine_id, days=days)


@app.get("/api/medicines", response_class=ORJSONResponse)
async def api_medicines(request: Request):
    require_known(request)
    async with SessionLocal() as session:
        meds = (await session.execute(select(Medicine).where(Medicine.active == True).order_by(Medicine.name))).scalars().all()  # noqa: E712
        return [{"id": m.id, "name": m.name} for m in meds]


@app.get("/api/medicines/{medicine_id}/history", response_class=ORJSONResponse)
async def api_medicine_history(medicine_id: int, request: Request, days: int = 30):
    require_known(request)
    async with SessionLocal() as session:
        events = await get_history_for_medicine(session, medicine_id, days=days)
        return [
            {
                "id": e.id,
                "date": e.due_at.astimezone(TZ).strftime("%d.%m.%Y"),
                "due_time": e.due_at.astimezone(TZ).strftime("%H:%M"),
                "title": event_title(e),
                "dose": e.schedule.dose,
                "status": e.status,
                "taken_at": e.taken_at.astimezone(TZ).strftime("%H:%M") if e.taken_at else None,
                "skipped_at": e.skipped_at.astimezone(TZ).strftime("%H:%M") if e.skipped_at else None,
            }
            for e in events
        ]
