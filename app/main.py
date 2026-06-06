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

from .bot import bot, dp, take_keyboard
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
    thanks_text,
)

settings = get_settings()
TZ = ZoneInfo(settings.timezone)
app = FastAPI(title="MedKid Telegram Bot")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
scheduler = AsyncIOScheduler(timezone=settings.timezone)
polling_task: asyncio.Task | None = None


class AddSchedulePayload(BaseModel):
    name: str
    dose: str
    time_local: str
    label: str = ""


def validate_init_data(init_data: str) -> int | None:
    """Return Telegram user_id when initData is valid; None for local/dev empty initData."""
    if not init_data:
        return None
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


async def reminder_tick() -> None:
    async with SessionLocal() as session:
        await ensure_events(session)
        due = await get_due_for_reminder(session)
        for event in due:
            text = reminder_text(event)
            recipients = []
            if settings.child:
                recipients.append(settings.child)
            recipients.extend(settings.parents)
            recipients = list(dict.fromkeys(recipients))
            for chat_id in recipients:
                try:
                    await bot.send_message(chat_id, text, reply_markup=take_keyboard(event.id))
                except Exception:
                    pass
            event.reminder_count += 1
            event.last_reminded_at = datetime.now(TZ)
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


@app.get("/api/today", response_class=ORJSONResponse)
async def api_today(request: Request):
    validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    async with SessionLocal() as session:
        await ensure_events(session)
        events = await get_today_events(session)
        return [
            {
                "id": e.id,
                "time": e.due_at.astimezone(TZ).strftime("%H:%M"),
                "title": event_title(e),
                "status": e.status,
                "taken_at": e.taken_at.astimezone(TZ).strftime("%H:%M") if e.taken_at else None,
            }
            for e in events
        ]


@app.post("/api/events/{event_id}/take")
async def api_take(event_id: int, request: Request):
    tg_id = validate_init_data(request.headers.get("X-Telegram-Init-Data", "")) or 0
    async with SessionLocal() as session:
        event = await mark_taken(session, event_id, tg_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    text = thanks_text()
    for parent_id in settings.parents:
        try:
            await bot.send_message(parent_id, f"✅ В мини-приложении отмечен прием: {event_title(event)}")
        except Exception:
            pass
    return {"ok": True, "message": text}


@app.get("/api/schedules", response_class=ORJSONResponse)
async def api_schedules(request: Request):
    validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Schedule).options(selectinload(Schedule.medicine)).where(Schedule.active == True).order_by(Schedule.time_local)  # noqa: E712
        )).scalars().all()
        return [
            {"id": r.id, "name": r.medicine.name, "dose": r.dose, "time_local": r.time_local, "label": r.label}
            for r in rows
        ]


@app.post("/api/schedules")
async def api_add_schedule(payload: AddSchedulePayload, request: Request):
    tg_id = validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    if settings.parents and tg_id not in settings.parents:
        raise HTTPException(status_code=403, detail="Only parents can add schedules")
    async with SessionLocal() as session:
        med = (await session.execute(select(Medicine).where(Medicine.name == payload.name))).scalar_one_or_none()
        if not med:
            med = Medicine(name=payload.name, default_dose=payload.dose)
            session.add(med)
            await session.flush()
        sched = Schedule(medicine_id=med.id, dose=payload.dose, time_local=payload.time_local, label=payload.label or payload.time_local)
        session.add(sched)
        await session.commit()
        await ensure_events(session)
        return {"ok": True, "id": sched.id}
