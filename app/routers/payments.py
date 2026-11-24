"""Payment routes: PayPal Orders v2 (server-side redirect) and checks.

1. An entry form saves an unpaid record and redirects to ``/payments/checkout``.
2. The entrant picks a check, or PayPal: ``/payments/paypal`` creates an order for the
   entry plus processing fee and redirects to PayPal.
3. PayPal sends them back to ``/payments/return``, where the server captures the order
   and marks the record paid only if PayPal's API says so.
4. ``/payments/webhook`` does the same for people who never come back, and flags refunds.
"""
import json
import logging
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies import base_context, get_current_user, get_db
from app.models.event import Event
from app.models.payment import Payment
from app.models.registration import Registration
from app.routers.registrations import _csrf, _validate_csrf
from app.utils import paypal_helpers, paypal_payments
from app.utils.email_helpers import queue_email
from app.utils.flash import flash
from app.utils.payment_checks import (
    can_access,
    check_deadline,
    checkout_url,
    confirmation_url,
    not_payable_reason,
    open_check,
    record_amount_cents,
    refresh_price_tier,
)
from app.utils.payment_records import (
    check_info,
    checks_enabled,
    get_record,
    record_payment,
    request_check,
)
from app.utils.processing_fee import record_processing_fee_cents

log = logging.getLogger(__name__)
router = APIRouter(tags=["payments"])
templates = Jinja2Templates(directory="app/templates")

_MAX_WEBHOOK_BYTES = 256 * 1024

_OUTCOME_MESSAGES = {
    paypal_payments.PAID: ("Payment received. Thank you!", "success"),
    paypal_payments.PENDING: ("PayPal is still processing your payment. Please don't pay again.", "info"),
    paypal_payments.PROCESSING: ("We're confirming your payment with PayPal. Please don't pay again.", "info"),
    paypal_payments.NOT_APPROVED: ("The payment wasn't completed at PayPal. You have not been charged.", "info"),
    paypal_payments.DECLINED: ("PayPal declined the payment. Please try again or choose another way to pay.", "error"),
    paypal_payments.NOT_CHARGED: ("That PayPal payment was not taken. Please check your entry below.", "info"),
    paypal_payments.REVIEW: ("There is a problem with your payment. Please contact the trial secretary and don't pay again.", "error"),
}


def _load_record(kind: str, record_id: int | None, token: str, user, db: Session):
    """Load a payable record. 404s unless the visitor may see it, so IDs can't be probed."""
    rec = get_record(db, kind, record_id)
    if rec is None or not can_access(rec, user, token):
        raise HTTPException(status_code=404, detail="Record not found")
    return rec


def _redirect_to_confirmation(rec) -> RedirectResponse:
    return RedirectResponse(confirmation_url(rec), status_code=303)


def _settle(request: Request, background_tasks: BackgroundTasks, db: Session, payment: Payment, rec) -> str:
    """Settle with PayPal, tell the visitor, and email if this marked the record paid."""
    outcome, newly_paid = paypal_payments.settle(db, payment)
    if newly_paid:
        queue_email(background_tasks, rec)
    flash(request, *_OUTCOME_MESSAGES[outcome])
    return outcome


def _finish_active(request, background_tasks, db: Session, rec) -> bool:
    """Resolve a payment already under way. True if the record can't be paid again now."""
    payment = paypal_payments.active_payment(db, rec)
    if payment is None:
        return False
    outcome = _settle(request, background_tasks, db, payment, rec)
    db.refresh(rec)
    return rec.is_paid or outcome in (
        paypal_payments.PENDING, paypal_payments.PROCESSING, paypal_payments.REVIEW
    )


def _can_pay_by_check(rec, event, db: Session) -> bool:
    if not checks_enabled() or record_amount_cents(rec) <= 0:
        return False
    deadline = check_deadline(rec, event, db)
    return deadline is not None and datetime.now(UTC) < deadline


