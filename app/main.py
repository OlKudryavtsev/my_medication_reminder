from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
from io import BytesIO
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
from urllib.parse import parse_qsl

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, ORJSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select, or_, delete
from sqlalchemy.orm import selectinload

from .bot import bot, dp, take_keyboard, group_take_keyboard
from .config import get_settings
from .db import init_db, SessionLocal, DoseEvent, Schedule, Medicine, User, Profile, TreatmentCourse, TreatmentAttachment, InventoryItem, MedicineCourse
from .ai import ai_enabled, ask_json, MEDICINE_SCHEMA_PROMPT, PRESCRIPTION_IMAGE_PROMPT, INVENTORY_PHOTO_PROMPT, REPORT_PROMPT, AIUnavailable
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
    ensure_profiles,
    profiles_for_user,
    resolve_profile_id,
    set_active_profile,
    is_profile_manager,
    profile_recipients,
    create_child_profile,
    update_profile_name,
    deactivate_profile,
    get_audit_log,
    log_action,
    get_courses,
    create_course,
    update_course,
    deactivate_course,
    set_meal_time_for_day,
    get_meal_overrides_for_day,
    normalize_recurrence,
    schedule_due_hhmm,
    get_overdue_for_parent_alert,
    overdue_alert_text,
    skipped_notification_text,
    get_evening_summary,
    evening_summary_text,
    get_inventory,
    low_stock_items,
    parse_dose_amount,
    refresh_schedule_need_fields,
    schedule_stock_info,
    schedule_applies_on_day,
    adjust_inventory_for_event,
    local_dt,
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
    timing_template: str = "fixed"
    meal_name: str = ""
    meal_offset_minutes: int = 0


class AddSchedulePayload(BaseModel):
    name: str
    dose: str
    time_local: str | None = None
    label: str = ""
    start_date: str | None = None
    end_date: str | None = None
    recurrence_type: str = "daily"
    recurrence_interval_days: int = 1
    course_id: int | None = None
    inventory_item_id: int | None = None
    consume_units_per_dose: float | None = None
    consume_unit_name: str = ""
    weekdays: str = ""
    specific_dates: str = ""
    dosage_form: str = ""
    administration_route: str = ""
    analogs: str = ""
    duration_value: int | None = None
    duration_unit: str = ""
    entries: list[ScheduleEntryPayload] | None = None


class TakePayload(BaseModel):
    actual_time: str | None = None


class BatchEventPayload(BaseModel):
    ids: list[int]
    actual_time: str | None = None


class EventActionPayload(BaseModel):
    note: str = ""


class StatusPayload(BaseModel):
    status: str
    actual_time: str | None = None


class ActiveProfilePayload(BaseModel):
    profile_id: int


class ProfilePayload(BaseModel):
    name: str


class CoursePayload(BaseModel):
    name: str
    assignment_date: str | None = None
    doctor: str = ""
    comment: str = ""


class MealTimePayload(BaseModel):
    meal_date: str
    meal_name: str
    time_local: str


class InventoryPayload(BaseModel):
    name: str
    quantity: int = 0
    unit_name: str = "шт"
    low_threshold: int = 5


class AITextPayload(BaseModel):
    text: str


class AIReportPayload(BaseModel):
    days: int = 30


class HistoryGridPayload(BaseModel):
    start_date: str
    end_date: str
    cells: dict[str, str] = {}  # key: YYYY-MM-DD|schedule_id, value: pending/taken/skipped/none
    overwrite_mode: str = "skip_existing"  # skip_existing/pending_only/overwrite_all
    apply_inventory: bool = False
    actual_time_mode: str = "planned"  # planned/none



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


def requested_profile_id(request: Request) -> int | None:
    raw = request.headers.get("X-Profile-Id") or request.query_params.get("profile_id")
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


async def require_profile(request: Request, session: SessionLocal) -> tuple[int, str, int]:
    tg_id, role = require_known(request)
    try:
        pid = await resolve_profile_id(session, tg_id, role, requested_profile_id(request))
    except ValueError:
        raise HTTPException(status_code=403, detail="No accessible profile")
    return tg_id, role, pid


async def require_profile_manager(request: Request, session: SessionLocal) -> tuple[int, str, int]:
    tg_id, role, pid = await require_profile(request, session)
    if not await is_profile_manager(session, tg_id, role, pid):
        raise HTTPException(status_code=403, detail="No access to this profile")
    return tg_id, role, pid


async def assert_event_in_profile(session, event_id: int, profile_id: int) -> None:
    exists = (await session.execute(
        select(DoseEvent.id).join(Schedule).where(DoseEvent.id == event_id, Schedule.profile_id == profile_id)
    )).scalar_one_or_none()
    if not exists:
        raise HTTPException(status_code=404, detail="Event not found in this profile")


async def reminder_tick() -> None:
    async with SessionLocal() as session:
        await ensure_events(session)
        due = await get_due_for_reminder(session)
        groups: dict[tuple[int | None, datetime], list] = {}
        for event in due:
            groups.setdefault((event.schedule.profile_id, event.due_at), []).append(event)

        for (profile_id, due_at), events in groups.items():
            if len(events) == 1:
                event = events[0]
                text = reminder_text(event)
                keyboard = take_keyboard(event.id)
            else:
                group_key = group_key_for_due(due_at, profile_id)
                text = group_reminder_text(events)
                keyboard = group_take_keyboard(group_key)

            for chat_id in await profile_recipients(session, profile_id):
                try:
                    await bot.send_message(chat_id, text, reply_markup=keyboard)
                except Exception:
                    pass

            now = datetime.now(TZ)
            for event in events:
                event.reminder_count += 1
                event.last_reminded_at = now
        await session.commit()


async def overdue_alert_tick() -> None:
    async with SessionLocal() as session:
        events = await get_overdue_for_parent_alert(session)
        groups: dict[tuple[int | None, datetime], list] = {}
        for event in events:
            groups.setdefault((event.schedule.profile_id, event.due_at), []).append(event)
        now = datetime.now(TZ)
        for (profile_id, due_at), rows in groups.items():
            # Личный профиль родителя: тревога не нужна, напоминания и так уходят только владельцу.
            profile = (await session.execute(select(Profile).where(Profile.id == profile_id))).scalar_one_or_none()
            if profile and profile.kind == "personal":
                for e in rows:
                    e.overdue_alert_sent_at = now
                continue
            text = overdue_alert_text(rows)
            for parent_id in settings.parents:
                try:
                    await bot.send_message(parent_id, text)
                except Exception:
                    pass
            for e in rows:
                e.overdue_alert_sent_at = now
        await session.commit()


async def low_stock_tick() -> None:
    async with SessionLocal() as session:
        rows = await low_stock_items(session)
        now = datetime.now(TZ)
        for item in rows:
            profile = (await session.execute(select(Profile).where(Profile.id == item.profile_id))).scalar_one_or_none()
            if not profile:
                continue
            if profile.kind == "personal" and profile.owner_tg_id:
                recipients = [int(profile.owner_tg_id)]
            else:
                recipients = settings.parents
            text = f"🛒 Нужно купить лекарство\n{item.name}: осталось {item.quantity} {item.unit_name or 'шт'}\nПорог напоминания: {item.low_threshold}"
            for chat_id in recipients:
                try:
                    await bot.send_message(chat_id, text)
                except Exception:
                    pass
            item.purchase_alert_sent_at = now
        await session.commit()


