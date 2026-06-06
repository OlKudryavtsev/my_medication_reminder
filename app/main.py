from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, ORJSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .bot import bot, dp, take_keyboard, group_take_keyboard
from .config import get_settings
from .db import init_db, SessionLocal, DoseEvent, Schedule, Medicine, User, Profile, TreatmentCourse, TreatmentAttachment
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
    weekdays: str = ""
    specific_dates: str = ""
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


@app.on_event("startup")
async def startup() -> None:
    global polling_task
    await init_db()
    async with SessionLocal() as session:
        await ensure_profiles(session)
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


@app.get("/api/courses", response_class=ORJSONResponse)
async def api_courses(request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        rows = await get_courses(session, profile_id)
        result = []
        for c in rows:
            attachments = (await session.execute(select(TreatmentAttachment).where(TreatmentAttachment.course_id == c.id).order_by(TreatmentAttachment.id.desc()))).scalars().all()
            result.append({
                "id": c.id,
                "name": c.name,
                "assignment_date": c.assignment_date.isoformat() if c.assignment_date else "",
                "doctor": c.doctor or "",
                "comment": c.comment or "",
                "attachments": [{"id": a.id, "filename": a.filename, "content_type": a.content_type} for a in attachments],
            })
        return result


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
async def api_today(request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        await ensure_events(session)
        events = await get_today_events(session, profile_id=profile_id)
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
                await bot.send_message(parent_id, f"⏭️ В мини-приложении отмечен пропуск: {event_title(event)}")
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
        today = datetime.now(TZ).date()
        result = []
        for r in rows:
            display_time = await schedule_due_hhmm(session, r, today)
            result.append({
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
                "weekdays": r.weekdays or "",
                "specific_dates": r.specific_dates or "",
                "timing_template": r.timing_template or "fixed",
                "meal_name": r.meal_name or "",
                "meal_offset_minutes": r.meal_offset_minutes or 0,
                "active": r.active,
            })
        result.sort(key=lambda x: (x["display_time"], x["name"]))
        return result


@app.post("/api/schedules")
async def api_add_schedule(payload: AddSchedulePayload, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
    start_date = parse_date_or_none(payload.start_date)
    end_date = parse_date_or_none(payload.end_date)
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
                    Schedule.weekdays == (payload.weekdays or ""),
                    Schedule.specific_dates == (payload.specific_dates or ""),
                    Schedule.timing_template == ((entry.timing_template or "fixed")),
                    Schedule.meal_name == (entry.meal_name or ""),
                    Schedule.meal_offset_minutes == (entry.meal_offset_minutes or 0),
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
                weekdays=payload.weekdays or "",
                specific_dates=payload.specific_dates or "",
                timing_template=entry.timing_template or "fixed",
                meal_name=entry.meal_name or "",
                meal_offset_minutes=entry.meal_offset_minutes or 0,
            )
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
    async with SessionLocal() as session:
        existing = (await session.execute(select(Schedule).where(Schedule.id == schedule_id, Schedule.profile_id == profile_id))).scalar_one_or_none()
        if not existing:
            raise HTTPException(status_code=404, detail="Schedule not found")
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
            weekdays=payload.weekdays,
            specific_dates=payload.specific_dates,
            timing_template=(payload.entries[0].timing_template if payload.entries else "fixed"),
            meal_name=(payload.entries[0].meal_name if payload.entries else ""),
            meal_offset_minutes=(payload.entries[0].meal_offset_minutes if payload.entries else 0),
        )
        if not sched:
            raise HTTPException(status_code=404, detail="Schedule not found")
        await log_action(session, profile_id, tg_id, "schedule_updated", "schedule", schedule_id, f"{payload.name} — {payload.dose}; {payload.time_local}", commit=True)
        return {"ok": True}


@app.delete("/api/schedules/{schedule_id}")
async def api_delete_schedule(schedule_id: int, request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile_manager(request, session)
        sched = (await session.execute(select(Schedule).where(Schedule.id == schedule_id, Schedule.profile_id == profile_id))).scalar_one_or_none()
        if not sched:
            raise HTTPException(status_code=404, detail="Schedule not found")
        sched.active = False
        await log_action(session, profile_id, tg_id, "schedule_deleted", "schedule", schedule_id, f"Удален прием из расписания", commit=False)
        await session.commit()
        return {"ok": True}


@app.get("/api/stats", response_class=ORJSONResponse)
async def api_stats(request: Request, medicine_id: int | None = None, days: int = 30):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        return await get_stats(session, medicine_id=medicine_id, days=days, profile_id=profile_id)


@app.get("/api/medicines", response_class=ORJSONResponse)
async def api_medicines(request: Request):
    async with SessionLocal() as session:
        tg_id, role, profile_id = await require_profile(request, session)
        rows = (await session.execute(select(Medicine).join(Schedule, Schedule.medicine_id == Medicine.id).where(Medicine.active == True, Schedule.active == True, Schedule.profile_id == profile_id).order_by(Medicine.name))).scalars().unique().all()  # noqa: E712
        return [{"id": m.id, "name": m.name} for m in rows]


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
