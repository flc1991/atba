from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import base_context, get_db
from app.models.event import Event
from app.models.trial import Trial
from app.utils.registration_windows import _ensure_utc

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# Event types where the user is expected to register before attending.
REGISTRATION_REQUIRED_TYPES = {"trial", "fun_run", "smart_dog_day"}


def _is_registering(event: Event, db: Session) -> bool:
    """Return True if registration is currently open for the given event."""
    if event.event_type == "trial":
        trials = db.query(Trial).filter_by(event_id=event.id).all()
        now = datetime.now(UTC)
        return any(
            t.reg_close_dt and _ensure_utc(t.reg_close_dt) > now
            for t in trials
        )
    if event.event_type in ("fun_run", "smart_dog_day"):
        # Pricing must be configured for the event to accept registrations.
        return event.fee_late_cents is not None
    # Other types (meeting, picnic, other) don't require registration.
    return True


@router.get("/", response_class=HTMLResponse)
def home(request: Request, ctx: dict = Depends(base_context), db: Session = Depends(get_db)):
    today = date.today()
    candidates = (
        db.query(Event)
        .filter(Event.is_published.is_(True), Event.is_deleted.is_(False), Event.end_date >= today)
        .order_by(Event.start_date)
        .all()
    )

    upcoming = []
    for e in candidates:
        requires_reg = e.event_type in REGISTRATION_REQUIRED_TYPES
        if not requires_reg or _is_registering(e, db):
            e.requires_registration = requires_reg  # used by template button text
            upcoming.append(e)
            if len(upcoming) >= 3:
                break

    ctx["upcoming_events"] = upcoming
    return templates.TemplateResponse(request, "main/home.html", ctx)


@router.get("/about", response_class=HTMLResponse)
def about(request: Request, ctx: dict = Depends(base_context)):
    return templates.TemplateResponse(request, "main/atba_info.html", ctx)
