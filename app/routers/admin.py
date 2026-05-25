"""Admin panel — CRUD for events, trials, members, entries, PDFs, call numbers."""
import os
import secrets
import shutil
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, require_admin
from app.models.event import EVENT_TYPES, Event
from app.models.event_pdf import DOCUMENT_TYPES, EventPdf
from app.models.member import Member
from app.models.registration import Registration
from app.models.trial import Trial, TrialEntry, TrialEntrySelection, TrialEvent, TrialEventClass
from app.models.user import User
from app.utils.countries import get_country_choices
from app.utils.flash import flash
from app.utils.registration_windows import compute_ahba_close, compute_akc_close, get_trial_status
from app.routers.trials import _parse_selection_rows, _compute_fee_from_rows

router = APIRouter(tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

_PDF_DIR = os.path.join("app", "static", "pdfs")


def _csrf(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        request.session["csrf_token"] = token
    return token


def _validate_csrf(submitted: str, request: Request) -> None:
    session_token = request.session.get("csrf_token", "")
    if not session_token or submitted != session_token:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _admin_ctx(request: Request, admin_user: User) -> dict:
    from app.utils.flash import get_flash_messages
    return {
        "request": request,
        "current_user": admin_user,
        "flash_messages": get_flash_messages(request),
        "csrf_token": _csrf(request),
    }


# ============================================================
# Dashboard
# ============================================================

@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    today = date.today()
    upcoming_events = (
        db.query(Event)
        .filter(Event.is_deleted.is_(False), Event.end_date >= today)
        .order_by(Event.start_date)
        .limit(10)
        .all()
    )
    recent_entries = (
        db.query(TrialEntry)
        .order_by(TrialEntry.id.desc())
        .limit(10)
        .all()
    )
    recent_regs = (
        db.query(Registration)
        .order_by(Registration.id.desc())
        .limit(10)
        .all()
    )
    ctx = _admin_ctx(request, admin)
    ctx.update({
        "upcoming_events": upcoming_events,
        "recent_entries": recent_entries,
        "recent_regs": recent_regs,
        "total_entries": db.query(TrialEntry).count(),
        "total_regs": db.query(Registration).count(),
        "total_members": db.query(Member).count(),
    })
    return templates.TemplateResponse(request, "admin/dashboard.html", ctx)


# ============================================================
# Events
# ============================================================

@router.get("/events", response_class=HTMLResponse)
def event_list(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    events = db.query(Event).filter(Event.is_deleted.is_(False)).order_by(Event.start_date.desc()).all()
    ctx = _admin_ctx(request, admin)
    ctx["events"] = events
    return templates.TemplateResponse(request, "admin/event_list.html", ctx)


@router.get("/events/new", response_class=HTMLResponse)
def event_new_get(
    request: Request,
    admin: User = Depends(require_admin),
):
    ctx = _admin_ctx(request, admin)
    ctx.update({"event": None, "form_data": {}, "event_types": EVENT_TYPES})
    return templates.TemplateResponse(request, "admin/event_form.html", ctx)


@router.post("/events/new", response_class=HTMLResponse)
async def event_new_post(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    event = Event()
    _apply_event_form(event, form)
    db.add(event)
    db.commit()
    flash(request, f"Event '{event.title}' created.", "success")
    return RedirectResponse(f"/admin/events/{event.id}/edit", status_code=303)


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
def event_edit_get(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    event = _get_event(event_id, db)
    trials = db.query(Trial).filter_by(event_id=event_id).order_by(Trial.governing_body).all()
    pdfs = db.query(EventPdf).filter_by(event_id=event_id).all()
    trial_secretaries = db.query(User).filter_by(is_trial_secretary=True, is_active=True).all()

    ctx = _admin_ctx(request, admin)
    ctx.update({
        "event": event,
        "form_data": _event_to_form(event),
        "event_types": EVENT_TYPES,
        "trials": trials,
        "trial_statuses": {t.id: get_trial_status(t.reg_close_dt) for t in trials},
        "pdfs": pdfs,
        "document_types": DOCUMENT_TYPES,
        "trial_secretaries": trial_secretaries,
    })
    return templates.TemplateResponse(request, "admin/event_form.html", ctx)


@router.post("/events/{event_id}/edit", response_class=HTMLResponse)
async def event_edit_post(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    event = _get_event(event_id, db)
    _apply_event_form(event, form)
    db.commit()
    flash(request, "Event updated.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/edit", status_code=303)


@router.post("/events/{event_id}/delete")
async def event_delete(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    event = _get_event(event_id, db)
    event.is_deleted = True
    db.commit()
    flash(request, f"Event '{event.title}' deleted.", "success")
    return RedirectResponse("/admin/events", status_code=303)


@router.post("/events/{event_id}/publish")
async def event_toggle_publish(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    event = _get_event(event_id, db)
    event.is_published = not event.is_published
    db.commit()
    status = "published" if event.is_published else "unpublished"
    flash(request, f"Event {status}.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/edit", status_code=303)


# ============================================================
# Trials
# ============================================================

@router.get("/events/{event_id}/trials/new", response_class=HTMLResponse)
def trial_new_get(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    event = _get_event(event_id, db)
    ctx = _admin_ctx(request, admin)
    ctx.update({"event": event, "trial": None, "form_data": {}})
    return templates.TemplateResponse(request, "admin/trial_form.html", ctx)


@router.post("/events/{event_id}/trials/new", response_class=HTMLResponse)
async def trial_new_post(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    event = _get_event(event_id, db)
    trial = Trial(event_id=event_id)
    _apply_trial_form(trial, form, event)
    db.add(trial)
    db.flush()
    _apply_trial_events_form(trial.id, form, db)
    db.commit()
    flash(request, f"{trial.governing_body} trial created.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/edit", status_code=303)


@router.get("/trials/{trial_id}/edit", response_class=HTMLResponse)
def trial_edit_get(
    trial_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    trial = _get_trial(trial_id, db)
    event = _get_event(trial.event_id, db)
    trial_events = db.query(TrialEvent).filter_by(trial_id=trial_id).order_by(TrialEvent.sort_order).all()
    te_classes_map = {
        te.id: db.query(TrialEventClass).filter_by(trial_event_id=te.id).order_by(TrialEventClass.sort_order).all()
        for te in trial_events
    }
    ctx = _admin_ctx(request, admin)
    ctx.update({
        "event": event,
        "trial": trial,
        "form_data": _trial_to_form(trial),
        "trial_events": trial_events,
        "te_classes_map": te_classes_map,
    })
    return templates.TemplateResponse(request, "admin/trial_form.html", ctx)


@router.post("/trials/{trial_id}/edit", response_class=HTMLResponse)
async def trial_edit_post(
    trial_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    trial = _get_trial(trial_id, db)
    event = _get_event(trial.event_id, db)
    _apply_trial_form(trial, form, event)
    db.commit()
    flash(request, "Trial updated.", "success")
    return RedirectResponse(f"/admin/trials/{trial_id}/edit", status_code=303)


@router.post("/trials/{trial_id}/copy")
async def trial_copy(
    trial_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    src = _get_trial(trial_id, db)
    new_trial = Trial(
        event_id=src.event_id,
        governing_body=src.governing_body,
    )
    db.add(new_trial)
    db.flush()
    # Copy TrialEvent + TrialEventClass rows
    for te in db.query(TrialEvent).filter_by(trial_id=trial_id).all():
        new_te = TrialEvent(
            trial_id=new_trial.id, name=te.name, sort_order=te.sort_order,
            fee_cents=te.fee_cents, available_days=te.available_days, is_test_class=te.is_test_class,
        )
        db.add(new_te)
        db.flush()
        for cls in db.query(TrialEventClass).filter_by(trial_event_id=te.id).all():
            db.add(TrialEventClass(trial_event_id=new_te.id, name=cls.name, sort_order=cls.sort_order))
    db.commit()
    flash(request, "Trial copied — adjust date and event number before publishing.", "success")
    return RedirectResponse(f"/admin/trials/{new_trial.id}/edit", status_code=303)


@router.post("/trials/{trial_id}/delete")
async def trial_delete(
    trial_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    trial = _get_trial(trial_id, db)
    event_id = trial.event_id
    # Only allow delete if no entries exist
    entry_count = db.query(TrialEntry).filter_by(event_id=event_id).count()
    if entry_count > 0:
        flash(request, "Cannot delete a trial that has entries.", "error")
        return RedirectResponse(f"/admin/events/{event_id}/edit", status_code=303)
    db.delete(trial)
    db.commit()
    flash(request, "Trial deleted.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/edit", status_code=303)


# ============================================================
# Trial Entries
# ============================================================

@router.get("/events/{event_id}/trial-entries", response_class=HTMLResponse)
def trial_entries_list(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    event = _get_event(event_id, db)
    entries = (
        db.query(TrialEntry)
        .filter_by(event_id=event_id)
        .order_by(TrialEntry.id)
        .all()
    )
    # Load selections per entry for summary
    entry_selections: dict[int, list] = {}
    for entry in entries:
        sels = db.query(TrialEntrySelection).filter_by(trial_entry_id=entry.id).all()
        details = []
        for sel in sels:
            te = db.get(TrialEvent, sel.trial_event_id)
            if te is None:
                continue  # orphaned selection — skip rather than 500
            # Test classes (AKC tests, AHBA JHD) have NULL trial_event_class_id.
            cls = (
                db.get(TrialEventClass, sel.trial_event_class_id)
                if sel.trial_event_class_id is not None else None
            )
            trial = db.get(Trial, te.trial_id)
            details.append({
                "governing_body": trial.governing_body if trial else "",
                "event_name": te.name,
                "class_name": cls.name if cls else "",
                "call_number": sel.call_number,
                "sel_id": sel.id,
            })
        entry_selections[entry.id] = details

    trial_secretaries = db.query(User).filter_by(is_trial_secretary=True, is_active=True).all()
    ctx = _admin_ctx(request, admin)
    ctx.update({
        "event": event,
        "entries": entries,
        "entry_selections": entry_selections,
        "trial_secretaries": trial_secretaries,
    })
    return templates.TemplateResponse(request, "admin/trial_entries.html", ctx)


@router.get("/trial-entries/{entry_id}", response_class=HTMLResponse)
def trial_entry_detail(
    entry_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    entry = _get_entry(entry_id, db)
    event = _get_event(entry.event_id, db)
    trials = db.query(Trial).filter_by(event_id=event.id).order_by(Trial.governing_body).all()
    trial_events_map = {}
    te_classes_map = {}
    for trial in trials:
        tes = db.query(TrialEvent).filter_by(trial_id=trial.id).order_by(TrialEvent.sort_order).all()
        trial_events_map[trial.id] = tes
        for te in tes:
            te_classes_map[te.id] = db.query(TrialEventClass).filter_by(trial_event_id=te.id).order_by(TrialEventClass.sort_order).all()

    sels = db.query(TrialEntrySelection).filter_by(trial_entry_id=entry_id).all()
    current_sels = {sel.trial_event_id: sel for sel in sels}

    ctx = _admin_ctx(request, admin)
    ctx.update({
        "event": event,
        "entry": entry,
        "trials": trials,
        "trial_events_map": trial_events_map,
        "te_classes_map": te_classes_map,
        "current_sels": current_sels,
    })
    return templates.TemplateResponse(request, "admin/trial_entry_form.html", ctx)


@router.post("/trial-entries/{entry_id}/edit")
async def trial_entry_edit(
    entry_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    entry = _get_entry(entry_id, db)
    entry.handler_name = form.get("handler_name", entry.handler_name).strip()
    entry.handler_email = form.get("handler_email", entry.handler_email).strip()
    entry.handler_phone = form.get("handler_phone", "").strip() or None
    entry.dog_name = form.get("dog_name", entry.dog_name).strip()
    entry.dog_breed = form.get("dog_breed", "").strip() or None
    entry.dog_registration_number = form.get("dog_registration_number", "").strip() or None
    entry.is_paid = form.get("is_paid") == "1"
    db.commit()
    flash(request, "Entry updated.", "success")
    return RedirectResponse(f"/admin/trial-entries/{entry_id}", status_code=303)


# ----------------------------------------------------------------
# Manual trial entry — full AKC/AHBA forms in admin mode
# ----------------------------------------------------------------

def _manual_entry_ctx(request: Request, admin: User, db: Session, event_id: int, body: str):
    """Build the context for rendering the public AKC/AHBA entry form in admin mode."""
    event = _get_event(event_id, db)
    trial = db.query(Trial).filter_by(event_id=event_id, governing_body=body).first()
    if not trial:
        raise HTTPException(status_code=404, detail=f"{body} trial not configured for this event")
    trial_events = (
        db.query(TrialEvent).filter_by(trial_id=trial.id).order_by(TrialEvent.sort_order).all()
    )
    te_classes_map = {
        te.id: db.query(TrialEventClass).filter_by(trial_event_id=te.id)
                  .order_by(TrialEventClass.sort_order).all()
        for te in trial_events
    }
    users = db.query(User).filter_by(is_active=True).order_by(User.name).all()
    ctx = _admin_ctx(request, admin)
    ctx.update({
        "admin_mode": True,
        "event": event,
        "trial": trial,
        "trial_events": trial_events,
        "te_classes_map": te_classes_map,
        "users": users,
        "saved_dogs": [],  # admin manual entry — no saved-dogs auto-fill
        "form_data": {},
        "countries": get_country_choices(),
        "csrf_token": _csrf(request),
    })
    return ctx, event, trial, trial_events, te_classes_map


def _save_manual_entry(
    *, request: Request, db: Session, event, trial, trial_events,
    te_classes_map, form, governing_body: str,
    day_choices: tuple[str, ...],
) -> TrialEntry:
    """Shared save path for both manual AKC and AHBA entries."""
    has_e2 = (
        (governing_body == "AKC" and trial.akc_event_number_2 is not None)
        or (governing_body == "AHBA" and trial.ahba_event_2_judge is not None)
    )
    selection_rows = _parse_selection_rows(
        form, trial_events, te_classes_map, has_e2, day_choices=day_choices,
    )

    def fget(name: str, default: str = "") -> str:
        return (form.get(name, default) or "").strip()

    user_id_raw = fget("user_id")
    user_id = int(user_id_raw) if user_id_raw else None

    dob_raw = fget("dog_dob") or None
    dog_dob = None
    if dob_raw:
        try:
            dog_dob = date.fromisoformat(dob_raw)
        except ValueError:
            dog_dob = None

    dog_call_name = fget("dog_call_name") or None
    dog_name = fget("dog_name")
    if not dog_call_name and dog_name:
        dog_call_name = dog_name

    entry = TrialEntry(
        event_id=event.id,
        user_id=user_id,
        governing_body=governing_body,
        handler_name=fget("handler_name") or "(unknown)",
        handler_email=fget("handler_email") or "",
        handler_phone=fget("handler_phone") or None,
        address_line1=fget("address_line1"),
        address_line2=fget("address_line2") or None,
        city=fget("city"),
        state_province=fget("state_province") or None,
        postal_code=fget("postal_code"),
        country=fget("country", "US") or "US",
        dog_name=dog_name or "(unknown)",
        dog_call_name=dog_call_name,
        dog_breed=fget("dog_breed") or None,
        dog_registration_number=fget("dog_registration_number") or None,
        dog_sex=fget("dog_sex") or None,
        dog_dob=dog_dob,
        dog_sire=fget("dog_sire") or None,
        dog_dam=fget("dog_dam") or None,
        dog_breeder=fget("dog_breeder") or None,
        dog_place_of_birth=fget("dog_place_of_birth") or None,
        akc_number_type=fget("akc_number_type") or None,
        akc_foreign_country=fget("akc_foreign_country") or None,
        akc_handler_name=fget("akc_handler_name") or None,
        akc_handler_address=fget("akc_handler_address") or None,
        akc_separate_entries=bool(form.get("akc_separate_entries")),
        ahba_agent_name=fget("ahba_agent_name") or None,
        ahba_agent_phone=fget("ahba_agent_phone") or None,
        ahba_agent_email=fget("ahba_agent_email") or None,
        signature=fget("signature") or None,
        total_fee_cents=_compute_fee_from_rows(selection_rows, trial_events),
        is_paid=bool(form.get("is_paid")),
        is_manual_entry=True,
    )
    db.add(entry)
    db.flush()

    for row in selection_rows:
        db.add(TrialEntrySelection(
            trial_entry_id=entry.id,
            trial_event_id=row["te_id"],
            trial_event_class_id=row["class_id"],
            akc_trial_pref=row["pref"],
            day_preference=row["day_preference"],
        ))

    db.commit()
    db.refresh(entry)
    return entry


@router.get("/events/{event_id}/trial-entries/new-akc", response_class=HTMLResponse)
def trial_entry_manual_akc_get(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ctx, *_ = _manual_entry_ctx(request, admin, db, event_id, "AKC")
    return templates.TemplateResponse(request, "trials/akc_entry_form.html", ctx)


@router.post("/events/{event_id}/trial-entries/new-akc")
async def trial_entry_manual_akc_post(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    _, event, trial, trial_events, te_classes_map = _manual_entry_ctx(request, admin, db, event_id, "AKC")
    entry = _save_manual_entry(
        request=request, db=db, event=event, trial=trial,
        trial_events=trial_events, te_classes_map=te_classes_map,
        form=form, governing_body="AKC",
        day_choices=("friday", "saturday"),
    )
    flash(request, f"Manual AKC entry created for {entry.handler_name} / {entry.dog_name}.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/trial-entries", status_code=303)


@router.get("/events/{event_id}/trial-entries/new-ahba", response_class=HTMLResponse)
def trial_entry_manual_ahba_get(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ctx, *_ = _manual_entry_ctx(request, admin, db, event_id, "AHBA")
    return templates.TemplateResponse(request, "trials/ahba_entry_form.html", ctx)


@router.post("/events/{event_id}/trial-entries/new-ahba")
async def trial_entry_manual_ahba_post(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    _, event, trial, trial_events, te_classes_map = _manual_entry_ctx(request, admin, db, event_id, "AHBA")
    entry = _save_manual_entry(
        request=request, db=db, event=event, trial=trial,
        trial_events=trial_events, te_classes_map=te_classes_map,
        form=form, governing_body="AHBA",
        day_choices=("saturday", "sunday"),
    )
    flash(request, f"Manual AHBA entry created for {entry.handler_name} / {entry.dog_name}.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/trial-entries", status_code=303)


# ============================================================
# Call Numbers (Phase 10)
# ============================================================

@router.get("/events/{event_id}/call-numbers", response_class=HTMLResponse)
def call_numbers_get(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    event = _get_event(event_id, db)
    trials = db.query(Trial).filter_by(event_id=event_id).order_by(Trial.governing_body).all()

    # Build: per trial → per TrialEvent → selections with call numbers
    trial_data = []
    for trial in trials:
        te_data = []
        for te in db.query(TrialEvent).filter_by(trial_id=trial.id).order_by(TrialEvent.sort_order).all():
            sels = (
                db.query(TrialEntrySelection)
                .filter_by(trial_event_id=te.id)
                .order_by(TrialEntrySelection.call_number.asc().nullslast())
                .all()
            )
            rows = []
            for sel in sels:
                entry = db.get(TrialEntry, sel.trial_entry_id)
                cls = db.get(TrialEventClass, sel.trial_event_class_id)
                rows.append({"sel": sel, "entry": entry, "cls": cls})
            te_data.append({"te": te, "rows": rows})
        trial_data.append({"trial": trial, "te_data": te_data})

    ctx = _admin_ctx(request, admin)
    ctx.update({"event": event, "trial_data": trial_data})
    return templates.TemplateResponse(request, "admin/call_numbers.html", ctx)


@router.post("/events/{event_id}/call-numbers")
async def call_numbers_post(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Save call number assignments."""
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)

    errors = []
    updates: dict[int, int | None] = {}  # sel_id → call_number

    for key, value in form.items():
        if key.startswith("cn_"):
            sel_id = int(key[3:])
            val = value.strip()
            if val:
                try:
                    updates[sel_id] = int(val)
                except ValueError:
                    errors.append(f"Invalid call number for selection {sel_id}.")
            else:
                updates[sel_id] = None

    if errors:
        for msg in errors:
            flash(request, msg, "error")
        return RedirectResponse(f"/admin/events/{event_id}/call-numbers", status_code=303)

    # Check uniqueness within each trial event
    by_te: dict[int, list[int]] = {}
    for sel_id, cn in updates.items():
        if cn is None:
            continue
        sel = db.get(TrialEntrySelection, sel_id)
        by_te.setdefault(sel.trial_event_id, []).append(cn)

    for te_id, numbers in by_te.items():
        if len(numbers) != len(set(numbers)):
            te = db.get(TrialEvent, te_id)
            errors.append(f"Duplicate call numbers in '{te.name}'. Each dog must have a unique number.")

    if errors:
        for msg in errors:
            flash(request, msg, "error")
        return RedirectResponse(f"/admin/events/{event_id}/call-numbers", status_code=303)

    for sel_id, cn in updates.items():
        sel = db.get(TrialEntrySelection, sel_id)
        if sel:
            sel.call_number = cn
    db.commit()
    flash(request, "Call numbers saved.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/call-numbers", status_code=303)


# ============================================================
# Registrations (Fun Run / Smart Dog Day)
# ============================================================

@router.get("/events/{event_id}/registrations", response_class=HTMLResponse)
def registrations_list(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    event = _get_event(event_id, db)
    regs = db.query(Registration).filter_by(event_id=event_id).order_by(Registration.id).all()
    ctx = _admin_ctx(request, admin)
    ctx.update({"event": event, "registrations": regs})
    return templates.TemplateResponse(request, "admin/registrations.html", ctx)


@router.post("/registrations/{reg_id}/edit")
async def registration_edit(
    reg_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    reg = db.get(Registration, reg_id)
    if not reg:
        raise HTTPException(404)
    reg.name = form.get("name", reg.name).strip()
    reg.email = form.get("email", reg.email).strip()
    reg.dog_name = form.get("dog_name", reg.dog_name).strip()
    reg.is_paid = form.get("is_paid") == "1"
    db.commit()
    flash(request, "Registration updated.", "success")
    return RedirectResponse(f"/admin/events/{reg.event_id}/registrations", status_code=303)


@router.post("/registrations/new")
async def registration_manual(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    event_id = int(form.get("event_id", 0))
    _get_event(event_id, db)
    reg = Registration(
        event_id=event_id,
        name=form.get("name", "").strip(),
        email=form.get("email", "").strip(),
        phone=form.get("phone", "").strip() or None,
        address_line1=form.get("address_line1", "").strip(),
        city=form.get("city", "").strip(),
        postal_code=form.get("postal_code", "").strip(),
        country=form.get("country", "US"),
        dog_name=form.get("dog_name", "").strip(),
        pricing_tier=form.get("pricing_tier", "late"),
        fee_cents=int(form.get("fee_cents", 0)),
        is_paid=form.get("is_paid") == "1",
        is_manual_entry=True,
    )
    db.add(reg)
    db.commit()
    flash(request, f"Manual registration created for {reg.name}.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/registrations", status_code=303)


# ============================================================
# PDF Upload (Phase 10)
# ============================================================

@router.post("/events/{event_id}/pdfs/upload")
async def pdf_upload(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    governing_body: str = Form(...),
    document_type: str = Form(...),
    pdf_file: UploadFile = File(...),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    _get_event(event_id, db)

    os.makedirs(_PDF_DIR, exist_ok=True)
    safe_name = f"event{event_id}_{governing_body.upper()}_{document_type}_{secrets.token_hex(6)}.pdf"
    dest = os.path.join(_PDF_DIR, safe_name)

    with open(dest, "wb") as f:
        shutil.copyfileobj(pdf_file.file, f)

    # Upsert EventPdf row
    existing = (
        db.query(EventPdf)
        .filter_by(event_id=event_id, governing_body=governing_body.upper(), document_type=document_type)
        .first()
    )
    if existing:
        # Remove old file
        old_path = os.path.join(_PDF_DIR, existing.filename)
        if os.path.isfile(old_path):
            os.remove(old_path)
        existing.filename = safe_name
    else:
        db.add(EventPdf(
            event_id=event_id,
            governing_body=governing_body.upper(),
            document_type=document_type,
            filename=safe_name,
        ))
    db.commit()
    flash(request, f"{document_type.replace('_', ' ').title()} PDF uploaded.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/edit", status_code=303)


@router.post("/pdfs/{pdf_id}/delete")
async def pdf_delete(
    pdf_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    pdf = db.get(EventPdf, pdf_id)
    if not pdf:
        raise HTTPException(404)
    event_id = pdf.event_id
    path = os.path.join(_PDF_DIR, pdf.filename)
    if os.path.isfile(path):
        os.remove(path)
    db.delete(pdf)
    db.commit()
    flash(request, "PDF deleted.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/edit", status_code=303)


# ============================================================
# Members
# ============================================================

@router.get("/members", response_class=HTMLResponse)
def member_list(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    members = db.query(Member).order_by(Member.name).all()
    ctx = _admin_ctx(request, admin)
    ctx["members"] = members
    ctx["current_year"] = date.today().year
    return templates.TemplateResponse(request, "admin/member_list.html", ctx)


@router.get("/members/new", response_class=HTMLResponse)
def member_new_get(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).filter_by(is_active=True).order_by(User.name).all()
    ctx = _admin_ctx(request, admin)
    ctx.update({"member": None, "form_data": {}, "users": users, "current_year": date.today().year})
    return templates.TemplateResponse(request, "admin/member_form.html", ctx)


@router.post("/members/new")
async def member_new_post(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    member = Member()
    _apply_member_form(member, form)
    db.add(member)
    db.commit()
    flash(request, f"Member '{member.name}' added.", "success")
    return RedirectResponse("/admin/members", status_code=303)


@router.get("/members/{member_id}/edit", response_class=HTMLResponse)
def member_edit_get(
    member_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    member = _get_member(member_id, db)
    users = db.query(User).filter_by(is_active=True).order_by(User.name).all()
    ctx = _admin_ctx(request, admin)
    ctx.update({
        "member": member,
        "form_data": _member_to_form(member),
        "users": users,
        "current_year": date.today().year,
    })
    return templates.TemplateResponse(request, "admin/member_form.html", ctx)


@router.post("/members/{member_id}/edit")
async def member_edit_post(
    member_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    member = _get_member(member_id, db)
    _apply_member_form(member, form)
    db.commit()
    flash(request, "Member updated.", "success")
    return RedirectResponse("/admin/members", status_code=303)


@router.post("/members/{member_id}/delete")
async def member_delete(
    member_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    member = _get_member(member_id, db)
    db.delete(member)
    db.commit()
    flash(request, "Member removed.", "success")
    return RedirectResponse("/admin/members", status_code=303)


@router.post("/members/{member_id}/set-status")
async def member_set_status(
    member_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    new_status = (form.get("status") or "").strip()
    if new_status not in Member.ALLOWED_STATUSES:
        flash(request, f"Invalid status '{new_status}'.", "error")
        return RedirectResponse("/admin/members", status_code=303)
    member = _get_member(member_id, db)
    member.status = new_status
    db.commit()
    flash(request, f"{member.name} → {new_status.title()}.", "success")
    return RedirectResponse("/admin/members", status_code=303)


@router.get("/members/bulk-add", response_class=HTMLResponse)
def member_bulk_add_get(
    request: Request,
    admin: User = Depends(require_admin),
):
    ctx = _admin_ctx(request, admin)
    ctx["current_year"] = date.today().year
    return templates.TemplateResponse(request, "admin/members_bulk_add.html", ctx)


@router.post("/members/bulk-add")
async def member_bulk_add_post(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    raw = form.get("members_text", "") or ""
    status = (form.get("status") or "member").strip()
    if status not in Member.ALLOWED_STATUSES:
        status = "member"
    current_year = date.today().year

    created = 0
    skipped: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = _parse_bulk_member_line(line, default_year=current_year)
        if not parsed:
            skipped.append(line)
            continue
        name, email, year = parsed
        db.add(Member(
            name=name,
            email=email,
            membership_year=year,
            status=status,
            address_line1="",
            city="",
            postal_code="",
            country="US",
        ))
        created += 1
    db.commit()

    msg = f"Bulk add: {created} created"
    if skipped:
        msg += f", {len(skipped)} skipped: " + "; ".join(skipped[:5])
        if len(skipped) > 5:
            msg += f" (+{len(skipped) - 5} more)"
    flash(request, msg, "success" if created else "error")
    return RedirectResponse("/admin/members", status_code=303)


# ============================================================
# Users / Trial Secretary
# ============================================================

@router.get("/users", response_class=HTMLResponse)
def user_list(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.name).all()
    ctx = _admin_ctx(request, admin)
    ctx["users"] = users
    return templates.TemplateResponse(request, "admin/user_list.html", ctx)


@router.post("/users/{user_id}/toggle-trial-secretary")
async def toggle_trial_secretary(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    user.is_trial_secretary = not user.is_trial_secretary
    db.commit()
    status = "designated" if user.is_trial_secretary else "removed"
    flash(request, f"{user.name} {status} as trial secretary.", "success")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle-admin")
async def toggle_admin(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    if user.id == admin.id:
        flash(request, "You cannot change your own role.", "error")
        return RedirectResponse("/admin/users", status_code=303)
    user.role = "admin" if user.role != "admin" else "user"
    db.commit()
    flash(request, f"{user.name} role set to '{user.role}'.", "success")
    return RedirectResponse("/admin/users", status_code=303)


# ----------------------------------------------------------------
# Unregistered-user create/edit + per-user dog management
# ----------------------------------------------------------------

def _user_to_form(u: User) -> dict:
    return {
        "name": u.name or "",
        "email": u.email or "",
        "phone": u.phone or "",
        "address_line1": u.address_line1 or "",
        "address_line2": u.address_line2 or "",
        "city": u.city or "",
        "state_province": u.state_province or "",
        "postal_code": u.postal_code or "",
        "country": u.country or "US",
    }


def _apply_user_form(u: User, form) -> None:
    u.name = (form.get("name") or "").strip()
    u.email = (form.get("email") or "").strip().lower()
    u.phone = (form.get("phone") or "").strip() or None
    u.address_line1 = (form.get("address_line1") or "").strip()
    u.address_line2 = (form.get("address_line2") or "").strip() or None
    u.city = (form.get("city") or "").strip()
    u.state_province = (form.get("state_province") or "").strip() or None
    u.postal_code = (form.get("postal_code") or "").strip()
    u.country = (form.get("country") or "US").strip() or "US"


@router.get("/users/new", response_class=HTMLResponse)
def user_new_get(
    request: Request,
    admin: User = Depends(require_admin),
):
    ctx = _admin_ctx(request, admin)
    ctx.update({"user": None, "form_data": {}, "dogs": []})
    return templates.TemplateResponse(request, "admin/user_form.html", ctx)


@router.post("/users/new")
async def user_new_post(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.utils.account_linking import link_orphan_records
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)

    email = (form.get("email") or "").strip().lower()
    name = (form.get("name") or "").strip()
    if not email or not name:
        flash(request, "Name and email are required.", "error")
        return RedirectResponse("/admin/users/new", status_code=303)
    if db.query(User).filter_by(email=email).first():
        flash(request, f"A user with email {email} already exists.", "error")
        return RedirectResponse("/admin/users/new", status_code=303)

    u = User(email=email, password_hash=None, is_unregistered=True, role="user")
    _apply_user_form(u, form)
    db.add(u)
    db.flush()
    # Auto-link any orphan entries/registrations matching this email.
    link_orphan_records(u, db)
    db.commit()
    flash(request, f"Unregistered user '{u.name}' created.", "success")
    return RedirectResponse(f"/admin/users/{u.id}/edit", status_code=303)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
def user_edit_get(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    from app.models.dog import Dog
    dogs = db.query(Dog).filter_by(user_id=user.id).order_by(Dog.dog_name).all()
    ctx = _admin_ctx(request, admin)
    ctx.update({"user": user, "form_data": _user_to_form(user), "dogs": dogs})
    return templates.TemplateResponse(request, "admin/user_form.html", ctx)


@router.post("/users/{user_id}/edit")
async def user_edit_post(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    _apply_user_form(user, form)
    db.commit()
    flash(request, f"{user.name} updated.", "success")
    return RedirectResponse(f"/admin/users/{user.id}/edit", status_code=303)


@router.post("/users/{user_id}/dogs/add")
async def user_dog_add(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models.dog import Dog
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    dog_name = (form.get("dog_name") or "").strip()
    if not dog_name:
        flash(request, "Dog's name is required.", "error")
        return RedirectResponse(f"/admin/users/{user.id}/edit", status_code=303)

    def _f(k):
        return (form.get(k) or "").strip() or None

    dob_raw = _f("dog_dob")
    dog_dob = None
    if dob_raw:
        try:
            dog_dob = date.fromisoformat(dob_raw)
        except ValueError:
            pass

    db.add(Dog(
        user_id=user.id,
        dog_name=dog_name,
        dog_call_name=_f("dog_call_name"),
        dog_breed=_f("dog_breed"),
        dog_sex=_f("dog_sex"),
        dog_dob=dog_dob,
        akc_number_type=_f("akc_number_type"),
        akc_registration_number=_f("akc_registration_number"),
        ahba_registration_number=_f("ahba_registration_number"),
    ))
    db.commit()
    flash(request, f"Added dog '{dog_name}' for {user.name}.", "success")
    return RedirectResponse(f"/admin/users/{user.id}/edit", status_code=303)


@router.post("/users/{user_id}/dogs/{dog_id}/delete")
async def user_dog_delete(
    user_id: int,
    dog_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models.dog import Dog
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    dog = db.query(Dog).filter_by(id=dog_id, user_id=user_id).first()
    if dog:
        db.delete(dog)
        db.commit()
        flash(request, f"Removed {dog.dog_name}.", "success")
    return RedirectResponse(f"/admin/users/{user_id}/edit", status_code=303)


# ============================================================
# Email verification (Phase 11a) — stubs wired to email_helpers
# ============================================================

@router.post("/trial-entries/{entry_id}/send-verification")
async def send_entry_verification(
    entry_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    entry = _get_entry(entry_id, db)
    from app.utils.email_helpers import send_trial_entry_verification
    background_tasks.add_task(send_trial_entry_verification, entry_id=entry_id)
    entry.verification_sent = True
    db.commit()
    flash(request, f"Verification email queued for {entry.handler_email}.", "success")
    return RedirectResponse(f"/admin/trial-entries/{entry_id}", status_code=303)


@router.post("/registrations/{reg_id}/send-verification")
async def send_reg_verification(
    reg_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    reg = db.get(Registration, reg_id)
    if not reg:
        raise HTTPException(404)
    from app.utils.email_helpers import send_registration_verification
    background_tasks.add_task(send_registration_verification, reg_id=reg_id)
    reg.verification_sent = True
    db.commit()
    flash(request, f"Verification email queued for {reg.email}.", "success")
    return RedirectResponse(f"/admin/events/{reg.event_id}/registrations", status_code=303)


@router.post("/events/{event_id}/send-all-verifications")
async def send_all_verifications(
    event_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    from app.utils.email_helpers import send_trial_entry_verification, send_registration_verification

    entries = db.query(TrialEntry).filter_by(event_id=event_id, verification_sent=False).all()
    for entry in entries:
        background_tasks.add_task(send_trial_entry_verification, entry_id=entry.id)
        entry.verification_sent = True

    regs = db.query(Registration).filter_by(event_id=event_id, verification_sent=False).all()
    for reg in regs:
        background_tasks.add_task(send_registration_verification, reg_id=reg.id)
        reg.verification_sent = True

    db.commit()
    total = len(entries) + len(regs)
    flash(request, f"{total} verification email(s) queued.", "success")
    return RedirectResponse(f"/admin/events/{event_id}/edit", status_code=303)


# ============================================================
# Helpers
# ============================================================

def _get_event(event_id: int, db: Session) -> Event:
    event = db.query(Event).filter_by(id=event_id, is_deleted=False).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _get_trial(trial_id: int, db: Session) -> Trial:
    trial = db.get(Trial, trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    return trial


def _get_entry(entry_id: int, db: Session) -> TrialEntry:
    entry = db.get(TrialEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


def _get_member(member_id: int, db: Session) -> Member:
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _apply_event_form(event: Event, form) -> None:
    event.title = form.get("title", "").strip()
    event.event_type = form.get("event_type", "trial")
    event.location = form.get("location", "").strip() or None
    event.description = form.get("description", "").strip() or None

    start_raw = form.get("start_date", "")
    end_raw = form.get("end_date", "")
    if start_raw:
        event.start_date = date.fromisoformat(start_raw)
    if end_raw:
        event.end_date = date.fromisoformat(end_raw)
    elif start_raw:
        event.end_date = event.start_date

    for field in ("fee_pre_member_cents", "fee_pre_general_cents", "fee_late_cents"):
        raw = form.get(field, "").strip()
        setattr(event, field, int(raw) if raw else None)

    pre_close_raw = form.get("pre_entry_close_dt", "").strip()
    if pre_close_raw:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from datetime import UTC
        EASTERN = ZoneInfo("America/New_York")
        event.pre_entry_close_dt = datetime.fromisoformat(pre_close_raw).replace(
            tzinfo=EASTERN
        ).astimezone(UTC)
    else:
        event.pre_entry_close_dt = None


def _event_to_form(event: Event) -> dict:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
    pre_close = ""
    if event.pre_entry_close_dt:
        pre_close = event.pre_entry_close_dt.astimezone(EASTERN).strftime("%Y-%m-%dT%H:%M")
    return {
        "title": event.title,
        "event_type": event.event_type,
        "start_date": event.start_date.isoformat() if event.start_date else "",
        "end_date": event.end_date.isoformat() if event.end_date else "",
        "location": event.location or "",
        "description": event.description or "",
        "fee_pre_member_cents": event.fee_pre_member_cents or "",
        "fee_pre_general_cents": event.fee_pre_general_cents or "",
        "fee_late_cents": event.fee_late_cents or "",
        "pre_entry_close_dt": pre_close,
    }


def _apply_trial_form(trial: Trial, form, event: Event) -> None:
    trial.governing_body = form.get("governing_body", "AKC").upper()
    trial.akc_event_number = form.get("akc_event_number", "").strip() or None
    trial.akc_event_number_2 = form.get("akc_event_number_2", "").strip() or None

    # reg_close_dt: admin can override or leave blank to auto-compute
    override = form.get("reg_close_dt_override", "").strip()
    if override:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from datetime import UTC
        EASTERN = ZoneInfo("America/New_York")
        trial.reg_close_dt = datetime.fromisoformat(override).replace(
            tzinfo=EASTERN
        ).astimezone(UTC)
    else:
        # Auto-compute from event start_date
        if trial.governing_body == "AKC":
            trial.reg_close_dt = compute_akc_close(event.start_date)
        else:
            trial.reg_close_dt = compute_ahba_close(event.start_date)


def _trial_to_form(trial: Trial) -> dict:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
    close_str = ""
    if trial.reg_close_dt:
        close_str = trial.reg_close_dt.astimezone(EASTERN).strftime("%Y-%m-%dT%H:%M")
    return {
        "governing_body": trial.governing_body,
        "akc_event_number": trial.akc_event_number or "",
        "akc_event_number_2": trial.akc_event_number_2 or "",
        "reg_close_dt_override": close_str,
    }


def _apply_trial_events_form(trial_id: int, form, db: Session) -> None:
    """Parse te_name_N / te_classes_N fields to create TrialEvent + TrialEventClass rows."""
    i = 0
    while True:
        name = form.get(f"te_name_{i}", "").strip()
        if not name:
            break
        te = TrialEvent(trial_id=trial_id, name=name, sort_order=i)
        db.add(te)
        db.flush()
        classes_raw = form.get(f"te_classes_{i}", "").strip()
        for j, cls_name in enumerate(c.strip() for c in classes_raw.split(",") if c.strip()):
            db.add(TrialEventClass(trial_event_id=te.id, name=cls_name, sort_order=j))
        i += 1


def _apply_member_form(member: Member, form) -> None:
    member.name = form.get("name", "").strip()
    member.email = form.get("email", "").strip() or None
    member.membership_year = int(form.get("membership_year", date.today().year))
    user_id_raw = form.get("user_id", "").strip()
    member.user_id = int(user_id_raw) if user_id_raw else None
    member.address_line1 = form.get("address_line1", "").strip()
    member.city = form.get("city", "").strip()
    member.postal_code = form.get("postal_code", "").strip()
    member.country = form.get("country", "US")
    status = form.get("status", "member").strip()
    member.status = status if status in Member.ALLOWED_STATUSES else "member"


def _parse_bulk_member_line(line: str, default_year: int) -> tuple[str, str, int] | None:
    """Parse a single bulk-import line into (name, email, year), or None if unparseable.

    Accepted formats (whitespace-tolerant):
      "Jane Smith, jane@example.com"
      "Jane Smith, jane@example.com, 2026"
      "Jane Smith <jane@example.com>"
    """
    import re
    s = line.strip()
    if not s:
        return None
    # Mailto-style: "Name <email>"
    m = re.match(r"^\s*(.+?)\s*<\s*([^<>\s]+@[^<>\s]+)\s*>\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip(), default_year
    # Comma-separated: name, email [, year]
    parts = [p.strip() for p in s.split(",")]
    if len(parts) < 2:
        return None
    name, email = parts[0], parts[1]
    if not name or "@" not in email:
        return None
    year = default_year
    if len(parts) >= 3 and parts[2].isdigit():
        year = int(parts[2])
    return name, email, year


def _member_to_form(member: Member) -> dict:
    return {
        "name": member.name,
        "email": member.email or "",
        "membership_year": member.membership_year,
        "user_id": member.user_id or "",
        "address_line1": member.address_line1 or "",
        "city": member.city or "",
        "postal_code": member.postal_code or "",
        "country": member.country or "US",
        "status": member.status,
    }
