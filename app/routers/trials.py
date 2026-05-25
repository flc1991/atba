import secrets
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies import base_context, get_db
from app.models.dog import Dog
from app.models.event import Event
from app.models.trial import Trial, TrialEntry, TrialEntrySelection, TrialEvent, TrialEventClass
from app.utils.countries import get_country_choices
from app.utils.account_linking import find_user_by_email
from app.utils.flash import flash
from app.utils.registration_windows import get_trial_status

router = APIRouter(tags=["trials"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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


def _load_event(event_id: int, db: Session) -> Event:
    event = (
        db.query(Event)
        .filter_by(id=event_id, event_type="trial", is_deleted=False)
        .filter(Event.is_published.is_(True))
        .first()
    )
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _load_trial(event_id: int, governing_body: str, db: Session) -> Trial:
    trial = db.query(Trial).filter_by(event_id=event_id, governing_body=governing_body).first()
    if not trial:
        raise HTTPException(status_code=404, detail=f"{governing_body} trial not found for this event")
    return trial


def _load_trial_events(trial_id: int, db: Session):
    """Return (trial_events, te_classes_map)."""
    tes = (
        db.query(TrialEvent)
        .filter_by(trial_id=trial_id)
        .order_by(TrialEvent.sort_order)
        .all()
    )
    te_classes_map: dict[int, list[TrialEventClass]] = {}
    for te in tes:
        te_classes_map[te.id] = (
            db.query(TrialEventClass)
            .filter_by(trial_event_id=te.id)
            .order_by(TrialEventClass.sort_order)
            .all()
        )
    return tes, te_classes_map


def _autofill(user) -> dict:
    if not user:
        return {}
    return {
        "handler_name": user.name or "",
        "handler_email": user.email or "",
        "handler_phone": user.phone or "",
        "address_line1": user.address_line1 or "",
        "address_line2": user.address_line2 or "",
        "city": user.city or "",
        "state_province": user.state_province or "",
        "postal_code": user.postal_code or "",
        "country": user.country or "US",
    }


def _get_saved_dogs(user, db: Session) -> list:
    if not user:
        return []
    return db.query(Dog).filter_by(user_id=user.id).order_by(Dog.dog_name).all()


def _parse_selection_rows(
    form,
    trial_events: list[TrialEvent],
    te_classes_map: dict[int, list[TrialEventClass]],
    has_e2: bool,
    day_choices: tuple[str, ...],
) -> list[dict]:
    """Parse per-side form data into a list of selection rows.

    Each entered (event, side) becomes one dict: {te_id, class_id, pref, day_preference}.
    `pref` is "event_1" or "event_2". For single-trial weekends (has_e2=False),
    only side "e1" is parsed; pref is still "event_1" for the unique constraint.
    `day_choices` restricts which day_pref values are accepted (e.g., ("friday",
    "saturday") for AKC, ("saturday", "sunday") for AHBA).
    """
    rows: list[dict] = []
    sides = [("e1", "event_1")]
    if has_e2:
        sides.append(("e2", "event_2"))

    for te in trial_events:
        te_id = te.id
        valid_class_ids = {cls.id for cls in te_classes_map.get(te_id, [])}
        for side, pref_value in sides:
            class_id: int | None = None
            entered = False
            if te.is_test_class:
                if form.get(f"enter_te_{side}_{te_id}"):
                    entered = True
            else:
                raw_cls = (form.get(f"class_{side}_{te_id}") or "").strip()
                if raw_cls:
                    try:
                        cid = int(raw_cls)
                    except ValueError:
                        continue
                    if cid in valid_class_ids:
                        class_id = cid
                        entered = True
            if not entered:
                continue

            # Day preference for this side
            avail = te.available_days
            if avail == "either" or avail is None:
                day = (form.get(f"day_pref_{side}_{te_id}") or "").strip()
                day_pref = day if day in day_choices else None
            elif avail in day_choices:
                day_pref = avail
            else:
                day_pref = None

            rows.append({
                "te_id": te_id,
                "class_id": class_id,
                "pref": pref_value,
                "day_preference": day_pref,
            })
    return rows


def _compute_fee_from_rows(rows: list[dict], trial_events: list[TrialEvent]) -> int:
    """Sum fee_cents across every selection row (one row = one entry = one fee)."""
    te_fee_map = {te.id: te.fee_cents for te in trial_events}
    return sum(te_fee_map.get(r["te_id"], 0) for r in rows)


# ---------------------------------------------------------------------------
# AKC entry — GET
# ---------------------------------------------------------------------------

@router.get("/{event_id}/trial/enter/akc", response_class=HTMLResponse)
def akc_entry_get(
    event_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    event = _load_event(event_id, db)
    trial = _load_trial(event_id, "AKC", db)
    status = get_trial_status(trial.reg_close_dt)
    if status != "open":
        if status == "not_yet_open":
            flash(request, "AKC trial registration for this event is not yet open.", "info")
        else:
            flash(request, "AKC trial registration is currently closed.", "info")
        return RedirectResponse(f"/events/{event_id}", status_code=302)

    trial_events, te_classes_map = _load_trial_events(trial.id, db)
    current_user = ctx.get("current_user")
    ctx.update({
        "event": event,
        "trial": trial,
        "trial_events": trial_events,
        "te_classes_map": te_classes_map,
        "form_data": _autofill(current_user),
        "saved_dogs": _get_saved_dogs(current_user, db),
        "countries": get_country_choices(),
        "csrf_token": _csrf(request),
        "paypal_client_id": settings.PAYPAL_CLIENT_ID,
    })
    return templates.TemplateResponse(request, "trials/akc_entry_form.html", ctx)


# ---------------------------------------------------------------------------
# AKC entry — POST
# ---------------------------------------------------------------------------

@router.post("/{event_id}/trial/enter/akc", response_class=HTMLResponse)
async def akc_entry_post(
    event_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)

    event = _load_event(event_id, db)
    trial = _load_trial(event_id, "AKC", db)
    status = get_trial_status(trial.reg_close_dt)
    if status != "open":
        msg = "AKC trial registration for this event is not yet open." \
            if status == "not_yet_open" else "AKC trial registration is now closed."
        flash(request, msg, "error")
        return RedirectResponse(f"/events/{event_id}", status_code=302)

    trial_events, te_classes_map = _load_trial_events(trial.id, db)
    current_user = ctx.get("current_user")

    # --- collect selections ---
    # One row per (trial_event, side) that the registrant entered. Each row carries
    # its own class and day preference so the two AKC trials can differ.
    has_e2 = trial.akc_event_number_2 is not None
    selection_rows = _parse_selection_rows(
        form, trial_events, te_classes_map, has_e2,
        day_choices=("friday", "saturday"),
    )

    # --- collect fields ---
    def fget(name: str, default: str = "") -> str:
        return form.get(name, default).strip()

    handler_name    = fget("handler_name")
    handler_email   = fget("handler_email")
    handler_phone   = fget("handler_phone") or None
    address_line1   = fget("address_line1")
    address_line2   = fget("address_line2") or None
    city            = fget("city")
    state_province  = fget("state_province") or None
    postal_code     = fget("postal_code")
    country_raw     = fget("country", "US")
    country         = country_raw if country_raw and country_raw != "---" else "US"

    dog_name          = fget("dog_name")
    dog_call_name     = fget("dog_call_name") or None
    akc_number_type   = fget("akc_number_type") or None
    dog_reg_number    = fget("dog_registration_number") or None
    akc_foreign_country = fget("akc_foreign_country") or None
    dog_breed         = fget("dog_breed") or None
    dog_sex           = fget("dog_sex") or None
    dog_dob_raw       = fget("dog_dob") or None
    dog_sire          = fget("dog_sire") or None
    dog_dam           = fget("dog_dam") or None
    dog_breeder       = fget("dog_breeder") or None
    akc_owner_names   = fget("akc_owner_names") or None
    akc_owner_address = fget("akc_owner_address") or None
    akc_handler_name  = fget("akc_handler_name") or None
    akc_handler_address = fget("akc_handler_address") or None
    akc_separate_entries = bool(form.get("akc_separate_entries"))
    signature         = fget("signature") or None

    dog_dob: date | None = None
    if dog_dob_raw:
        try:
            dog_dob = date.fromisoformat(dog_dob_raw)
        except ValueError:
            dog_dob = None

    # --- if call name blank, fill with dog name ---
    if not dog_call_name and dog_name:
        dog_call_name = dog_name

    # --- validation ---
    errors: list[str] = []
    if not handler_name:
        errors.append("Name is required.")
    if not handler_email:
        errors.append("Email is required.")
    if not dog_name:
        errors.append("Dog's registered name is required.")
    if not akc_number_type:
        errors.append("Registration type (AKC/PAL/Foreign) is required.")
    if not dog_reg_number:
        errors.append("Registration number is required.")
    if akc_number_type == "Foreign" and not akc_foreign_country:
        errors.append("Country of origin is required for foreign registrations.")
    if not dog_breed:
        errors.append("Breed is required.")
    if not dog_sex:
        errors.append("Sex is required.")
    if not dog_dob:
        errors.append("Date of birth is required.")
    if not handler_name:
        errors.append("Owner name is required.")
    if not signature:
        errors.append("Signature (typed name) is required.")
    if not selection_rows:
        errors.append("Please select at least one class or test to enter.")

    if errors:
        for msg in errors:
            flash(request, msg, "error")
        ctx.update({
            "event": event,
            "trial": trial,
            "trial_events": trial_events,
            "te_classes_map": te_classes_map,
            "form_data": dict(form),
            "saved_dogs": _get_saved_dogs(current_user, db),
            "countries": get_country_choices(),
            "csrf_token": _csrf(request),
            "paypal_client_id": settings.PAYPAL_CLIENT_ID,
        })
        return templates.TemplateResponse(
            request, "trials/akc_entry_form.html", ctx, status_code=422
        )

    total_fee_cents = _compute_fee_from_rows(selection_rows, trial_events)

    # Link to a User account by handler email if there's no logged-in user.
    linked_user_id = current_user.id if current_user else None
    if linked_user_id is None and handler_email:
        matched = find_user_by_email(handler_email, db)
        if matched:
            linked_user_id = matched.id

    entry = TrialEntry(
        event_id=event_id,
        user_id=linked_user_id,
        governing_body="AKC",
        handler_name=handler_name,
        handler_email=handler_email,
        handler_phone=handler_phone,
        address_line1=address_line1,
        address_line2=address_line2,
        city=city,
        state_province=state_province,
        postal_code=postal_code,
        country=country,
        dog_name=dog_name,
        dog_call_name=dog_call_name,
        dog_breed=dog_breed,
        dog_registration_number=dog_reg_number,
        dog_sex=dog_sex,
        dog_dob=dog_dob,
        dog_sire=dog_sire,
        dog_dam=dog_dam,
        dog_breeder=dog_breeder,
        akc_number_type=akc_number_type,
        akc_foreign_country=akc_foreign_country,
        akc_owner_names=akc_owner_names,
        akc_owner_address=akc_owner_address,
        akc_handler_name=akc_handler_name,
        akc_handler_address=akc_handler_address,
        akc_separate_entries=akc_separate_entries,
        signature=signature,
        total_fee_cents=total_fee_cents,
        paypal_order_id=None,
        is_paid=False,
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

    # Optionally save (or merge into existing) dog on user's account
    if current_user and form.get("save_dog"):
        existing_dog = db.query(Dog).filter_by(
            user_id=current_user.id, dog_name=dog_name
        ).first()
        if existing_dog:
            # Merge: only update empty fields on the existing record. Keeps
            # AHBA fields the user already saved when re-saving from AKC.
            def _merge(attr, value):
                if value and not getattr(existing_dog, attr):
                    setattr(existing_dog, attr, value)
            _merge("dog_call_name", dog_call_name if dog_call_name != dog_name else None)
            _merge("dog_breed", dog_breed)
            _merge("dog_sex", dog_sex)
            _merge("dog_dob", dog_dob)
            _merge("dog_sire", dog_sire)
            _merge("dog_dam", dog_dam)
            _merge("dog_breeder", dog_breeder)
            _merge("akc_number_type", akc_number_type)
            _merge("akc_registration_number", dog_reg_number)
            _merge("akc_foreign_country", akc_foreign_country)
        else:
            db.add(Dog(
                user_id=current_user.id,
                dog_name=dog_name,
                dog_call_name=dog_call_name if dog_call_name != dog_name else None,
                dog_breed=dog_breed,
                dog_sex=dog_sex,
                dog_dob=dog_dob,
                dog_sire=dog_sire,
                dog_dam=dog_dam,
                dog_breeder=dog_breeder,
                akc_number_type=akc_number_type,
                akc_registration_number=dog_reg_number,
                akc_foreign_country=akc_foreign_country,
            ))
        db.commit()

    return RedirectResponse(
        f"/payments/checkout/entry/{entry.id}",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# AHBA entry — GET
# ---------------------------------------------------------------------------

@router.get("/{event_id}/trial/enter/ahba", response_class=HTMLResponse)
def ahba_entry_get(
    event_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    event = _load_event(event_id, db)
    trial = _load_trial(event_id, "AHBA", db)
    status = get_trial_status(trial.reg_close_dt)
    if status != "open":
        if status == "not_yet_open":
            flash(request, "AHBA trial registration for this event is not yet open.", "info")
        else:
            flash(request, "AHBA trial registration is currently closed.", "info")
        return RedirectResponse(f"/events/{event_id}", status_code=302)

    trial_events, te_classes_map = _load_trial_events(trial.id, db)
    current_user = ctx.get("current_user")
    ctx.update({
        "event": event,
        "trial": trial,
        "trial_events": trial_events,
        "te_classes_map": te_classes_map,
        "form_data": _autofill(current_user),
        "saved_dogs": _get_saved_dogs(current_user, db),
        "countries": get_country_choices(),
        "csrf_token": _csrf(request),
        "paypal_client_id": settings.PAYPAL_CLIENT_ID,
    })
    return templates.TemplateResponse(request, "trials/ahba_entry_form.html", ctx)


# ---------------------------------------------------------------------------
# AHBA entry — POST
# ---------------------------------------------------------------------------

@router.post("/{event_id}/trial/enter/ahba", response_class=HTMLResponse)
async def ahba_entry_post(
    event_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _validate_csrf(form.get("csrf_token", ""), request)

    event = _load_event(event_id, db)
    trial = _load_trial(event_id, "AHBA", db)
    status = get_trial_status(trial.reg_close_dt)
    if status != "open":
        msg = "AHBA trial registration for this event is not yet open." \
            if status == "not_yet_open" else "AHBA trial registration is now closed."
        flash(request, msg, "error")
        return RedirectResponse(f"/events/{event_id}", status_code=302)

    trial_events, te_classes_map = _load_trial_events(trial.id, db)

    current_user = ctx.get("current_user")

    # --- collect selections ---
    # One row per (trial_event, side) the registrant entered, each with its own
    # class and day preference so the two AHBA judges can score different runs.
    has_e2 = trial.ahba_event_2_judge is not None
    selection_rows = _parse_selection_rows(
        form, trial_events, te_classes_map, has_e2,
        day_choices=("saturday", "sunday"),
    )

    def fget(name: str, default: str = "") -> str:
        return form.get(name, default).strip()

    handler_name    = fget("handler_name")   # AHBA: actual owner name
    handler_email   = fget("handler_email")
    handler_phone   = fget("handler_phone") or None
    address_line1   = fget("address_line1")
    address_line2   = fget("address_line2") or None
    city            = fget("city")
    state_province  = fget("state_province") or None
    postal_code     = fget("postal_code")
    country_raw     = fget("country", "US")
    country         = country_raw if country_raw and country_raw != "---" else "US"

    dog_name            = fget("dog_name")
    dog_reg_number      = fget("dog_registration_number") or None
    dog_sex             = fget("dog_sex") or None
    dog_breed           = fget("dog_breed") or None
    dog_place_of_birth  = fget("dog_place_of_birth") or None
    dog_breeder         = fget("dog_breeder") or None
    dog_sire            = fget("dog_sire") or None
    dog_dam             = fget("dog_dam") or None
    ahba_agent_name     = fget("ahba_agent_name") or None
    ahba_agent_phone    = fget("ahba_agent_phone") or None
    ahba_agent_email    = fget("ahba_agent_email") or None
    signature           = fget("signature") or None

    # --- validation ---
    errors: list[str] = []
    if not handler_name:
        errors.append("Owner name is required.")
    if not handler_email:
        errors.append("Owner email is required.")
    if not handler_phone:
        errors.append("Owner phone number is required.")
    if not dog_name:
        errors.append("Dog's full registered name is required.")
    if not dog_reg_number:
        errors.append("Registry type and number is required.")
    if not dog_sex:
        errors.append("Sex is required.")
    if not dog_breed:
        errors.append("Breed variety is required.")
    if not signature:
        errors.append("Signature (typed name) is required.")
    if not selection_rows:
        errors.append("Please select at least one event to enter.")

    if errors:
        for msg in errors:
            flash(request, msg, "error")
        ctx.update({
            "event": event,
            "trial": trial,
            "trial_events": trial_events,
            "te_classes_map": te_classes_map,
            "form_data": dict(form),
            "saved_dogs": _get_saved_dogs(current_user, db),
            "countries": get_country_choices(),
            "csrf_token": _csrf(request),
            "paypal_client_id": settings.PAYPAL_CLIENT_ID,
        })
        return templates.TemplateResponse(
            request, "trials/ahba_entry_form.html", ctx, status_code=422
        )

    total_fee_cents = _compute_fee_from_rows(selection_rows, trial_events)

    linked_user_id = current_user.id if current_user else None
    if linked_user_id is None and handler_email:
        matched = find_user_by_email(handler_email, db)
        if matched:
            linked_user_id = matched.id

    entry = TrialEntry(
        event_id=event_id,
        user_id=linked_user_id,
        governing_body="AHBA",
        handler_name=handler_name,
        handler_email=handler_email,
        handler_phone=handler_phone,
        address_line1=address_line1,
        address_line2=address_line2,
        city=city,
        state_province=state_province,
        postal_code=postal_code,
        country=country,
        dog_name=dog_name,
        dog_breed=dog_breed,
        dog_registration_number=dog_reg_number,
        dog_sex=dog_sex,
        dog_place_of_birth=dog_place_of_birth,
        dog_breeder=dog_breeder,
        dog_sire=dog_sire,
        dog_dam=dog_dam,
        ahba_agent_name=ahba_agent_name,
        ahba_agent_phone=ahba_agent_phone,
        ahba_agent_email=ahba_agent_email,
        signature=signature,
        total_fee_cents=total_fee_cents,
        paypal_order_id=None,
        is_paid=False,
    )
    db.add(entry)
    db.flush()

    for row in selection_rows:
        db.add(TrialEntrySelection(
            trial_entry_id=entry.id,
            trial_event_id=row["te_id"],
            trial_event_class_id=row["class_id"],
            # Reuses the akc_trial_pref column for AHBA two-trial weekends.
            akc_trial_pref=row["pref"],
            day_preference=row["day_preference"],
        ))

    db.commit()
    db.refresh(entry)

    # Optionally save (or merge into existing) dog on user's account
    if current_user and form.get("save_dog"):
        existing_dog = db.query(Dog).filter_by(
            user_id=current_user.id, dog_name=dog_name
        ).first()
        if existing_dog:
            # Merge AHBA-specific fields into a dog that may already exist
            # from an AKC save. Only fills empty fields, never overwrites.
            def _merge(attr, value):
                if value and not getattr(existing_dog, attr):
                    setattr(existing_dog, attr, value)
            _merge("dog_breed", dog_breed)
            _merge("dog_sex", dog_sex)
            _merge("dog_sire", dog_sire)
            _merge("dog_dam", dog_dam)
            _merge("dog_breeder", dog_breeder)
            _merge("ahba_registration_number", dog_reg_number)
            _merge("dog_place_of_birth", dog_place_of_birth)
        else:
            db.add(Dog(
                user_id=current_user.id,
                dog_name=dog_name,
                dog_breed=dog_breed,
                dog_sex=dog_sex,
                dog_sire=dog_sire,
                dog_dam=dog_dam,
                dog_breeder=dog_breeder,
                ahba_registration_number=dog_reg_number,
                dog_place_of_birth=dog_place_of_birth,
            ))
        db.commit()

    return RedirectResponse(
        f"/payments/checkout/entry/{entry.id}",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------

@router.get("/{event_id}/trial/confirmation", response_class=HTMLResponse)
def trial_entry_confirmation(
    event_id: int,
    entry_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter_by(id=event_id, is_deleted=False).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    entry = db.query(TrialEntry).filter_by(id=entry_id, event_id=event_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    selections = db.query(TrialEntrySelection).filter_by(trial_entry_id=entry_id).all()
    selection_details = []
    for sel in selections:
        te = db.get(TrialEvent, sel.trial_event_id)
        cls = db.get(TrialEventClass, sel.trial_event_class_id) if sel.trial_event_class_id else None
        selection_details.append({
            "event_name": te.name,
            "class_name": cls.name if cls else "—",
            "akc_trial_pref": sel.akc_trial_pref,
            "day_preference": sel.day_preference,
            "call_number": sel.call_number,
        })

    ctx.update({
        "event": event,
        "entry": entry,
        "selection_details": selection_details,
    })
    return templates.TemplateResponse(request, "trials/confirmation.html", ctx)


# ---------------------------------------------------------------------------
# Legacy redirect: old single-entry URL → event detail
# ---------------------------------------------------------------------------

@router.get("/{event_id}/trial/enter", response_class=HTMLResponse)
def trial_enter_redirect(event_id: int):
    return RedirectResponse(f"/events/{event_id}", status_code=302)
