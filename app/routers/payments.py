"""PayPal payment routes.

Two flows are supported:

1. **Standard Payments (form-redirect)** — primary flow used by the registration and
   trial entry forms. The submit button on each form posts to its registration handler,
   which saves a pending record and then redirects the user to ``/payments/checkout``.
   That page renders an auto-submitting form that POSTs the user to PayPal's
   ``cgi-bin/webscr`` endpoint with a dynamic ``amount``. After paying, PayPal
   redirects the user back to ``/payments/return`` which marks the record as paid.

2. **Server REST API (JS SDK)** — legacy ``/create-order``/``/capture-order``
   endpoints kept for compatibility. Not currently used by the site.
"""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies import base_context, get_db
from app.models.event import Event
from app.models.registration import Registration
from app.models.trial import TrialEntry
from app.utils.flash import flash
from app.utils.paypal_helpers import (
    capture_paypal_order,
    create_paypal_order,
    get_access_token,
)

router = APIRouter(tags=["payments"])
templates = Jinja2Templates(directory="app/templates")

_MAX_CENTS = 50_000
_MIN_CENTS = 1


# ---------------------------------------------------------------------------
# Standard Payments — form-redirect flow
# ---------------------------------------------------------------------------

# kind → (model_class, confirmation_url_template)
_CHECKOUT_KINDS = {
    "registration": (Registration, "/events/{event_id}/register/confirmation?registration_id={record_id}"),
    "entry": (TrialEntry, "/events/{event_id}/trial/confirmation?entry_id={record_id}"),
}


def _load_pending(kind: str, record_id: int, db: Session):
    if kind not in _CHECKOUT_KINDS:
        raise HTTPException(status_code=404, detail="Unknown payment kind")
    model_cls, _ = _CHECKOUT_KINDS[kind]
    rec = db.get(model_cls, record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
    return rec


def _record_amount_cents(rec) -> int:
    return getattr(rec, "fee_cents", None) or getattr(rec, "total_fee_cents", 0) or 0


def _record_event_id(rec) -> int:
    return rec.event_id


@router.get("/checkout/{kind}/{record_id}", response_class=HTMLResponse)
def checkout(
    kind: str,
    record_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    """Render an auto-submitting form to PayPal Standard Payments."""
    rec = _load_pending(kind, record_id, db)
    if getattr(rec, "is_paid", False):
        # Already paid — go straight to the confirmation page.
        return _redirect_to_confirmation(kind, rec)

    event = db.get(Event, _record_event_id(rec))
    amount_cents = _record_amount_cents(rec)

    # Free entry — no payment needed; mark paid and confirm.
    if amount_cents <= 0:
        rec.is_paid = True
        db.commit()
        return _redirect_to_confirmation(kind, rec)

    if not settings.PAYPAL_BUSINESS_EMAIL:
        flash(
            request,
            "PayPal is not yet configured for this site. Please contact the trial secretary.",
            "error",
        )
        return RedirectResponse(f"/events/{event.id}", status_code=302)

    item_name = f"{event.title} — {kind.replace('_', ' ').title()} #{rec.id}"
    return_url = (
        str(request.url_for("payment_return"))
        + "?"
        + urlencode({"kind": kind, "record_id": rec.id})
    )
    cancel_url = (
        str(request.url_for("payment_cancel"))
        + "?"
        + urlencode({"kind": kind, "record_id": rec.id})
    )
    notify_url = ""  # IPN intentionally not configured

    ctx.update({
        "paypal_url": settings.paypal_checkout_url,
        "business_email": settings.PAYPAL_BUSINESS_EMAIL,
        "amount_dollars": f"{amount_cents / 100:.2f}",
        "item_name": item_name,
        "item_number": f"{kind}-{rec.id}",
        "return_url": return_url,
        "cancel_url": cancel_url,
        "notify_url": notify_url,
        "event": event,
        "record": rec,
        "kind": kind,
    })
    return templates.TemplateResponse(request, "payments/checkout.html", ctx)


@router.get("/return", response_class=HTMLResponse, name="payment_return")
def payment_return(
    request: Request,
    kind: str,
    record_id: int,
    db: Session = Depends(get_db),
):
    """PayPal redirects here after successful payment; mark as paid."""
    rec = _load_pending(kind, record_id, db)
    if not rec.is_paid:
        rec.is_paid = True
        # Record the PayPal transaction id if it was passed back (PDT/auto-return).
        tx = request.query_params.get("tx") or request.query_params.get("txn_id")
        if tx and not rec.paypal_order_id:
            rec.paypal_order_id = tx
        db.commit()
    flash(request, "Payment received — your registration is confirmed!", "success")
    return _redirect_to_confirmation(kind, rec)


@router.get("/cancel", response_class=HTMLResponse, name="payment_cancel")
def payment_cancel(
    request: Request,
    kind: str,
    record_id: int,
    db: Session = Depends(get_db),
):
    """User cancelled at PayPal — leave record pending and send them back to the event page."""
    rec = _load_pending(kind, record_id, db)
    flash(
        request,
        "Payment was cancelled. Your registration is still pending — you can pay later from your account.",
        "info",
    )
    return RedirectResponse(f"/events/{rec.event_id}", status_code=302)


def _redirect_to_confirmation(kind: str, rec) -> RedirectResponse:
    _, url_tmpl = _CHECKOUT_KINDS[kind]
    return RedirectResponse(
        url_tmpl.format(event_id=rec.event_id, record_id=rec.id),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Legacy JS SDK endpoints (kept; not currently wired to any form)
# ---------------------------------------------------------------------------


class CreateOrderRequest(BaseModel):
    event_id: int
    event_type: str  # 'registration' | 'entry'
    amount_cents: int


class CaptureOrderRequest(BaseModel):
    order_id: str


@router.post("/create-order")
async def create_order(body: CreateOrderRequest) -> JSONResponse:
    if not (_MIN_CENTS <= body.amount_cents <= _MAX_CENTS):
        raise HTTPException(status_code=422, detail="Invalid amount")

    try:
        token = await get_access_token()
        order_id = await create_paypal_order(token, body.amount_cents)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PayPal error: {exc}") from exc

    return JSONResponse({"order_id": order_id})


@router.post("/capture-order")
async def capture_order(body: CaptureOrderRequest) -> JSONResponse:
    if not body.order_id:
        raise HTTPException(status_code=422, detail="order_id is required")

    try:
        token = await get_access_token()
        result = await capture_paypal_order(token, body.order_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PayPal error: {exc}") from exc

    status = result.get("status", "UNKNOWN")
    return JSONResponse({"status": status, "order_id": body.order_id})