async def evening_summary_tick() -> None:
    async with SessionLocal() as session:
        profiles = (await session.execute(select(Profile).where(Profile.active == True))).scalars().all()
        for profile in profiles:
            summary = await get_evening_summary(session, profile.id)
            if not summary["total"]:
                continue
            text = evening_summary_text(profile, summary)
            if profile.kind == "personal" and profile.owner_tg_id:
                recipients = [int(profile.owner_tg_id)]
            else:
                recipients = await profile_recipients(session, profile.id)
            for chat_id in recipients:
                try:
                    await bot.send_message(chat_id, text)
                except Exception:
                    pass


@app.on_event("startup")
async def startup() -> None:
    global polling_task
    await init_db()
    async with SessionLocal() as session:
        await ensure_profiles(session)
        await seed_default_schedule(session)
        await ensure_events(session)
    scheduler.add_job(reminder_tick, "interval", minutes=1, id="reminder_tick", replace_existing=True)
    scheduler.add_job(overdue_alert_tick, "interval", minutes=5, id="overdue_alert_tick", replace_existing=True)
    scheduler.add_job(low_stock_tick, "cron", hour=settings.low_stock_check_hour, minute=0, id="low_stock_tick", replace_existing=True)
    scheduler.add_job(evening_summary_tick, "cron", hour=23, minute=45, id="evening_summary_tick", replace_existing=True)
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
    # Telegram WebView can aggressively cache Mini App HTML/JS.
    # No-store makes UI fixes appear immediately after deploy/reload.
    return FileResponse(
        "app/static/index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/me", response_class=ORJSONResponse)
async def api_me(request: Request):
    tg_id, role = require_known(request)
    async with SessionLocal() as session:
        profiles = await profiles_for_user(session, tg_id, role)
        active_id = await resolve_profile_id(session, tg_id, role, requested_profile_id(request)) if profiles else None
    return {"tg_id": tg_id, "role": role, "is_parent": role == "parent", "is_child": role == "child", "active_profile_id": active_id, "can_manage_current_profile": role in {"parent", "child"}}


@app.get("/api/profiles", response_class=ORJSONResponse)
async def api_profiles(request: Request):
    tg_id, role = require_known(request)
    async with SessionLocal() as session:
        profiles = await profiles_for_user(session, tg_id, role)
        active_id = await resolve_profile_id(session, tg_id, role, requested_profile_id(request)) if profiles else None
        return [{"id": p.id, "name": p.name, "kind": p.kind, "owner_tg_id": p.owner_tg_id, "active": p.id == active_id} for p in profiles]


@app.post("/api/active-profile")
async def api_active_profile(payload: ActiveProfilePayload, request: Request):
    tg_id, role = require_known(request)
    async with SessionLocal() as session:
        profile = await set_active_profile(session, tg_id, role, payload.profile_id)
        if not profile:
            raise HTTPException(status_code=403, detail="Profile is not accessible")
        return {"ok": True, "profile_id": profile.id, "name": profile.name}


@app.post("/api/profiles")
async def api_create_profile(payload: ProfilePayload, request: Request):
    tg_id = require_parent(request)
    async with SessionLocal() as session:
        profile = await create_child_profile(session, payload.name, tg_id)
        return {"ok": True, "id": profile.id, "name": profile.name, "kind": profile.kind}