@router.get("/checkout/{kind}/{record_id}", response_class=HTMLResponse)
def checkout(
    kind: str,
    record_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    t: str = "",
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    """Let the entrant choose PayPal or a check."""
    rec = _load_record(kind, record_id, t, ctx.get("current_user"), db)
    if rec.is_paid or _finish_active(request, background_tasks, db, rec):
        return _redirect_to_confirmation(rec)

    event = db.get(Event, rec.event_id)
    reason = not_payable_reason(rec, event, db)
    if reason:
        flash(request, reason, "error")
        return _redirect_to_confirmation(rec)

    price_changed = refresh_price_tier(rec, event, db)
    if price_changed:
        db.commit()
    amount_cents = record_amount_cents(rec)

    # Deliberately free (a fee of exactly 0); blank fees are refused before this point.
    if amount_cents <= 0:
        record_payment(db, rec, "free", received_cents=0)
        db.commit()
        queue_email(background_tasks, rec)
        return _redirect_to_confirmation(rec)

    payment = open_check(db, rec)
    can_check = payment is None and _can_pay_by_check(rec, event, db)
    if not settings.paypal_enabled and not can_check:
        flash(
            request,
            "Online payment is not available right now. Please contact the trial secretary.",
            "error",
        )
        return _redirect_to_confirmation(rec)

    fee_cents = record_processing_fee_cents(db, rec)
    ctx.update({
        "paypal_enabled": settings.paypal_enabled,
        "amount_cents": amount_cents,
        "processing_fee_cents": fee_cents,
        "total_cents": amount_cents + fee_cents,
        "item_name": f"{event.title} — {kind.title()} #{rec.id}",
        "confirmation_url": confirmation_url(rec),
        "price_changed": price_changed,
        "can_check": can_check,
        "open_check": payment,
        "check": check_info(db, rec, event, payment),
        "token": t,
        "csrf_token": _csrf(request),
        "event": event,
        "record": rec,
        "kind": kind,
    })
    return templates.TemplateResponse(request, "payments/checkout.html", ctx)


def _ready_to_pay(request, background_tasks, rec, event, db: Session) -> RedirectResponse | None:
    """Checks shared by both ways of paying. A redirect means stop."""
    if rec.is_paid or _finish_active(request, background_tasks, db, rec):
        return _redirect_to_confirmation(rec)
    reason = not_payable_reason(rec, event, db)
    if reason is None and record_amount_cents(rec) <= 0:
        return RedirectResponse(checkout_url(rec), status_code=303)
    if reason:
        flash(request, reason, "error")
        return _redirect_to_confirmation(rec)
    if refresh_price_tier(rec, event, db):
        db.commit()
        flash(request, "Pre-entry pricing has ended. Please check the new amount.", "info")
        return RedirectResponse(checkout_url(rec), status_code=303)
    return None


@router.post("/paypal/{kind}/{record_id}")
def pay_by_paypal(
    kind: str,
    record_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(""),
    t: str = Form(""),
    total: int = Form(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a PayPal order and send the entrant to approve it."""
    _validate_csrf(csrf_token, request)
    rec = _load_record(kind, record_id, t, current_user, db)
    event = db.get(Event, rec.event_id)
    if stop := _ready_to_pay(request, background_tasks, rec, event, db):
        return stop
    if not settings.paypal_enabled:
        flash(request, "PayPal is not available right now.", "error")
        return RedirectResponse(checkout_url(rec), status_code=303)
    # `total` is only what they were shown, so nobody pays an amount they didn't see.
    if total != record_amount_cents(rec) + record_processing_fee_cents(db, rec):
        flash(request, "The amount has changed. Please check it and try again.", "info")
        return RedirectResponse(checkout_url(rec), status_code=303)
    try:
        url = paypal_payments.start_payment(db, rec, f"{event.title} - {kind.title()} #{rec.id}")
    except httpx.HTTPError:
        log.exception("Could not create a PayPal order for %s %s", kind, record_id)
        flash(request, "We couldn't reach PayPal. You have not been charged; please try again.", "error")
        return RedirectResponse(checkout_url(rec), status_code=303)
    return RedirectResponse(url, status_code=303)


@router.post("/check/{kind}/{record_id}")
def pay_by_check(
    kind: str,
    record_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(""),
    t: str = Form(""),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Finish the entry as 'awaiting check'. It stays unpaid until the check arrives."""
    _validate_csrf(csrf_token, request)
    rec = _load_record(kind, record_id, t, current_user, db)
    if open_check(db, rec):
        return _redirect_to_confirmation(rec)
    event = db.get(Event, rec.event_id)
    if stop := _ready_to_pay(request, background_tasks, rec, event, db):
        return stop
    if not _can_pay_by_check(rec, event, db):
        flash(request, "It is too late to pay for this entry by check.", "error")
        return _redirect_to_confirmation(rec)
    request_check(db, rec)
    try:
        db.commit()
    except IntegrityError:  # double-click: the other request already recorded it
        db.rollback()
        return _redirect_to_confirmation(rec)
    queue_email(background_tasks, rec)
    return _redirect_to_confirmation(rec)


def _payment_from_order(order_id: str, t: str, user, db: Session):
    payment = db.query(Payment).filter_by(paypal_order_id=order_id).first() if order_id else None
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment, _load_record(payment.kind, payment.record_id, t, user, db)


@router.get("/return", response_class=HTMLResponse, name="payment_return")
def payment_return(
    request: Request,
    background_tasks: BackgroundTasks,
    token: str = "",
    t: str = "",
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Back from PayPal. `token` is PayPal's order id, matched to the order we saved."""
    payment, rec = _payment_from_order(token, t, current_user, db)
    _settle(request, background_tasks, db, payment, rec)
    return _redirect_to_confirmation(rec)


@router.get("/cancel", response_class=HTMLResponse, name="payment_cancel")
def payment_cancel(
    request: Request,
    token: str = "",
    t: str = "",
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancelled at PayPal — leave the record unpaid and show how to pay later."""
    _, rec = _payment_from_order(token, t, current_user, db)
    if not rec.is_paid:
        flash(request, "Payment was cancelled. Your entry is saved but not yet paid.", "info")
    return _redirect_to_confirmation(rec)


@router.post("/webhook")
async def webhook(
    request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    """PayPal notifications. Anything but a 2xx makes PayPal retry."""
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > _MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413)
    if not settings.PAYPAL_WEBHOOK_ID or not settings.paypal_enabled:
        raise HTTPException(status_code=503, detail="Webhook not configured")
    try:
        text = body.decode("utf-8")
        event = json.loads(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Invalid event")
    try:
        if not await run_in_threadpool(paypal_helpers.verify_webhook, request.headers, text):
            raise HTTPException(status_code=400, detail="Invalid signature")
        payment, newly_paid = await run_in_threadpool(paypal_payments.handle_event, db, event)
    except httpx.HTTPError as exc:
        log.warning("Webhook %s will be retried: %s", event.get("id"), exc)
        raise HTTPException(status_code=503, detail="Try again later") from exc
    if newly_paid:
        rec = get_record(db, payment.kind, payment.record_id)
        queue_email(background_tasks, rec)
    return Response(status_code=200)
