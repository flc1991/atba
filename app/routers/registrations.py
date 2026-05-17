from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies import base_context, get_db
from app.models.event import Event
from app.models.member import Member
from app.models.registration import FUN_RUN_EVENTS, Registration, RegistrationDog
from app.utils.countries import get_country_choices
from app.utils.flash import flash
from app.utils.registration_windows import resolve_pricing_tier

router = APIRouter(tags=["registrations"])
templates = Jinja2Templates(directory="app/templates")


def _get_open_event(event_id: int, expected_type: str, db: Session) -> Event:
    """Fetch a published event of the expected type or raise 404."""
    event = (
        db.query(Event)
        .filter_by(id=event_id, event_type=expected_type, is_deleted=False)
        .filter(Event.is_published.is_(True))
        .first()
    )
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _is_current_member(user, is_member_claim: bool, db: Session) -> bool:
    """Return True if the user is verified as a current member."""
    current_year = date.today().year
    if user:
        # Check if the logged-in user has a member record for this year
        member = (
            db.query(Member)
            .filter(Member.user_id == user.id, Member.membership_year >= current_year)
            .first()
        )
        return member is not None
    # Guest claiming membership — grant pre_member rate; admin verifies later
    return is_member_claim


def _csrf(request: Request) -> str:
    """Return (and set if missing) a simple session CSRF token."""
    token = request.session.get("csrf_token")
    if not token:
        import secrets
        token = secrets.token_hex(32)
        request.session["csrf_token"] = token
    return token


def _validate_csrf(request_token: str, request: Request) -> None:
    """Raise 403 if the submitted token doesn't match the session token."""
    session_token = request.session.get("csrf_token", "")
    if not session_token or request_token != session_token:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _reg_context(
    request: Request,
    event: Event,
    ctx: dict,
    db: Session,
    form_data: dict | None = None,
) -> dict:
    """Build the template context for a registration GET."""
    current_user = ctx.get("current_user")
    is_member = _is_current_member(current_user, False, db) if current_user else False
    tier = resolve_pricing_tier(event.pre_entry_close_dt, is_member)

    fee_map = {
        "pre_member": event.fee_pre_member_cents,
        "pre_general": event.fee_pre_general_cents,
        "late": event.fee_late_cents,
    }
    fee_cents = fee_map.get(tier) or event.fee_late_cents or 0

    # Auto-fill from logged-in user
    if form_data is None and current_user:
        form_data = {
            "name": current_user.name or "",
            "email": current_user.email or "",
            "phone": current_user.phone or "",
            "address_line1": current_user.address_line1 or "",
            "address_line2": current_user.address_line2 or "",
            "city": current_user.city or "",
            "state_province": current_user.state_province or "",
            "postal_code": current_user.postal_code or "",
            "country": current_user.country or "US",
        }

    ctx.update({
        "event": event,
        "fee_cents": fee_cents,
        "pricing_tier": tier,
        "form_data": form_data or {},
        "countries": get_country_choices(),
        "csrf_token": _csrf(request),
        "paypal_client_id": settings.PAYPAL_CLIENT_ID,
    })
    return ctx


# ---------------------------------------------------------------------------
# Fun Run
# ---------------------------------------------------------------------------