@app.put("/api/profiles/{profile_id}")
async def api_update_profile(profile_id: int, payload: ProfilePayload, request: Request):
    tg_id = require_parent(request)
    async with SessionLocal() as session:
        # Родитель может переименовывать детские профили и свой личный профиль.
        profiles = await profiles_for_user(session, tg_id, "parent")
        allowed = {p.id for p in profiles}
        if profile_id not in allowed:
            raise HTTPException(status_code=403, detail="Profile is not accessible")
        profile = await update_profile_name(session, profile_id, payload.name, tg_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        return {"ok": True, "id": profile.id, "name": profile.name}


@app.delete("/api/profiles/{profile_id}")
async def api_delete_profile(profile_id: int, request: Request):
    tg_id = require_parent(request)
    async with SessionLocal() as session:
        profile = (await session.execute(select(Profile).where(Profile.id == profile_id))).scalar_one_or_none()
        if not profile or profile.kind != "child":
            raise HTTPException(status_code=400, detail="Only child profiles can be deleted")
        profile = await deactivate_profile(session, profile_id, tg_id)
        return {"ok": True}


@app.get("/api/audit", response_class=ORJSONResponse)
async def api_audit(request: Request, limit: int = 50):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        rows = await get_audit_log(session, profile_id, limit=max(1, min(limit, 100)))
        return [
            {
                "id": r.id,
                "created_at": r.created_at.astimezone(TZ).strftime("%d.%m %H:%M") if r.created_at else "",
                "actor_tg_id": r.actor_tg_id,
                "action": r.action,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "details": r.details,
            }
            for r in rows
        ]




@app.get("/api/ai/status", response_class=ORJSONResponse)
async def api_ai_status(request: Request):
    # Доступен известным пользователям, чтобы фронт мог скрыть AI-блоки.
    require_known(request)
    return {"enabled": ai_enabled(), "model": settings.openai_model if ai_enabled() else ""}


@app.post("/api/ai/parse-medicine", response_class=ORJSONResponse)
async def api_ai_parse_medicine(payload: AITextPayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    try:
        data = await ask_json(MEDICINE_SCHEMA_PROMPT, user_text=payload.text.strip())
    except AIUnavailable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI parse failed: {e}")
    return data


@app.post("/api/ai/parse-prescription", response_class=ORJSONResponse)
async def api_ai_parse_prescription(request: Request, file: UploadFile = File(...)):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is too large; max 8 MB")
    try:
        result = await ask_json(PRESCRIPTION_IMAGE_PROMPT, image_bytes=data, content_type=file.content_type or "image/jpeg")
    except AIUnavailable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI parse failed: {e}")
    return result


@app.post("/api/ai/recognize-inventory-photo", response_class=ORJSONResponse)
async def api_ai_inventory_photo(request: Request, file: UploadFile = File(...)):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is too large; max 8 MB")
    try:
        result = await ask_json(INVENTORY_PHOTO_PROMPT, image_bytes=data, content_type=file.content_type or "image/jpeg")
    except AIUnavailable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI recognize failed: {e}")
    return result


@app.post("/api/ai/report-draft", response_class=ORJSONResponse)
async def api_ai_report_draft(payload: AIReportPayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        profile, stats, events = await _report_rows(session, profile_id, max(1, min(payload.days, 365)))
        facts = {
            "profile": profile.name if profile else "Профиль",
            "days": payload.days,
            "stats": stats,
            "events": [
                {
                    "date": e.due_at.astimezone(TZ).strftime("%Y-%m-%d"),
                    "time": e.due_at.astimezone(TZ).strftime("%H:%M"),
                    "medicine": e.schedule.medicine.name,
                    "dose": e.schedule.dose,
                    "status": e.status,
                    "taken_at": e.taken_at.astimezone(TZ).strftime("%H:%M") if e.taken_at else "",
                    "skipped_at": e.skipped_at.astimezone(TZ).strftime("%H:%M") if e.skipped_at else "",
                }
                for e in events[:120]
            ],
        }
    try:
        result = await ask_json(REPORT_PROMPT, user_text=json.dumps(facts, ensure_ascii=False))
    except AIUnavailable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI report failed: {e}")
    return result




def calc_end_date_from_duration(start_date, duration_value, duration_unit):
    if not start_date or not duration_value or duration_value <= 0:
        return None
    unit = (duration_unit or "days").strip()
    if unit == "months":
        return start_date + relativedelta(months=duration_value) - timedelta(days=1)
    if unit == "weeks":
        return start_date + timedelta(weeks=duration_value) - timedelta(days=1)
    return start_date + timedelta(days=duration_value) - timedelta(days=1)


async def serialize_schedule_row(session, r: Schedule, include_need: bool = False) -> dict:
    today = datetime.now(TZ).date()
    display_time = await schedule_due_hhmm(session, r, today)
    stock = await schedule_stock_info(session, r)
    data = {
        "id": r.id,
        "name": r.medicine.name,
        "dose": r.dose,
        "time_local": r.time_local,
        "display_time": display_time,
        "label": r.label,
        "start_date": r.start_date.isoformat() if r.start_date else "",
        "end_date": r.end_date.isoformat() if r.end_date else "",
        "recurrence_type": r.recurrence_type or "daily",
        "recurrence_interval_days": r.recurrence_interval_days or 1,
        "course_id": r.course_id,
        "medicine_course_id": getattr(r, "medicine_course_id", None),
        "weekdays": r.weekdays or "",
        "specific_dates": r.specific_dates or "",
        "timing_template": r.timing_template or "fixed",
        "meal_name": r.meal_name or "",
        "meal_offset_minutes": r.meal_offset_minutes or 0,
        "dosage_form": getattr(r, "dosage_form", "") or "",
        "administration_route": getattr(r, "administration_route", "") or "",
        "analogs": getattr(r, "analogs", "") or "",
        "duration_value": getattr(r, "duration_value", None),
        "duration_unit": getattr(r, "duration_unit", "") or "",
        "active": bool(r.active),
    }
    if include_need:
        data.update({
            "planned_doses_count": stock.get("planned_doses_count", 0),
            "planned_units_total": stock.get("planned_units_total", 0),
            "taken_units": stock.get("taken_units", 0),
            "remaining_need_units": stock.get("remaining_need_units", 0),
            "inventory_quantity": stock.get("inventory_quantity"),
            "shortage_units": stock.get("shortage_units"),
            "consume_unit_name": stock.get("consume_unit_name") or "шт",
        })
    return data

@app.get("/api/courses", response_class=ORJSONResponse)
async def api_courses(request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        rows = await get_courses(session, profile_id)
        result = []
        for c in rows:
            attachments = (await session.execute(select(TreatmentAttachment).where(TreatmentAttachment.course_id == c.id).order_by(TreatmentAttachment.id.desc()))).scalars().all()
            items_q = select(Schedule).options(selectinload(Schedule.medicine)).where(
                Schedule.profile_id == profile_id,
                Schedule.course_id == c.id,
            ).order_by(Schedule.active.desc(), Schedule.medicine_course_id, Schedule.time_local, Schedule.id)
            items = (await session.execute(items_q)).scalars().all()
            result.append({
                "id": c.id,
                "name": c.name,
                "assignment_date": c.assignment_date.isoformat() if c.assignment_date else "",
                "doctor": c.doctor or "",
                "comment": c.comment or "",
                "attachments": [{"id": a.id, "filename": a.filename, "content_type": a.content_type} for a in attachments],
                "items": [await serialize_schedule_row(session, item, include_need=True) for item in items],
            })
        return result


@app.get("/api/medicine-courses/{medicine_course_id}/history-grid", response_class=ORJSONResponse)
async def api_medicine_course_history_grid(medicine_course_id: int, request: Request, start_date: str, end_date: str):
    """Preview day-by-day historical intake grid for one medicine course."""
    start = parse_date_or_none(start_date)
    end = parse_date_or_none(end_date)
    if not start or not end or end < start:
        raise HTTPException(status_code=400, detail="Bad date range")
    if (end - start).days > 120:
        raise HTTPException(status_code=400, detail="Максимальный период для ввода истории — 120 дней")
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        mc = (await session.execute(select(MedicineCourse).where(MedicineCourse.id == medicine_course_id, MedicineCourse.profile_id == profile_id))).scalar_one_or_none()
        if not mc:
            raise HTTPException(status_code=404, detail="Medicine course not found")
        schedules = list((await session.execute(
            select(Schedule).options(selectinload(Schedule.medicine)).where(
                Schedule.profile_id == profile_id,
                Schedule.medicine_course_id == medicine_course_id,
            ).order_by(Schedule.time_local, Schedule.id)
        )).scalars().all())
        days = []
        cur = start
        while cur <= end:
            row = {"date": cur.isoformat(), "items": []}
            for sched in schedules:
                if not schedule_applies_on_day(sched, cur):
                    continue
                hhmm = await schedule_due_hhmm(session, sched, cur)
                due = local_dt(cur, hhmm)
                ev = (await session.execute(select(DoseEvent).where(DoseEvent.schedule_id == sched.id, DoseEvent.due_at == due))).scalar_one_or_none()
                row["items"].append({
                    "schedule_id": sched.id,
                    "time": hhmm,
                    "label": sched.label or hhmm,
                    "medicine": sched.medicine.name,
                    "dose": sched.dose,
                    "status": ev.status if ev else "none",
                    "taken_at": ev.taken_at.astimezone(TZ).strftime("%H:%M") if ev and ev.taken_at else "",
                    "skipped_at": ev.skipped_at.astimezone(TZ).strftime("%H:%M") if ev and ev.skipped_at else "",
                })
            days.append(row)
            cur += timedelta(days=1)
        return {"course_id": mc.id, "name": mc.name, "dose": mc.dose, "days": days}


@app.post("/api/medicine-courses/{medicine_course_id}/history-grid")
async def api_apply_medicine_course_history_grid(medicine_course_id: int, payload: HistoryGridPayload, request: Request):
    """Apply day-by-day historical intake statuses.

    Safety modes:
    - skip_existing: do not overwrite already created dose_events;
    - pending_only: overwrite only missing/pending dose_events;
    - overwrite_all: overwrite taken/skipped too.
    Inventory is updated only when apply_inventory=true.
    """
    start = parse_date_or_none(payload.start_date)
    end = parse_date_or_none(payload.end_date)
    if not start or not end or end < start:
        raise HTTPException(status_code=400, detail="Bad date range")
    if (end - start).days > 120:
        raise HTTPException(status_code=400, detail="Максимальный период для ввода истории — 120 дней")
    if payload.overwrite_mode not in {"skip_existing", "pending_only", "overwrite_all"}:
        raise HTTPException(status_code=400, detail="Bad overwrite_mode")
    allowed_status = {"none", "pending", "taken", "skipped"}
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        mc = (await session.execute(select(MedicineCourse).where(MedicineCourse.id == medicine_course_id, MedicineCourse.profile_id == profile_id))).scalar_one_or_none()
        if not mc:
            raise HTTPException(status_code=404, detail="Medicine course not found")
        schedules = list((await session.execute(
            select(Schedule).options(selectinload(Schedule.medicine)).where(
                Schedule.profile_id == profile_id,
                Schedule.medicine_course_id == medicine_course_id,
            ).order_by(Schedule.time_local, Schedule.id)
        )).scalars().all())
        changed = created = skipped_existing = 0
        cur = start
        while cur <= end:
            for sched in schedules:
                if not schedule_applies_on_day(sched, cur):
                    continue
                key = f"{cur.isoformat()}|{sched.id}"
                status = (payload.cells or {}).get(key, "none")
                if status not in allowed_status or status == "none":
                    continue
                hhmm = await schedule_due_hhmm(session, sched, cur)
                due = local_dt(cur, hhmm)
                ev = (await session.execute(select(DoseEvent).where(DoseEvent.schedule_id == sched.id, DoseEvent.due_at == due))).scalar_one_or_none()
                if ev:
                    if payload.overwrite_mode == "skip_existing":
                        skipped_existing += 1
                        continue
                    if payload.overwrite_mode == "pending_only" and ev.status != "pending":
                        skipped_existing += 1
                        continue
                else:
                    ev = DoseEvent(schedule_id=sched.id, due_at=due)
                    ev.schedule = sched
                    session.add(ev)
                    await session.flush()
                    created += 1
                prev_status = ev.status
                if payload.apply_inventory:
                    if prev_status == "taken" and status != "taken":
                        await adjust_inventory_for_event(session, ev, +1)
                    elif prev_status != "taken" and status == "taken":
                        await adjust_inventory_for_event(session, ev, -1)
                ev.status = status
                if status == "taken":
                    ev.taken_at = due if payload.actual_time_mode == "planned" else None
                    ev.taken_by = tg_id
                    ev.skipped_at = None
                    ev.skipped_by = None
                    ev.postponed_until = None
                    ev.note = "Исторический ввод"
                elif status == "skipped":
                    ev.skipped_at = datetime.combine(cur, time.min, tzinfo=TZ)
                    ev.skipped_by = tg_id
                    ev.taken_at = None
                    ev.taken_by = None
                    ev.postponed_until = None
                    ev.note = "Исторический ввод"
                else:
                    ev.taken_at = None
                    ev.taken_by = None
                    ev.skipped_at = None
                    ev.skipped_by = None
                    ev.postponed_until = None
                    ev.note = "Исторический ввод"
                changed += 1
            cur += timedelta(days=1)
        await log_action(session, profile_id, tg_id, "history_imported", "medicine_course", medicine_course_id, f"История приема: {start.isoformat()} — {end.isoformat()}, изменено: {changed}", commit=False)
        await session.commit()
        return {"ok": True, "changed": changed, "created": created, "skipped_existing": skipped_existing}


@app.post("/api/courses")
async def api_create_course(payload: CoursePayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        course = await create_course(
            session,
            profile_id=profile_id,
            name=payload.name,
            assignment_date=parse_date_or_none(payload.assignment_date),
            doctor=payload.doctor,
            comment=payload.comment,
            actor_tg_id=tg_id,
        )
        return {"ok": True, "id": course.id, "name": course.name}


@app.put("/api/courses/{course_id}")
async def api_update_course(course_id: int, payload: CoursePayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        course = await update_course(
            session,
            profile_id=profile_id,
            course_id=course_id,
            name=payload.name,
            assignment_date=parse_date_or_none(payload.assignment_date),
            doctor=payload.doctor,
            comment=payload.comment,
            actor_tg_id=tg_id,
        )
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        return {"ok": True, "id": course.id, "name": course.name}


@app.delete("/api/courses/{course_id}")
async def api_delete_course(course_id: int, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        course = await deactivate_course(session, profile_id=profile_id, course_id=course_id, actor_tg_id=tg_id)
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        return {"ok": True}


@app.post("/api/courses/{course_id}/attachments")
async def api_upload_course_attachment(course_id: int, request: Request, file: UploadFile = File(...)):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        course = (await session.execute(select(TreatmentCourse).where(TreatmentCourse.id == course_id, TreatmentCourse.profile_id == profile_id, TreatmentCourse.active == True))).scalar_one_or_none()
        if not course:
            raise HTTPException(status_code=404, detail="Assignment not found")
        data = await file.read()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File is too large; max 8 MB")
        att = TreatmentAttachment(course_id=course_id, filename=file.filename or "attachment", content_type=file.content_type or "application/octet-stream", data=data)
        session.add(att)
        await session.flush()
        await log_action(session, profile_id, tg_id, "assignment_file_added", "course", course_id, f"Добавлен файл к назначению: {att.filename}", commit=False)
        await session.commit()
        return {"ok": True, "id": att.id, "filename": att.filename}


@app.get("/api/attachments/{attachment_id}")
async def api_download_attachment(attachment_id: int, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        att = (await session.execute(select(TreatmentAttachment).join(TreatmentCourse, TreatmentCourse.id == TreatmentAttachment.course_id).where(TreatmentAttachment.id == attachment_id, TreatmentCourse.profile_id == profile_id, TreatmentCourse.active == True))).scalar_one_or_none()
        if not att:
            raise HTTPException(status_code=404, detail="Attachment not found")
        headers = {"Content-Disposition": f'inline; filename="{att.filename}"'}
        return Response(content=att.data, media_type=att.content_type or "application/octet-stream", headers=headers)


@app.get("/api/today", response_class=ORJSONResponse)
async def api_today(request: Request, day: str | None = None):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        await ensure_events(session)
        target_day = parse_date_or_none(day) if day else None
        events = await get_today_events(session, profile_id=profile_id, target_date=target_day)
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


@app.post("/api/events/batch/take")
async def api_batch_take(payload: BatchEventPayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        ids = list(dict.fromkeys([int(x) for x in payload.ids if int(x) > 0]))
        if not ids:
            raise HTTPException(status_code=400, detail="ids are required")
        rows = (await session.execute(
            select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(DoseEvent.id.in_(ids), Schedule.profile_id == profile_id)
        )).scalars().all()
        if not rows:
            raise HTTPException(status_code=404, detail="Events not found")
        taken_events = []
        for event in rows:
            if event.status == "pending":
                event = await mark_taken(session, event.id, tg_id, actual_time=payload.actual_time)
                if event:
                    taken_events.append(event)
        await log_action(session, profile_id, tg_id, "group_taken", "dose_event", taken_events[0].id if taken_events else None, f"Групповая отметка: {len(taken_events)} прием(а)", commit=True)
    if taken_events:
        actual = taken_events[0].taken_at.astimezone(TZ).strftime("%H:%M") if taken_events[0].taken_at else ""
        async with SessionLocal() as session:
            recipients = await profile_recipients(session, taken_events[0].schedule.profile_id)
        lines = "\n".join(f"• {event_title(e)}" for e in taken_events)
        for parent_id in recipients:
            if parent_id != tg_id:
                try:
                    await bot.send_message(parent_id, f"✅ В мини-приложении отмечена группа приемов ({len(taken_events)}):\n{lines}\nФактическое время: {actual}")
                except Exception:
                    pass
    return {"ok": True, "count": len(taken_events), "message": f"Отмечено приемов: {len(taken_events)}"}


@app.post("/api/events/batch/skip")
async def api_batch_skip(payload: BatchEventPayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        ids = list(dict.fromkeys([int(x) for x in payload.ids if int(x) > 0]))
        rows = (await session.execute(
            select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(DoseEvent.id.in_(ids), Schedule.profile_id == profile_id)
        )).scalars().all()
        skipped=[]
        for event in rows:
            if event.status == "pending":
                event = await mark_skipped(session, event.id, tg_id)
                if event:
                    skipped.append(event)
        await log_action(session, profile_id, tg_id, "group_skipped", "dose_event", skipped[0].id if skipped else None, f"Групповой пропуск: {len(skipped)} прием(а)", commit=True)
    if skipped:
        async with SessionLocal() as session:
            recipients = await profile_recipients(session, skipped[0].schedule.profile_id)
        text = skipped_notification_text(skipped)
        for parent_id in recipients:
            if parent_id != tg_id:
                try:
                    await bot.send_message(parent_id, text)
                except Exception:
                    pass
    return {"ok": True, "count": len(skipped), "message": f"Пропущено приемов: {len(skipped)}"}


@app.post("/api/events/batch/snooze")
async def api_batch_snooze(payload: BatchEventPayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        ids = list(dict.fromkeys([int(x) for x in payload.ids if int(x) > 0]))
        rows = (await session.execute(
            select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(DoseEvent.id.in_(ids), Schedule.profile_id == profile_id)
        )).scalars().all()
        snoozed=[]
        for event in rows:
            if event.status == "pending":
                event = await snooze_event(session, event.id, tg_id, settings.snooze_minutes)
                if event:
                    snoozed.append(event)
        await log_action(session, profile_id, tg_id, "group_snoozed", "dose_event", snoozed[0].id if snoozed else None, f"Групповое отложение: {len(snoozed)} прием(а)", commit=True)
    return {"ok": True, "count": len(snoozed), "message": f"Отложено приемов: {len(snoozed)}"}


@app.post("/api/events/{event_id}/take")
async def api_take(event_id: int, payload: TakePayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        await assert_event_in_profile(session, event_id, profile_id)
        event = await mark_taken(session, event_id, tg_id, actual_time=payload.actual_time)
        if event:
            await log_action(session, profile_id, tg_id, "event_taken", "dose_event", event.id, event_title(event), commit=True)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    text = thanks_text(event.id)
    actual = event.taken_at.astimezone(TZ).strftime("%H:%M") if event.taken_at else ""
    async with SessionLocal() as session:
        recipients = await profile_recipients(session, event.schedule.profile_id)
    for parent_id in recipients:
        if parent_id != tg_id:
            try:
                await bot.send_message(parent_id, f"✅ В мини-приложении отмечен прием: {event_title(event)}\nФактическое время: {actual}")
            except Exception:
                pass
    return {"ok": True, "message": text}


@app.patch("/api/events/{event_id}/taken-time")
async def api_update_taken_time(event_id: int, payload: TakePayload, request: Request):
    tg_id, role = require_known(request)
    if not payload.actual_time:
        raise HTTPException(status_code=400, detail="actual_time is required")
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        await assert_event_in_profile(session, event_id, profile_id)
        event = await update_taken_time(session, event_id, tg_id, payload.actual_time)
        if event:
            await log_action(session, profile_id, tg_id, "event_time_changed", "dose_event", event.id, f"{event_title(event)} → {payload.actual_time}", commit=True)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    actual = event.taken_at.astimezone(TZ).strftime("%H:%M") if event.taken_at else payload.actual_time
    async with SessionLocal() as session:
        recipients = await profile_recipients(session, event.schedule.profile_id)
    for parent_id in recipients:
        if parent_id != tg_id:
            try:
                await bot.send_message(parent_id, f"✏️ В мини-приложении изменено фактическое время: {event_title(event)}\nНовое время: {actual}")
            except Exception:
                pass
    return {"ok": True, "message": f"Время изменено на {actual}"}




@app.patch("/api/events/{event_id}/status")
async def api_update_event_status(event_id: int, payload: StatusPayload, request: Request):
    tg_id, role = require_known(request)
    status = (payload.status or "").strip().lower()
    if status not in {"pending", "taken", "skipped"}:
        raise HTTPException(status_code=400, detail="status must be pending, taken or skipped")
    if status == "taken" and payload.actual_time and not payload.actual_time.count(":") == 1:
        raise HTTPException(status_code=400, detail="actual_time must be HH:MM")
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        await assert_event_in_profile(session, event_id, profile_id)
        event = await set_event_status(session, event_id, tg_id, status, payload.actual_time)
        if event:
            await log_action(session, profile_id, tg_id, "event_status_changed", "dose_event", event.id, f"{event_title(event)} → {status}", commit=True)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    label = {"pending": "не принято", "taken": "принято", "skipped": "пропущено"}[status]
    actual = event.taken_at.astimezone(TZ).strftime("%H:%M") if event.taken_at else ""
    async with SessionLocal() as session:
        recipients = await profile_recipients(session, event.schedule.profile_id)
    for parent_id in recipients:
        if parent_id != tg_id:
            try:
                extra = f"\nФактическое время: {actual}" if status == "taken" and actual else ""
                await bot.send_message(parent_id, f"✏️ В мини-приложении изменен статус: {event_title(event)}\nНовый статус: {label}{extra}")
            except Exception:
                pass
    return {"ok": True, "message": f"Статус изменен: {label}"}


@app.post("/api/events/{event_id}/skip")
async def api_skip(event_id: int, payload: EventActionPayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        await assert_event_in_profile(session, event_id, profile_id)
        event = await mark_skipped(session, event_id, tg_id, note=payload.note)
        if event:
            await log_action(session, profile_id, tg_id, "event_skipped", "dose_event", event.id, event_title(event), commit=True)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    async with SessionLocal() as session:
        recipients = await profile_recipients(session, event.schedule.profile_id)
    for parent_id in recipients:
        if parent_id != tg_id:
            try:
                await bot.send_message(parent_id, skipped_notification_text([event]))
            except Exception:
                pass
    return {"ok": True, "message": "Пропуск сохранен"}


@app.post("/api/events/{event_id}/snooze")
async def api_snooze(event_id: int, payload: EventActionPayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        await assert_event_in_profile(session, event_id, profile_id)
        event = await snooze_event(session, event_id, tg_id, settings.snooze_minutes)
        if event:
            await log_action(session, profile_id, tg_id, "event_snoozed", "dose_event", event.id, event_title(event), commit=True)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    until = event.postponed_until.astimezone(TZ).strftime("%H:%M") if event.postponed_until else ""
    return {"ok": True, "message": f"Отложено до {until}"}


@app.get("/api/schedules", response_class=ORJSONResponse)
async def api_schedules(request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        rows = (await session.execute(
            select(Schedule).options(selectinload(Schedule.medicine)).where(Schedule.active == True, Schedule.profile_id == profile_id).order_by(Schedule.time_local)  # noqa: E712
        )).scalars().all()
        result = [await serialize_schedule_row(session, r, include_need=False) for r in rows]
        result.sort(key=lambda x: (x["display_time"], x["name"]))
        return result



async def ensure_medicine_course_for_payload(session, *, profile_id: int, med: Medicine, payload: AddSchedulePayload, start_date: date | None, end_date: date | None, active: bool) -> MedicineCourse | None:
    """Create/update course-of-medicine row inside assignment.

    Schedules are only intake rules (times/meal slots). This row is the medical course.
    For legacy/manual schedules without assignment we still create a technical row so
    dose_events stay attached to schedules while UI can group by medicine_course_id.
    """
    recurrence_type, recurrence_interval_days = normalize_recurrence(payload.recurrence_type, payload.recurrence_interval_days)
    # User accepted: one medicine occurs only once within one assignment.
    q = select(MedicineCourse).where(
        MedicineCourse.profile_id == profile_id,
        MedicineCourse.assignment_id == payload.course_id,
        MedicineCourse.medicine_id == med.id,
    )
    if not payload.course_id:
        q = q.where(
            MedicineCourse.dose == payload.dose,
            MedicineCourse.start_date == start_date,
            MedicineCourse.end_date == end_date,
            MedicineCourse.recurrence_type == recurrence_type,
            MedicineCourse.recurrence_interval_days == recurrence_interval_days,
        )
    mc = (await session.execute(q.order_by(MedicineCourse.id.desc()))).scalars().first()
    if not mc:
        mc = MedicineCourse(profile_id=profile_id, assignment_id=payload.course_id, medicine_id=med.id)
        session.add(mc)
        await session.flush()
    mc.name = med.name
    mc.dose = payload.dose or ""
    mc.dosage_form = payload.dosage_form or ""
    mc.administration_route = payload.administration_route or ""
    mc.analogs = payload.analogs or ""
    mc.start_date = start_date
    mc.duration_value = payload.duration_value
    mc.duration_unit = payload.duration_unit or ""
    if start_date and payload.duration_value:
        mc.end_date = calc_end_date_from_duration(start_date, payload.duration_value, payload.duration_unit) or end_date
    else:
        mc.end_date = end_date
    mc.recurrence_type = recurrence_type
    mc.recurrence_interval_days = recurrence_interval_days
    mc.weekdays = payload.weekdays or ""
    mc.specific_dates = payload.specific_dates or ""
    # The timing template is course-level summary; individual schedule rows keep exact meal slot/time.
    first_entry = (payload.entries or [None])[0]
    mc.timing_template = (first_entry.timing_template if first_entry else "fixed") or "fixed"
    mc.active = bool(active)
    mc.status = "active" if active else ("completed" if mc.end_date and mc.end_date < datetime.now(TZ).date() else "draft")
    return mc

def schedule_should_be_active(course_id: int | None, start_date: date | None, end_date: date | None = None) -> bool:
    if not course_id:
        return True
    today = datetime.now(TZ).date()
    if start_date is None:
        return False
    if end_date and end_date < today:
        return False
    return start_date <= today

@app.post("/api/schedules")
async def api_add_schedule(payload: AddSchedulePayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
    start_date = parse_date_or_none(payload.start_date)
    end_date = parse_date_or_none(payload.end_date)
    if start_date and payload.duration_value:
        end_date = calc_end_date_from_duration(start_date, payload.duration_value, payload.duration_unit) or end_date
    recurrence_type, recurrence_interval_days = normalize_recurrence(payload.recurrence_type, payload.recurrence_interval_days)
    if recurrence_type not in {"daily", "weekly", "monthly", "specific_dates"}:
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
        # v29: inventory is not selected on a medicine row. It is matched automatically
        # by medicine_id/name from the "Аптечка" tab. Dose is the only source for
        # расход/потребность calculations.
        inv_id = None
        if payload.course_id:
            duplicate_mc = (await session.execute(select(MedicineCourse).where(
                MedicineCourse.profile_id == profile_id,
                MedicineCourse.assignment_id == payload.course_id,
                MedicineCourse.medicine_id == med.id,
                MedicineCourse.status != "completed",
            ))).scalar_one_or_none()
            if duplicate_mc:
                raise HTTPException(status_code=409, detail=f"В этом назначении уже есть {med.name}. Откройте существующий курс и измените его.")

        default_amount, default_unit = parse_dose_amount(payload.dose)
        consume_amount = float(default_amount or 1)
        consume_unit = default_unit or "шт"
        course_active = schedule_should_be_active(payload.course_id, start_date, end_date)
        medicine_course = await ensure_medicine_course_for_payload(session, profile_id=profile_id, med=med, payload=payload, start_date=start_date, end_date=end_date, active=course_active)
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
                    Schedule.profile_id == profile_id,
                    Schedule.medicine_id == med.id,
                    Schedule.dose == payload.dose,
                    Schedule.time_local == hhmm,
                    Schedule.label == label,
                    Schedule.start_date == start_date,
                    Schedule.end_date == end_date,
                    Schedule.recurrence_type == recurrence_type,
                    Schedule.recurrence_interval_days == recurrence_interval_days,
                    Schedule.course_id == payload.course_id,
                    Schedule.medicine_course_id == (medicine_course.id if medicine_course else None),
                    Schedule.inventory_item_id == inv_id,
                    Schedule.consume_units_per_dose == consume_amount,
                    Schedule.weekdays == (payload.weekdays or ""),
                    Schedule.specific_dates == (payload.specific_dates or ""),
                    Schedule.timing_template == ((entry.timing_template or "fixed")),
                    Schedule.meal_name == (entry.meal_name or ""),
                    Schedule.meal_offset_minutes == (entry.meal_offset_minutes or 0),
                    Schedule.dosage_form == (payload.dosage_form or ""),
                    Schedule.administration_route == (payload.administration_route or ""),
                    Schedule.analogs == (payload.analogs or ""),
                )
            )).scalar_one_or_none()
            if existing:
                created_ids.append(existing)
                continue

            sched = Schedule(
                profile_id=profile_id,
                medicine_id=med.id,
                dose=payload.dose,
                time_local=hhmm,
                label=label,
                start_date=start_date,
                end_date=end_date,
                recurrence_type=recurrence_type,
                recurrence_interval_days=recurrence_interval_days,
                course_id=payload.course_id,
                medicine_course_id=medicine_course.id if medicine_course else None,
                weekdays=payload.weekdays or "",
                specific_dates=payload.specific_dates or "",
                timing_template=entry.timing_template or "fixed",
                meal_name=entry.meal_name or "",
                meal_offset_minutes=entry.meal_offset_minutes or 0,
                dosage_form=payload.dosage_form or "",
                administration_route=payload.administration_route or "",
                analogs=payload.analogs or "",
                duration_value=payload.duration_value,
                duration_unit=payload.duration_unit or "",
                inventory_item_id=inv_id,
                active=course_active,
                consume_units_per_dose=consume_amount,
                consume_unit_name=consume_unit,
            )
            refresh_schedule_need_fields(sched)
            session.add(sched)
            await session.flush()
            created_ids.append(sched.id)
        if not created_ids:
            raise HTTPException(status_code=400, detail="No valid time entries")
        await log_action(session, profile_id, tg_id, "schedule_created", "schedule", created_ids[0], f"{payload.name} — {payload.dose}; приемов: {len(created_ids)}")
        await session.commit()
        await ensure_events(session)
        return {"ok": True, "ids": created_ids, "count": len(created_ids)}


@app.put("/api/schedules/{schedule_id}")
async def api_update_schedule(schedule_id: int, payload: AddSchedulePayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
    start_date = parse_date_or_none(payload.start_date)
    end_date = parse_date_or_none(payload.end_date)
    if start_date and payload.duration_value:
        end_date = calc_end_date_from_duration(start_date, payload.duration_value, payload.duration_unit) or end_date
    async with SessionLocal() as session:
        existing = (await session.execute(select(Schedule).where(Schedule.id == schedule_id, Schedule.profile_id == profile_id))).scalar_one_or_none()
        if not existing:
            raise HTTPException(status_code=404, detail="Schedule not found")
        if payload.course_id and payload.name:
            med_for_check = (await session.execute(select(Medicine).where(Medicine.name == payload.name))).scalar_one_or_none()
            if med_for_check:
                duplicate_mc = (await session.execute(select(MedicineCourse).where(
                    MedicineCourse.profile_id == profile_id,
                    MedicineCourse.assignment_id == payload.course_id,
                    MedicineCourse.medicine_id == med_for_check.id,
                    MedicineCourse.id != (existing.medicine_course_id or 0),
                    MedicineCourse.status != "completed",
                ))).scalar_one_or_none()
                if duplicate_mc:
                    raise HTTPException(status_code=409, detail=f"В этом назначении уже есть {med_for_check.name}. Измените существующий курс.")
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
            course_id=payload.course_id,
            inventory_item_id=payload.inventory_item_id,
            consume_units_per_dose=payload.consume_units_per_dose,
            consume_unit_name=payload.consume_unit_name,
            weekdays=payload.weekdays,
            specific_dates=payload.specific_dates,
            timing_template=(payload.entries[0].timing_template if payload.entries else "fixed"),
            meal_name=(payload.entries[0].meal_name if payload.entries else ""),
            meal_offset_minutes=(payload.entries[0].meal_offset_minutes if payload.entries else 0),
            dosage_form=payload.dosage_form,
            administration_route=payload.administration_route,
            analogs=payload.analogs,
            duration_value=payload.duration_value,
            duration_unit=payload.duration_unit,
        )
        if not sched:
            raise HTTPException(status_code=404, detail="Schedule not found")
        await log_action(session, profile_id, tg_id, "schedule_updated", "schedule", schedule_id, f"{payload.name} — {payload.dose}; {payload.time_local}", commit=True)
        return {"ok": True}



@app.post("/api/schedules/{schedule_id}/start")
async def api_start_schedule(schedule_id: int, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        sched = (await session.execute(select(Schedule).where(Schedule.id == schedule_id, Schedule.profile_id == profile_id))).scalar_one_or_none()
        if not sched:
            raise HTTPException(status_code=404, detail="Schedule not found")
        sched.active = True
        if not sched.start_date:
            sched.start_date = datetime.now(TZ).date()
        computed_end = calc_end_date_from_duration(sched.start_date, getattr(sched, "duration_value", None), getattr(sched, "duration_unit", ""))
        if computed_end:
            sched.end_date = computed_end
        refresh_schedule_need_fields(sched)
        if getattr(sched, "medicine_course_id", None):
            mc = (await session.execute(select(MedicineCourse).where(MedicineCourse.id == sched.medicine_course_id))).scalar_one_or_none()
            if mc:
                mc.active = True
                mc.status = "active"
                mc.start_date = sched.start_date
                mc.end_date = sched.end_date
        await log_action(session, profile_id, tg_id, "schedule_started", "schedule", sched.id, "Курс лекарства начат", commit=False)
        await session.commit()
        await ensure_events(session)
        return {"ok": True}

@app.delete("/api/schedules/{schedule_id}")
async def api_delete_schedule(schedule_id: int, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        sched = (await session.execute(select(Schedule).where(Schedule.id == schedule_id, Schedule.profile_id == profile_id))).scalar_one_or_none()
        if not sched:
            raise HTTPException(status_code=404, detail="Schedule not found")
        sched.active = False
        # Preserve already completed intake history; remove only future pending events.
        from .service import rebuild_future_events_for_schedule
        await rebuild_future_events_for_schedule(session, sched.id)
        if getattr(sched, "medicine_course_id", None):
            still_active = (await session.execute(select(Schedule.id).where(
                Schedule.medicine_course_id == sched.medicine_course_id,
                Schedule.id != sched.id,
                Schedule.active == True,
            ))).scalar_one_or_none()
            if not still_active:
                mc = (await session.execute(select(MedicineCourse).where(MedicineCourse.id == sched.medicine_course_id))).scalar_one_or_none()
                if mc:
                    mc.active = False
                    mc.status = "cancelled"
        await log_action(session, profile_id, tg_id, "schedule_deleted", "schedule", schedule_id, f"Удален будущий прием из расписания", commit=False)
        await session.commit()
        return {"ok": True}


@app.get("/api/inventory-options", response_class=ORJSONResponse)
async def api_inventory_options(request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        rows = (await session.execute(select(InventoryItem).where(InventoryItem.profile_id == profile_id, InventoryItem.active == True).order_by(InventoryItem.name))).scalars().all()  # noqa: E712
        return [{"id": r.id, "name": r.name, "quantity": r.quantity, "unit_name": r.unit_name} for r in rows]


@app.get("/api/inventory", response_class=ORJSONResponse)
async def api_inventory(request: Request, search: str = ""):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        search_text = search.strip()
        rows = await get_inventory(session, profile_id, search=search_text)

        # Backward compatibility: older versions could create stock records before
        # profile binding became strict. Show such legacy records in the selected
        # profile instead of making the user think the аптечка is empty.
        if not rows:
            legacy_q = select(InventoryItem).where(InventoryItem.active == True, InventoryItem.profile_id.is_(None))  # noqa: E712
            if search_text:
                legacy_q = legacy_q.where(InventoryItem.name.ilike(f"%{search_text}%"))
            legacy = list((await session.execute(legacy_q.order_by(InventoryItem.name))).scalars().all())
            for item in legacy:
                item.profile_id = profile_id
            if legacy:
                await session.commit()
                rows = legacy

        return [{
            "id": r.id,
            "name": r.name,
            "quantity": r.quantity,
            "unit_name": r.unit_name,
            "low_threshold": r.low_threshold,
            "has_photo": bool(r.photo_data),
            "photo_url": f"/api/inventory/{r.id}/photo" if r.photo_data else "",
        } for r in rows]


@app.post("/api/inventory")
async def api_inventory_add(payload: InventoryPayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        med = (await session.execute(select(Medicine).where(Medicine.name == name))).scalar_one_or_none()
        item = InventoryItem(profile_id=profile_id, medicine_id=med.id if med else None, name=name, quantity=max(0, payload.quantity), unit_name=payload.unit_name or "шт", low_threshold=max(0, payload.low_threshold), active=True)
        session.add(item)
        await session.flush()
        await log_action(session, profile_id, tg_id, "inventory_created", "inventory", item.id, f"Добавлено в аптечку: {item.name}", commit=True)
        return {"ok": True, "id": item.id}


@app.put("/api/inventory/{item_id}")
async def api_inventory_update(item_id: int, payload: InventoryPayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        item = (await session.execute(select(InventoryItem).where(InventoryItem.id == item_id, InventoryItem.profile_id == profile_id, InventoryItem.active == True))).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        med = (await session.execute(select(Medicine).where(Medicine.name == payload.name.strip()))).scalar_one_or_none() if payload.name.strip() else None
        item.name = payload.name.strip() or item.name
        item.medicine_id = med.id if med else item.medicine_id
        item.quantity = max(0, payload.quantity)
        item.unit_name = payload.unit_name or "шт"
        item.low_threshold = max(0, payload.low_threshold)
        # если пополнили выше порога, разрешаем новое напоминание в будущем
        if item.quantity > item.low_threshold:
            item.purchase_alert_sent_at = None
        await log_action(session, profile_id, tg_id, "inventory_updated", "inventory", item.id, f"Обновлена аптечка: {item.name}", commit=True)
        return {"ok": True}


@app.delete("/api/inventory/{item_id}")
async def api_inventory_delete(item_id: int, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        item = (await session.execute(select(InventoryItem).where(InventoryItem.id == item_id, InventoryItem.profile_id == profile_id, InventoryItem.active == True))).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        item.active = False
        await log_action(session, profile_id, tg_id, "inventory_deleted", "inventory", item.id, f"Удалено из аптечки: {item.name}", commit=True)
        return {"ok": True}


@app.post("/api/inventory/{item_id}/photo")
async def api_inventory_photo_upload(item_id: int, request: Request, file: UploadFile = File(...)):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        item = (await session.execute(select(InventoryItem).where(InventoryItem.id == item_id, InventoryItem.profile_id == profile_id, InventoryItem.active == True))).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        data = await file.read()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File is too large; max 8 MB")
        item.photo_filename = file.filename or "medicine-photo"
        item.photo_content_type = file.content_type or "application/octet-stream"
        item.photo_data = data
        await log_action(session, profile_id, tg_id, "inventory_photo_added", "inventory", item.id, f"Добавлено фото лекарства: {item.name}", commit=True)
        return {"ok": True}


@app.get("/api/inventory/{item_id}/photo")
async def api_inventory_photo(item_id: int):
    # Фото используется внутри <img>, где нельзя передать Telegram initData header.
    # Поэтому endpoint отдает только активное фото по id без списка/метаданных.
    async with SessionLocal() as session:
        item = (await session.execute(select(InventoryItem).where(InventoryItem.id == item_id, InventoryItem.active == True))).scalar_one_or_none()
        if not item or not item.photo_data:
            raise HTTPException(status_code=404, detail="Photo not found")
        return Response(content=item.photo_data, media_type=item.photo_content_type or "application/octet-stream")


async def _report_rows(session, profile_id: int, days: int = 30):
    profile = (await session.execute(select(Profile).where(Profile.id == profile_id))).scalar_one_or_none()
    stats = await get_stats(session, days=days, profile_id=profile_id)
    start = datetime.now(TZ) - timedelta(days=days)
    events = list((await session.execute(
        select(DoseEvent).options(selectinload(DoseEvent.schedule).selectinload(Schedule.medicine)).join(Schedule).where(
            Schedule.profile_id == profile_id,
            DoseEvent.due_at >= start,
            DoseEvent.due_at <= datetime.now(TZ),
        ).order_by(DoseEvent.due_at.desc())
    )).scalars().all())
    return profile, stats, events


@app.get("/api/reports/doctor.xlsx")
async def api_report_xlsx(request: Request, days: int = 30):
    from openpyxl import Workbook
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        profile, stats, events = await _report_rows(session, profile_id, max(1, min(days, 365)))
    wb = Workbook()
    ws = wb.active
    ws.title = "Статистика"
    ws.append(["Профиль", profile.name if profile else ""])
    ws.append(["Период, дней", days])
    ws.append([])
    ws.append(["Препарат", "Всего", "Принято", "Пропущено", "Не отмечено", "% принято"])
    for r in stats:
        ws.append([r["medicine"], r["total"], r["taken"], r["skipped"], r["pending"], r["taken_percent"]])
    ws2 = wb.create_sheet("История")
    ws2.append(["Дата", "План", "Препарат", "Доза", "Статус", "Факт"])
    for e in events:
        ws2.append([e.due_at.astimezone(TZ).strftime("%d.%m.%Y"), e.due_at.astimezone(TZ).strftime("%H:%M"), e.schedule.medicine.name, e.schedule.dose, e.status, e.taken_at.astimezone(TZ).strftime("%H:%M") if e.taken_at else ""])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=doctor_report.xlsx"})


@app.get("/api/reports/doctor.pdf")
async def api_report_pdf(request: Request, days: int = 30):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        profile, stats, events = await _report_rows(session, profile_id, max(1, min(days, 365)))
    bio = BytesIO()
    c = canvas.Canvas(bio, pagesize=A4)
    font = "Helvetica"
    for fp in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/local/share/fonts/DejaVuSans.ttf"]:
        if os.path.exists(fp):
            pdfmetrics.registerFont(TTFont("DejaVuSans", fp)); font = "DejaVuSans"; break
    width, height = A4
    y = height - 42
    def line(txt, size=10, dy=16):
        nonlocal y
        if y < 60:
            c.showPage(); y = height - 42; c.setFont(font, size)
        c.setFont(font, size); c.drawString(36, y, str(txt)[:110]); y -= dy
    line("Отчет для врача", 16, 24)
    line(f"Профиль: {profile.name if profile else ''}")
    line(f"Период: {days} дней")
    y -= 8
    line("Статистика", 13, 20)
    for r in stats:
        line(f"{r['medicine']}: принято {r['taken']}/{r['total']} ({r['taken_percent']}%), пропущено {r['skipped']}, не отмечено {r['pending']}")
    y -= 8
    line("Последние события", 13, 20)
    for e in events[:80]:
        fact = f", факт {e.taken_at.astimezone(TZ).strftime('%H:%M')}" if e.taken_at else ""
        line(f"{e.due_at.astimezone(TZ).strftime('%d.%m %H:%M')} — {e.schedule.medicine.name} {e.schedule.dose} — {e.status}{fact}", 9, 14)
    c.save()
    bio.seek(0)
    return StreamingResponse(bio, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=doctor_report.pdf"})


@app.get("/api/stats", response_class=ORJSONResponse)
async def api_stats(request: Request, medicine_id: int | None = None, days: int = 30, course_id: int | None = None):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        return await get_stats(session, medicine_id=medicine_id, days=days, profile_id=profile_id, course_id=course_id)




@app.delete("/api/stats/medicine/{medicine_id}")
async def api_clear_medicine_stats(medicine_id: int, request: Request):
    profile_id = await _profile_id(request)
    tg_id = request.state.tg_id
    async with SessionLocal() as session:
        if not await is_profile_manager(session, profile_id, tg_id):
            raise HTTPException(403, "Нет доступа")
        schedule_ids = [r[0] for r in (await session.execute(
            select(Schedule.id).where(Schedule.profile_id == profile_id, Schedule.medicine_id == medicine_id)
        )).all()]
        if schedule_ids:
            await session.execute(delete(DoseEvent).where(DoseEvent.schedule_id.in_(schedule_ids)))
            await log_action(session, profile_id, tg_id, "stats_cleared", "medicine", medicine_id, "Очищены факты приемов из статистики")
        await session.commit()
        return {"ok": True, "deleted_for_schedules": len(schedule_ids)}

@app.get("/api/medicines", response_class=ORJSONResponse)
async def api_medicines(request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        rows = (await session.execute(select(Medicine).join(Schedule, Schedule.medicine_id == Medicine.id).where(Medicine.active == True, Schedule.active == True, Schedule.profile_id == profile_id).order_by(Medicine.name))).scalars().unique().all()  # noqa: E712
        return [{"id": m.id, "name": m.name} for m in rows]


@app.get("/api/medicine-options", response_class=ORJSONResponse)
async def api_medicine_options(request: Request):
    """Medicine names for dropdowns.

    The Аптечка form must not become unusable if the selected profile has no
    schedule yet or if older stock records were created without profile binding.
    Therefore the list is intentionally built from several safe sources:
    selected profile schedule, already-created stock records for accessible
    profiles, legacy profile-less stock records, and the global medicine
    dictionary.
    """
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        profiles = await profiles_for_user(session, tg_id, role)
        accessible_ids = {p.id for p in profiles}
        accessible_ids.add(profile_id)
        names: set[str] = set()

        # 1) Active schedule of the selected profile.
        sched_rows = (await session.execute(
            select(Medicine.name)
            .join(Schedule, Schedule.medicine_id == Medicine.id)
            .where(Medicine.active == True, Schedule.profile_id == profile_id)  # noqa: E712
        )).scalars().all()
        names.update([n for n in sched_rows if n])

        # 2) Stock items for all profiles accessible to the current user.
        if accessible_ids:
            inv_rows = (await session.execute(
                select(InventoryItem.name).where(
                    InventoryItem.active == True,  # noqa: E712
                    InventoryItem.profile_id.in_(accessible_ids),
                )
            )).scalars().all()
            names.update([n for n in inv_rows if n])

        # 3) Legacy stock items without profile_id. Bind them to the selected
        # profile immediately so they are displayed by /api/inventory as well.
        legacy_items = (await session.execute(
            select(InventoryItem).where(InventoryItem.active == True, InventoryItem.profile_id.is_(None))  # noqa: E712
        )).scalars().all()
        changed = False
        for item in legacy_items:
            if item.name:
                names.add(item.name)
            item.profile_id = profile_id
            changed = True
        if changed:
            await session.commit()

        # 4) Global dictionary of medicines. This keeps the dropdown populated
        # even when a medicine exists in the system but is not yet scheduled in
        # the selected profile.
        all_meds = (await session.execute(
            select(Medicine.name).where(Medicine.active == True).order_by(Medicine.name)  # noqa: E712
        )).scalars().all()
        names.update([n for n in all_meds if n])

        return [{"name": n} for n in sorted(names, key=lambda x: x.lower())]


@app.get("/api/medicines/{medicine_id}/history", response_class=ORJSONResponse)
async def api_medicine_history(medicine_id: int, request: Request, days: int = 30):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        events = await get_history_for_medicine(session, medicine_id, days=days, profile_id=profile_id)
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
