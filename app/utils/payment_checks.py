"""Access, opening and pricing checks shared by checkout, confirmation and entry routes."""
import secrets
from datetime import UTC, datetime, time
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.payment import Payment
from app.models.registration import Registration, RegistrationDog
from app.models.trial import Trial, TrialEntry, TrialEntrySelection
from app.utils.registration_windows import (
    EASTERN,
    _ensure_utc,
    get_event_status,
    get_pricing_tier,
    get_trial_status,
)


def can_access(rec: Registration | TrialEntry, user, token: str | None) -> bool:
    """True for an admin, the record's owner, or a holder of the record's token."""
    if user is not None and (user.role == "admin" or rec.user_id == user.id):
        return True
    if not token or not rec.access_token:
        return False
    return secrets.compare_digest(token.encode(), rec.access_token.encode())


def record_kind(rec: Registration | TrialEntry) -> str:
    return "registration" if isinstance(rec, Registration) else "entry"


def open_check(db: Session, rec: Registration | TrialEntry) -> Payment | None:
    """The check this record is waiting for, if any."""
    return db.query(Payment).filter_by(
        kind=record_kind(rec), record_id=rec.id, status="awaiting_check"
    ).first()


def check_deadline(rec: Registration | TrialEntry, event: Event, db: Session) -> datetime | None:
    """When a check must arrive (UTC): the entry deadline. For Fun Run / Smart Dog Day
    that is the pre-entry cutoff at the pre-entry price, else the end of the start date."""
    if isinstance(rec, TrialEntry):
        trial = _trial_for(rec, db)
        return _ensure_utc(trial.reg_close_dt) if trial and trial.reg_close_dt else None
    if rec.pricing_tier != "late" and event.pre_entry_close_dt:
        return _ensure_utc(event.pre_entry_close_dt)
    return datetime.combine(event.start_date, time(23, 59, 59), tzinfo=EASTERN).astimezone(UTC)


def _trial_for(entry: TrialEntry, db: Session) -> Trial | None:
    return db.query(Trial).filter_by(
        event_id=entry.event_id, governing_body=entry.governing_body
    ).first()


def checkout_url(rec: Registration | TrialEntry) -> str:
    return f"/payments/checkout/{record_kind(rec)}/{rec.id}?" + urlencode({"t": rec.access_token or ""})


def confirmation_url(rec: Registration | TrialEntry, **extra) -> str:
    if isinstance(rec, Registration):
        path, id_param = "register/confirmation", "registration_id"
    else:
        path, id_param = "trial/confirmation", "entry_id"
    query = urlencode({id_param: rec.id, "t": rec.access_token or "", **extra})
    return f"/events/{rec.event_id}/{path}?{query}"


def record_amount_cents(rec: Registration | TrialEntry) -> int:
    return rec.fee_cents if isinstance(rec, Registration) else rec.total_fee_cents


def resolve_event_fee(event: Event, tier: str) -> int | None:
    """Fee in cents for a tier, falling back to the late fee. None means not set;
    0 means deliberately free."""
    fee = {
        "pre_member": event.fee_pre_member_cents,
        "pre_general": event.fee_pre_general_cents,
        "late": event.fee_late_cents,
    }.get(tier)
    return fee if fee is not None else event.fee_late_cents


def not_payable_reason(
    rec: Registration | TrialEntry, event: Event | None, db: Session
) -> str | None:
    """Why this unpaid record cannot be paid online now, or None if it can.

    Manual entries were accepted by an admin, so closing dates do not apply to them.
    """
    if event is None or event.is_deleted or not event.is_published:
        return "This event is no longer available."
    # A check locks its price until its deadline. After that, switching to PayPal
    # could dodge the late rate, so it needs the secretary.
    if open_check(db, rec):
        deadline = check_deadline(rec, event, db)
        if deadline and datetime.now(UTC) > deadline:
            return "The deadline for your check has passed. Please contact the trial secretary."
    if rec.is_manual_entry:
        # A $0 manual entry has no fee recorded; it is not "free" and must not auto-pay.
        return "No fee has been set for this entry." if record_amount_cents(rec) <= 0 else None
    if isinstance(rec, TrialEntry):
        if not db.query(TrialEntrySelection.id).filter_by(trial_entry_id=rec.id).first():
            return "This entry has no classes selected."
        trial = _trial_for(rec, db)
        if trial is None or get_trial_status(trial.reg_close_dt) != "open":
            return "Entries for this trial are not open, so payment cannot be made online."
        return None
    if get_event_status(event) != "open":
        return "Registration for this event is not open, so payment cannot be made online."
    return None


def refresh_price_tier(rec: Registration | TrialEntry, event: Event, db: Session) -> bool:
    """Move an unpaid online registration to the late price once pre-entry has ended.

    Returns True if the price tier changed. The caller commits.
    """
    if not isinstance(rec, Registration) or rec.is_manual_entry or rec.pricing_tier == "late":
        return False
    if get_pricing_tier(event.pre_entry_close_dt) != "late" or event.fee_late_cents is None:
        return False
    runs = 1
    if event.event_type == "fun_run":
        dogs = db.query(RegistrationDog).filter_by(registration_id=rec.id).all()
        runs = sum(len(d.events) for d in dogs)
    rec.pricing_tier = "late"
    rec.fee_cents = runs * event.fee_late_cents
    return True
