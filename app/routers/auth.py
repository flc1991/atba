from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import base_context, get_db, require_login
from app.limiter import limiter
from app.models.user import User
from app.utils.auth import hash_password, verify_password
from app.utils.countries import get_country_choices
from app.utils.flash import flash

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, ctx: dict = Depends(base_context)):
    if ctx["current_user"]:
        return RedirectResponse("/", status_code=302)
    ctx.update({"form_data": {}, "next": request.query_params.get("next", ""), "csrf_token": _csrf(request)})
    return templates.TemplateResponse(request, "auth/login.html", ctx)


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
    ctx: dict = Depends(base_context),
):
    user = db.query(User).filter_by(email=email.strip().lower()).first()
    if user and user.is_active and verify_password(password, user.password_hash):
        request.session["user_id"] = user.id
        flash(request, f"Welcome back, {user.name.split()[0]}!", "success")
        return RedirectResponse(next or "/", status_code=302)

    flash(request, "Invalid email or password.", "error")
    ctx.update({"form_data": {"email": email}, "next": next, "csrf_token": _csrf(request)})
    return templates.TemplateResponse(request, "auth/login.html", ctx, status_code=401)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, ctx: dict = Depends(base_context)):
    if ctx["current_user"]:
        return RedirectResponse("/", status_code=302)
    ctx.update({"form_data": {}, "countries": get_country_choices(), "csrf_token": _csrf(request)})
    return templates.TemplateResponse(request, "auth/register.html", ctx)


@router.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    address_line1: str = Form(""),
    address_line2: str = Form(""),
    city: str = Form(""),
    state_province: str = Form(""),
    postal_code: str = Form(""),
    country: str = Form("US"),
    phone: str = Form(""),
    db: Session = Depends(get_db),
    ctx: dict = Depends(base_context),
):
    form_data = {
        "name": name, "email": email, "address_line1": address_line1,
        "address_line2": address_line2, "city": city, "state_province": state_province,
        "postal_code": postal_code, "country": country, "phone": phone,
    }

    errors = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password != password2:
        errors.append("Passwords do not match.")
    if db.query(User).filter_by(email=email.strip().lower()).first():
        errors.append("An account with that email already exists.")

    if errors:
        for e in errors:
            flash(request, e, "error")
        ctx.update({"form_data": form_data, "countries": get_country_choices(), "csrf_token": _csrf(request)})
        return templates.TemplateResponse(request, "auth/register.html", ctx, status_code=422)

    user = User(
        email=email.strip().lower(),
        password_hash=hash_password(password),
        name=name.strip(),
        address_line1=address_line1.strip(),
        address_line2=address_line2.strip() or None,
        city=city.strip(),
        state_province=state_province.strip() or None,
        postal_code=postal_code.strip(),
        country=country,
        phone=phone.strip() or None,
    )
    db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    flash(request, "Account created! You are now logged in.", "success")
    return RedirectResponse("/", status_code=302)


# ---------------------------------------------------------------------------
# Account / profile
# ---------------------------------------------------------------------------


@router.get("/account", response_class=HTMLResponse)
def account_page(
    request: Request,
    ctx: dict = Depends(base_context),
    current_user=Depends(require_login),
):
    ctx.update({"user": current_user, "countries": get_country_choices(), "csrf_token": _csrf(request)})
    return templates.TemplateResponse(request, "auth/account.html", ctx)


@router.post("/account", response_class=HTMLResponse)
def account_submit(
    request: Request,
    name: str = Form(...),
    new_password: str = Form(""),
    new_password2: str = Form(""),
    address_line1: str = Form(""),
    address_line2: str = Form(""),
    city: str = Form(""),
    state_province: str = Form(""),
    postal_code: str = Form(""),
    country: str = Form("US"),
    phone: str = Form(""),
    db: Session = Depends(get_db),
    ctx: dict = Depends(base_context),
    current_user=Depends(require_login),
):
    errors = []
    if new_password:
        if len(new_password) < 8:
            errors.append("New password must be at least 8 characters.")
        if new_password != new_password2:
            errors.append("Passwords do not match.")

    if errors:
        for e in errors:
            flash(request, e, "error")
        ctx.update({"user": current_user, "countries": get_country_choices(), "csrf_token": _csrf(request)})
        return templates.TemplateResponse(request, "auth/account.html", ctx, status_code=422)

    current_user.name = name.strip()
    current_user.address_line1 = address_line1.strip()
    current_user.address_line2 = address_line2.strip() or None
    current_user.city = city.strip()
    current_user.state_province = state_province.strip() or None
    current_user.postal_code = postal_code.strip()
    current_user.country = country
    current_user.phone = phone.strip() or None
    if new_password:
        current_user.password_hash = hash_password(new_password)

    db.commit()
    flash(request, "Account updated.", "success")
    return RedirectResponse("/auth/account", status_code=302)


# ---------------------------------------------------------------------------
# My Entries
# ---------------------------------------------------------------------------


@router.get("/entries", response_class=HTMLResponse)
def entries_page(
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
    current_user=Depends(require_login),
):
    from app.models.trial import TrialEntry, TrialEntrySelection, TrialEvent, TrialEventClass, Trial
    from app.models.event import Event
    from app.models.registration import Registration

    # Fetch trial entries with related data
    trial_entries_raw = (
        db.query(TrialEntry)
        .filter_by(user_id=current_user.id)
        .order_by(TrialEntry.id.desc())
        .all()
    )

    trial_entries = []
    for entry in trial_entries_raw:
        event = db.get(Event, entry.event_id)
        sels = (
            db.query(TrialEntrySelection, TrialEvent, TrialEventClass, Trial)
            .join(TrialEvent, TrialEntrySelection.trial_event_id == TrialEvent.id)
            .join(TrialEventClass, TrialEntrySelection.trial_event_class_id == TrialEventClass.id)
            .join(Trial, TrialEvent.trial_id == Trial.id)
            .filter(TrialEntrySelection.trial_entry_id == entry.id)
            .all()
        )
        sel_list = [
            {
                "trial_event_name": te.name,
                "class_name": tec.name,
                "governing_body": trial.governing_body,
                "call_number": sel.call_number,
            }
            for sel, te, tec, trial in sels
        ]
        trial_entries.append((entry, event, sel_list))

    # Fetch fun run / smart dog day registrations
    registrations_raw = (
        db.query(Registration)
        .filter_by(user_id=current_user.id)
        .order_by(Registration.id.desc())
        .all()
    )
    registrations = [(reg, db.get(Event, reg.event_id)) for reg in registrations_raw]

    ctx.update({"trial_entries": trial_entries, "registrations": registrations})
    return templates.TemplateResponse(request, "auth/entries.html", ctx)


# ---------------------------------------------------------------------------
# CSRF helper (placeholder — full CSRF integrated in Phase 11b)
# ---------------------------------------------------------------------------

def _csrf(request: Request) -> str:
    """Return a CSRF token stored in the session (simple version; replaced by fastapi-csrf-protect in Phase 11b)."""
    import secrets
    token = request.session.get("_csrf")
    if not token:
        token = secrets.token_hex(24)
        request.session["_csrf"] = token
    return token
