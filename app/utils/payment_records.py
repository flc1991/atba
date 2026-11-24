"""Writes to payment history. Every change to `is_paid` goes through here.

Callers commit.
"""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.config import settings
from app.models.payment import Payment
from app.models.registration import Registration
from app.models.trial import TrialEntry
from app.utils.payment_checks import check_deadline, open_check, record_amount_cents, record_kind
from app.utils.processing_fee import record_processing_fee_cents
from app.utils.registration_windows import EASTERN, format_et

Record = Registration | TrialEntry
MODELS = {"registration": Registration, "entry": TrialEntry}


def get_record(db: Session, kind: str, record_id: int | None) -> Record | None:
    model = MODELS.get(kind)
    return db.get(model, record_id) if model and record_id is not None else None


def who(user) -> str | None:
    return f"{user.name} <{user.email}>" if user else None


def parse_dollars(raw: str | None) -> int:
    """Dollars to cents. Raises ValueError unless it is a plain amount of 0 or more."""
    try:
        amount = Decimal((raw or "").strip().lstrip("$").replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("Enter a valid dollar amount.") from exc
    if not amount.is_finite() or amount < 0 or amount > 100_000:
        raise ValueError("Enter a valid dollar amount.")
    return int((amount * 100).to_integral_value())


def today_et() -> date:
    return datetime.now(EASTERN).date()


def history(db: Session, rec: Record) -> list[Payment]:
    return (
        db.query(Payment)
        .filter_by(kind=record_kind(rec), record_id=rec.id)
        .order_by(Payment.id)
        .all()
    )


def paid_amount_cents(db: Session, rec: Record) -> int:
    """What was actually received (including any processing fee), else the amount due."""
    if rec.is_paid:
        done = [p for p in history(db, rec) if p.status == "completed" and p.received_cents is not None]
        if done:
            return done[-1].received_cents
    return record_amount_cents(rec)


def awaiting_checks(db: Session, kind: str) -> dict[int, Payment]:
    """Outstanding checks of a kind, keyed by record id."""
    return {
        p.record_id: p
        for p in db.query(Payment).filter_by(kind=kind, status="awaiting_check")
        if p.record_id is not None
    }


def checks_enabled() -> bool:
    return bool(settings.CHECK_PAYEE_NAME and settings.CHECK_MAILING_ADDRESS)


def check_info(db: Session, rec: Record, event, payment: Payment | None = None) -> dict:
    """Everything needed to tell someone where to send a check and by when."""
    deadline = check_deadline(rec, event, db)
    now = datetime.now(UTC)
    return {
        "payee": settings.CHECK_PAYEE_NAME,
        "address": settings.CHECK_MAILING_ADDRESS,
        "amount_cents": payment.total_cents if payment else record_amount_cents(rec),
        "deadline": format_et(deadline) if deadline else None,
        "overdue": deadline is not None and now > deadline,
        "soon": deadline is not None and now + timedelta(days=7) > deadline,
        "payment": payment,
    }


def request_check(db: Session, rec: Record) -> Payment:
    """Record that a check is on its way. The record stays unpaid."""
    existing = open_check(db, rec)
    if existing:
        return existing
    cents = record_amount_cents(rec)
    payment = Payment(
        kind=record_kind(rec), record_id=rec.id, method="check", status="awaiting_check",
        entry_cents=cents, total_cents=cents,
    )
    db.add(payment)
    return payment


def record_payment(
    db: Session, rec: Record, method: str, *, received_cents: int, received_on: date | None = None,
    reference: str | None = None, user=None,
) -> bool:
    """Mark a record paid. Returns False, changing nothing, if it already was."""
    if rec.is_paid:
        return False
    now = datetime.now(UTC)
    payment = open_check(db, rec)
    if payment is not None and method != "check":
        payment.status, payment.voided_at = "void", now
        payment.note = f"Replaced by {method} payment"
        payment = None
    if payment is None:
        cents = record_amount_cents(rec)
        fee = record_processing_fee_cents(db, rec) if method == "paypal" else 0
        if received_cents != cents + fee:
            fee = 0
        payment = Payment(
            kind=record_kind(rec), record_id=rec.id, method=method, status="completed",
            entry_cents=cents, processing_fee_cents=fee, total_cents=cents + fee,
        )
        db.add(payment)
    payment.status = "completed"
    payment.received_cents = received_cents
    payment.received_on = received_on or today_et()
    payment.reference = reference
    payment.paid_at = now
    payment.recorded_by = who(user)
    rec.is_paid = True
    if reference:
        rec.payment_reference = f"Check #{reference}" if method == "check" and reference.isdigit() else reference
    db.flush()
    return True


def mark_unpaid(db: Session, rec: Record, user) -> None:
    """Reverse a paid record, keeping the voided payments as history."""
    rec.is_paid = False
    rec.payment_reference = None
    for payment in history(db, rec):
        if payment.status == "completed":
            payment.status, payment.voided_at = "void", datetime.now(UTC)
            payment.note = f"Marked unpaid by {who(user)}"
    db.flush()


def detach_payments(db: Session, rec: Record, label: str, user) -> None:
    """Keep the history of a record that is about to be deleted."""
    for payment in history(db, rec):
        if payment.status == "awaiting_check":
            payment.status, payment.voided_at = "void", datetime.now(UTC)
        payment.note = f"Deleted {record_kind(rec)} #{rec.id} ({label}) by {who(user)}"[:500]
        payment.record_id = None
    db.flush()