@router.get("/{event_id}/register/fun-run", response_class=HTMLResponse)
def fun_run_get(
    event_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    event = _get_open_event(event_id, "fun_run", db)
    ctx = _reg_context(request, event, ctx, db)
    ctx["fun_run_events"] = FUN_RUN_EVENTS
    return templates.TemplateResponse(request, "registrations/fun_run.html", ctx)


@router.post("/{event_id}/register/fun-run", response_class=HTMLResponse)
async def fun_run_post(
    event_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    from app.utils.flash import get_flash_messages

    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)
    event = _get_open_event(event_id, "fun_run", db)
    current_user = ctx.get("current_user")

    # Contact fields
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip()
    phone = (form.get("phone") or "").strip()
    address_line1 = (form.get("address_line1") or "").strip()
    address_line2 = (form.get("address_line2") or "").strip()
    city = (form.get("city") or "").strip()
    state_province = (form.get("state_province") or "").strip()
    postal_code = (form.get("postal_code") or "").strip()
    country = (form.get("country") or "US").strip()
    is_member_claim = bool(form.get("is_member_claim"))

    # Parse dogs (dog_0_name, dog_0_breed, dog_0_event_1..4, dog_0_event_1_judged..4, dog_1_name, ...)
    dogs = []
    for i in range(10):
        dog_name_val = (form.get(f"dog_{i}_name") or "").strip()
        if not dog_name_val and i > 0:
            break
        dogs.append({
            "dog_name": dog_name_val,
            "dog_breed": (form.get(f"dog_{i}_breed") or "").strip() or None,
            "event_1": (form.get(f"dog_{i}_event_1") or "").strip() or None,
            "event_2": (form.get(f"dog_{i}_event_2") or "").strip() or None,
            "event_3": (form.get(f"dog_{i}_event_3") or "").strip() or None,
            "event_4": (form.get(f"dog_{i}_event_4") or "").strip() or None,
            "event_1_judged": bool(form.get(f"dog_{i}_event_1_judged")),
            "event_2_judged": bool(form.get(f"dog_{i}_event_2_judged")),
            "event_3_judged": bool(form.get(f"dog_{i}_event_3_judged")),
            "event_4_judged": bool(form.get(f"dog_{i}_event_4_judged")),
        })

    # Server-side pricing re-evaluation
    is_member = _is_current_member(current_user, is_member_claim, db)
    tier = resolve_pricing_tier(event.pre_entry_close_dt, is_member)
    per_event_fee = {
        "pre_member": event.fee_pre_member_cents,
        "pre_general": event.fee_pre_general_cents,
        "late": event.fee_late_cents,
    }.get(tier) or event.fee_late_cents or 0

    total_events = sum(
        sum(1 for e in [d["event_1"], d["event_2"], d["event_3"], d["event_4"]] if e)
        for d in dogs
    )
    fee_cents = total_events * per_event_fee

    errors = []
    if not name:
        errors.append("Full name is required.")
    if not email:
        errors.append("Email address is required.")
    if not dogs or not dogs[0]["dog_name"]:
        errors.append("At least one dog's name is required.")
    if total_events == 0:
        errors.append("Please select at least one event for at least one dog.")

    if errors:
        for msg in errors:
            flash(request, msg, "error")
        form_data = {
            "name": name, "email": email, "phone": phone,
            "address_line1": address_line1, "address_line2": address_line2,
            "city": city, "state_province": state_province,
            "postal_code": postal_code, "country": country,
            "is_member_claim": is_member_claim,
        }
        ctx = _reg_context(request, event, ctx, db, form_data=form_data)
        ctx["fun_run_events"] = FUN_RUN_EVENTS
        ctx["initial_dogs"] = dogs
        ctx["flash_messages"] = get_flash_messages(request)
        return templates.TemplateResponse(
            request, "registrations/fun_run.html", ctx, status_code=422
        )

    reg = Registration(
        event_id=event.id,
        user_id=current_user.id if current_user else None,
        name=name,
        email=email,
        phone=phone or None,
        address_line1=address_line1,
        address_line2=address_line2 or None,
        city=city,
        state_province=state_province or None,
        postal_code=postal_code,
        country=country if country and country != "---" else "US",
        dog_name=dogs[0]["dog_name"],
        pricing_tier=tier,
        fee_cents=fee_cents,
        paypal_order_id=None,
        is_paid=False,
    )
    db.add(reg)
    db.flush()

    for d in dogs:
        if d["dog_name"]:
            db.add(RegistrationDog(
                registration_id=reg.id,
                dog_name=d["dog_name"],
                dog_breed=d["dog_breed"],
                event_1=d["event_1"],
                event_2=d["event_2"],
                event_3=d["event_3"],
                event_4=d["event_4"],
                event_1_judged=d["event_1_judged"] and bool(d["event_1"]),
                event_2_judged=d["event_2_judged"] and bool(d["event_2"]),
                event_3_judged=d["event_3_judged"] and bool(d["event_3"]),
                event_4_judged=d["event_4_judged"] and bool(d["event_4"]),
            ))

    db.commit()
    db.refresh(reg)

    return RedirectResponse(
        f"/payments/checkout/registration/{reg.id}",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Smart Dog Day
# ---------------------------------------------------------------------------

@router.get("/{event_id}/register/smart-dog-day", response_class=HTMLResponse)
def smart_dog_day_get(
    event_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    event = _get_open_event(event_id, "smart_dog_day", db)
    return templates.TemplateResponse(
        request, "registrations/smart_dog_day.html", _reg_context(request, event, ctx, db)
    )


@router.post("/{event_id}/register/smart-dog-day", response_class=HTMLResponse)
def smart_dog_day_post(
    event_id: int,
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    address_line1: str = Form(""),
    address_line2: str = Form(""),
    city: str = Form(""),
    state_province: str = Form(""),
    postal_code: str = Form(""),
    country: str = Form("US"),
    dog_name: str = Form(...),
    is_member_claim: str = Form(""),
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    _validate_csrf(csrf_token, request)
    event = _get_open_event(event_id, "smart_dog_day", db)
    return _process_registration(
        request, event, ctx, db,
        name=name, email=email, phone=phone,
        address_line1=address_line1, address_line2=address_line2,
        city=city, state_province=state_province,
        postal_code=postal_code, country=country,
        dog_name=dog_name,
        is_member_claim=bool(is_member_claim),
    )


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------

@router.get("/{event_id}/register/confirmation", response_class=HTMLResponse)
def registration_confirmation(
    event_id: int,
    registration_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter_by(id=event_id, is_deleted=False).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    reg = db.query(Registration).filter_by(id=registration_id, event_id=event_id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    reg_dogs = db.query(RegistrationDog).filter_by(registration_id=reg.id).all()
    ctx.update({"event": event, "registration": reg, "registration_dogs": reg_dogs})
    return templates.TemplateResponse(request, "registrations/confirmation.html", ctx)


# ---------------------------------------------------------------------------
# Shared processing logic
# ---------------------------------------------------------------------------

def _process_registration(
    request: Request,
    event: Event,
    ctx: dict,
    db: Session,
    *,
    name: str,
    email: str,
    phone: str,
    address_line1: str,
    address_line2: str,
    city: str,
    state_province: str,
    postal_code: str,
    country: str,
    dog_name: str,
    is_member_claim: bool,
):
    """Validate, re-evaluate pricing server-side, save a pending Registration, and
    redirect to the PayPal checkout page."""
    current_user = ctx.get("current_user")

    # Server-side pricing re-evaluation — never trust client-supplied amount
    is_member = _is_current_member(current_user, is_member_claim, db)
    tier = resolve_pricing_tier(event.pre_entry_close_dt, is_member)

    fee_map = {
        "pre_member": event.fee_pre_member_cents,
        "pre_general": event.fee_pre_general_cents,
        "late": event.fee_late_cents,
    }
    fee_cents = fee_map.get(tier) or event.fee_late_cents or 0

    # Basic field validation
    errors = []
    if not name.strip():
        errors.append("Full name is required.")
    if not email.strip():
        errors.append("Email address is required.")
    if not dog_name.strip():
        errors.append("Dog's name is required.")

    if errors:
        for msg in errors:
            flash(request, msg, "error")
        form_data = {
            "name": name, "email": email, "phone": phone,
            "address_line1": address_line1, "address_line2": address_line2,
            "city": city, "state_province": state_province,
            "postal_code": postal_code, "country": country,
            "dog_name": dog_name, "is_member_claim": is_member_claim,
        }
        template = (
            "registrations/fun_run.html"
            if event.event_type == "fun_run"
            else "registrations/smart_dog_day.html"
        )
        return templates.TemplateResponse(
            request,
            template,
            _reg_context(request, event, ctx, db, form_data=form_data),
            status_code=422,
        )

    reg = Registration(
        event_id=event.id,
        user_id=current_user.id if current_user else None,
        name=name.strip(),
        email=email.strip(),
        phone=phone.strip() or None,
        address_line1=address_line1.strip(),
        address_line2=address_line2.strip() or None,
        city=city.strip(),
        state_province=state_province.strip() or None,
        postal_code=postal_code.strip(),
        country=country if country and country != "---" else "US",
        dog_name=dog_name.strip(),
        pricing_tier=tier,
        fee_cents=fee_cents,
        paypal_order_id=None,
        is_paid=False,
    )
    db.add(reg)
    db.commit()
    db.refresh(reg)

    return RedirectResponse(
        f"/payments/checkout/registration/{reg.id}",
        status_code=303,
    )
