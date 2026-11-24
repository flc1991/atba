"""Turns PayPal orders and webhooks into payment records.

An order is only money once captured, and only the server captures. A record is marked
paid only from what PayPal's API reports, never from a redirect or an event's contents.
No database write is left uncommitted while waiting on PayPal (SQLite locks).
"""
import logging
import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import httpx
from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.payment import ACTIVE_STATUSES, Payment, PaypalEvent
from app.utils import paypal_helpers as paypal
from app.utils.payment_checks import open_check, record_amount_cents, record_kind
from app.utils.payment_records import MODELS, get_record, today_et
from app.utils.processing_fee import record_processing_fee_cents

log = logging.getLogger(__name__)

# Outcomes of settle()
PAID = "paid"
PENDING = "pending"            # PayPal is holding the payment (e.g. an eCheck)
PROCESSING = "processing"      # unknown: ask again later, don't let them pay twice
NOT_APPROVED = "not_approved"
DECLINED = "declined"
NOT_CHARGED = "not_charged"    # we refused to capture
REVIEW = "review"              # charged, but something doesn't match


def active_payment(db: Session, rec) -> Payment | None:
    """A PayPal payment that may already have taken money, so don't take another."""
    return db.query(Payment).filter(
        Payment.kind == record_kind(rec), Payment.record_id == rec.id,
        or_(
            Payment.status.in_(ACTIVE_STATUSES),
            and_(Payment.status == "review", Payment.needs_review.is_(True)),
        ),
    ).first()


def start_payment(db: Session, rec, description: str) -> str:
    """Create an order and return the PayPal URL. Raises httpx.HTTPError."""
    entry = record_amount_cents(rec)
    fee = record_processing_fee_cents(db, rec)
    payment = Payment(
        kind=record_kind(rec), record_id=rec.id, method="paypal", status="created",
        public_id=secrets.token_urlsafe(24),
        entry_cents=entry, processing_fee_cents=fee, total_cents=entry + fee,
    )
    db.add(payment)
    db.commit()
    base = settings.BASE_URL.rstrip("/")
    query = urlencode({"t": rec.access_token or ""})
    try:
        order_id, url = paypal.create_order(
            total_cents=payment.total_cents,
            custom_id=payment.public_id,
            description=description,
            # PayPal adds ?token=<order id>; the record is found from that, not from us.
            return_url=f"{base}/payments/return?{query}",
            cancel_url=f"{base}/payments/cancel?{query}",
        )
    except httpx.HTTPError:
        _void(db, payment, "PayPal order could not be created")
        raise
    payment.paypal_order_id = order_id
    db.commit()
    return url


def settle(db: Session, payment: Payment) -> tuple[str, bool]:
    """Capture an approved order and record the result. Safe to call repeatedly.

    Returns (outcome, newly_paid).
    """
    if payment.paid_at is not None:
        return (PAID if payment.status == "completed" else NOT_CHARGED), False
    if payment.status == "review":
        return REVIEW, False
    if payment.status not in ("created", *ACTIVE_STATUSES) or not payment.paypal_order_id:
        return NOT_CHARGED, False
    try:
        order = paypal.get_order(payment.paypal_order_id)
        if order.get("status") == "APPROVED":
            refusal = _refusal(db, payment, order)
            # APPROVED means not charged yet, even if an earlier attempt was interrupted.
            if refusal:
                _void(db, payment, refusal)
                return NOT_CHARGED, False
            if payment.status == "created" and not _claim(db, payment):
                return PROCESSING, False
            try:
                paypal.capture_order(payment.paypal_order_id, payment.public_id)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500 or exc.response.status_code in (401, 403, 408, 409, 429):
                    raise
                log.warning("Capture refused for payment %s: %s", payment.id, exc.response.text[:500])
            order = paypal.get_order(payment.paypal_order_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            _void(db, payment, "PayPal order expired")
            return NOT_CHARGED, False
        log.exception("PayPal error settling payment %s", payment.id)
        return PROCESSING, False
    except httpx.HTTPError:
        log.exception("PayPal unreachable settling payment %s", payment.id)
        return PROCESSING, False

    unit = (order.get("purchase_units") or [{}])[0]
    captures = (unit.get("payments") or {}).get("captures") or []
    if captures:
        # Newest wins: a declined capture can be followed by a good one.
        good = [c for c in captures if c.get("status") not in ("DECLINED", "FAILED")]
        return apply_capture(db, payment, (good or captures)[-1], unit.get("custom_id"))
    if order.get("status") == "APPROVED":
        _void(db, payment, "PayPal declined the payment")
        return DECLINED, False
    if payment.status == "capturing":
        payment.status = "created"
        db.commit()
    return NOT_APPROVED, False


def _refusal(db: Session, payment: Payment, order: dict) -> str | None:
    """Why an approved order must not be captured."""
    unit = (order.get("purchase_units") or [{}])[0]
    if unit.get("custom_id") != payment.public_id or not _amount_matches(payment, unit.get("amount")):
        log.error("PayPal order %s does not match payment %s", payment.paypal_order_id, payment.id)
        return "PayPal order did not match our record"
    rec = get_record(db, payment.kind, payment.record_id)
    if rec is None:
        return "Entry no longer exists"
    db.refresh(rec)
    if rec.is_paid:
        return "Already paid another way"
    if record_amount_cents(rec) != payment.entry_cents:
        return "Price changed before payment"
    return None


def _claim(db: Session, payment: Payment) -> bool:
    """Become the one request allowed to capture for this record."""
    try:
        won = db.execute(
            update(Payment).where(Payment.id == payment.id, Payment.status == "created")
            .values(status="capturing").execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
    except IntegrityError:  # another order for the same record is being captured
        db.rollback()
        won = 0
    db.refresh(payment)
    return bool(won)


def _amount_matches(payment: Payment, amount: dict | None) -> bool:
    try:
        cents = Decimal((amount or {}).get("value", "")) * 100
    except InvalidOperation:
        return False
    return amount.get("currency_code") == "USD" and cents == payment.total_cents


def apply_capture(
    db: Session, payment: Payment, capture: dict, custom_id: str | None = None
) -> tuple[str, bool]:
    """Record a capture as reported by PayPal's API. Returns (outcome, newly_paid)."""
    if payment.paid_at is not None:
        return PAID, False
    status = capture.get("status")
    if status in ("DECLINED", "FAILED"):
        payment.status = "denied"
        payment.note = "PayPal declined the payment"
        db.commit()
        return DECLINED, False
    if (capture.get("custom_id") or custom_id) != payment.public_id or not _amount_matches(
        payment, capture.get("amount")
    ):
        payment.status, payment.needs_review = "review", True
        payment.paypal_capture_id = capture.get("id")
        payment.note = "PayPal charged something that does not match this entry"
        db.commit()
        log.error("Capture %s does not match payment %s", capture.get("id"), payment.id)
        return REVIEW, False
    if status == "PENDING":
        payment.paypal_capture_id = capture["id"]
        payment.status = "pending"
        try:
            db.commit()
        except IntegrityError:
            # Another payment for this record is already in flight.
            db.rollback()
            payment.status, payment.needs_review = "review", True
            payment.note = "Second PayPal payment pending for this entry"
            payment.paypal_capture_id = capture["id"]
            db.commit()
            return REVIEW, False
        return PENDING, False
    if status not in ("COMPLETED", "PARTIALLY_REFUNDED", "REFUNDED"):
        log.error("Unknown capture status %r for payment %s", status, payment.id)
        return PROCESSING, False

    # The return page and the webhook can both get here; only one may win.
    now = datetime.now(UTC)
    won = db.execute(
        update(Payment).where(Payment.id == payment.id, Payment.paid_at.is_(None)).values(
            status="completed", paid_at=now, received_cents=payment.total_cents,
            received_on=today_et(), paypal_capture_id=capture["id"], reference=capture["id"],
            voided_at=None, note=None,
        ).execution_options(synchronize_session=False)
    ).rowcount
    if not won:
        db.rollback()
        db.refresh(payment)
        return PAID, False
    newly_paid = False
    rec = get_record(db, payment.kind, payment.record_id)
    if rec is not None:
        model = MODELS[payment.kind]
        newly_paid = bool(db.execute(
            update(model).where(model.id == rec.id, model.is_paid.is_(False))
            .values(is_paid=True, payment_reference=f"PayPal: {capture['id']}")
            .execution_options(synchronize_session=False)
        ).rowcount)
        check = open_check(db, rec)
        if check:
            check.status, check.voided_at = "void", now
            check.note = "Replaced by paypal payment"
    db.commit()
    db.refresh(payment)
    if rec is not None:
        db.refresh(rec)
    if rec is None:
        _flag(db, payment, "Paid, but the entry was deleted")
    elif not newly_paid:
        _flag(db, payment, "Possible double payment: the entry was already paid")
    if status != "COMPLETED":
        _flag(db, payment, "Refunded in PayPal")
        if status == "REFUNDED":
            payment.status = "refunded"
            db.commit()
    return PAID, newly_paid


def _flag(db: Session, payment: Payment, note: str) -> None:
    payment.needs_review = True
    payment.note = note
    db.commit()


def _void(db: Session, payment: Payment, note: str) -> None:
    payment.status, payment.voided_at, payment.note = "void", datetime.now(UTC), note
    db.commit()


# ---------------------------------------------------------------------------
# Webhook events (already verified as genuine)
# ---------------------------------------------------------------------------

def handle_event(db: Session, event: dict) -> tuple[Payment | None, bool]:
    """Process one event once. Returns (payment, newly_paid). Raises httpx.HTTPError to
    make PayPal retry."""
    event_id, kind = event.get("id"), event.get("event_type", "")
    resource = event.get("resource") or {}
    if not event_id or db.get(PaypalEvent, event_id):
        return None, False

    payment, newly_paid = None, False
    if kind == "CHECKOUT.ORDER.APPROVED":
        # The buyer approved but may have closed the tab, so nothing is charged yet.
        payment = _by_order(db, resource.get("id"))
        if payment:
            outcome, newly_paid = settle(db, payment)
            if outcome == PROCESSING:
                raise httpx.TransportError("Payment still processing; retry later")
    elif kind.startswith("PAYMENT.CAPTURE."):
        refund = kind in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED")
        capture_id = _capture_id(resource)
        payment = _by_capture(db, capture_id, resource)
        if payment and capture_id:
            # Events can arrive late or out of order: use the capture's state now.
            capture = paypal.get_capture(capture_id)
            _, newly_paid = apply_capture(db, payment, capture)
            if refund:
                # A refund made after we marked the entry unpaid is already dealt with.
                if payment.status == "completed" and capture.get("status") == "REFUNDED":
                    payment.status = "refunded"
                word = "reversed" if kind.endswith("REVERSED") else (
                    "partly refunded" if capture.get("status") == "PARTIALLY_REFUNDED" else "refunded")
                _flag(db, payment, f"Payment {word} in PayPal")

    db.add(PaypalEvent(event_id=event_id, event_type=kind[:100]))
    try:
        db.commit()
    except IntegrityError:  # the same event delivered twice at once
        db.rollback()
    return payment, newly_paid


def _by_order(db: Session, order_id: str | None) -> Payment | None:
    return db.query(Payment).filter_by(paypal_order_id=order_id).first() if order_id else None


def _by_capture(db: Session, capture_id: str | None, resource: dict) -> Payment | None:
    if capture_id:
        payment = db.query(Payment).filter_by(paypal_capture_id=capture_id).first()
        if payment:
            return payment
    custom_id = resource.get("custom_id")
    return db.query(Payment).filter_by(public_id=custom_id).first() if custom_id else None


def _capture_id(resource: dict) -> str | None:
    """A refund's own id is not the capture's; its 'up' link points to the capture."""
    for link in resource.get("links") or []:
        if link.get("rel") == "up" and "/captures/" in link.get("href", ""):
            return link["href"].rstrip("/").rsplit("/", 1)[-1]
    return resource.get("id")
