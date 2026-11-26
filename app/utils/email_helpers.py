"""
Entry and registration emails.

Called from BackgroundTasks so failures don't block the HTTP response. Each send
function returns True only if the mail server accepted the message, and
`verification_sent` is set only then, and only for a paid record.
"""
import logging

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType, MultipartSubtypeEnum
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings
from app.database import SessionLocal
from app.models.event import Event
from app.models.registration import Registration, RegistrationDog
from app.models.trial import Trial, TrialEntry, TrialEntrySelection, TrialEvent, TrialEventClass
from app.utils.entry_fees import balance_cents
from app.utils.payment_checks import confirmation_url, open_check
from app.utils.payment_records import check_info, paid_amount_cents
from app.utils.trial_selections import trial_label

log = logging.getLogger(__name__)

# Plain-text templates must not be HTML-escaped.
_jinja_env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"]),
)


def mail_configured() -> bool:
    return bool(settings.MAIL_FROM)


def _render(template_name: str, context: dict) -> str:
    return _jinja_env.get_template(template_name).render(**context)


async def _deliver(subject: str, recipient: str, html: str, plain: str) -> None:
    # Built per send: a blank MAIL_FROM fails validation, which must not break imports.
    config = ConnectionConfig(
        MAIL_USERNAME=settings.MAIL_USERNAME,
        MAIL_PASSWORD=settings.MAIL_PASSWORD,
        MAIL_FROM=settings.MAIL_FROM,
        MAIL_PORT=settings.MAIL_PORT,
        MAIL_SERVER=settings.MAIL_SERVER,
        MAIL_STARTTLS=settings.MAIL_STARTTLS,
        MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
        USE_CREDENTIALS=bool(settings.MAIL_USERNAME),
        VALIDATE_CERTS=True,
    )
    await FastMail(config).send_message(MessageSchema(
        subject=subject,
        recipients=[recipient],
        body=html,
        subtype=MessageType.html,
        alternative_body=plain,
        multipart_subtype=MultipartSubtypeEnum.alternative,
    ))


def _payment_context(db, rec, event) -> dict:
    """status is 'paid', 'awaiting_check' or 'unpaid'."""
    check = None if rec.is_paid else open_check(db, rec)
    amount = check.total_cents if check else paid_amount_cents(db, rec)
    return {
        "status": "paid" if rec.is_paid else "awaiting_check" if check else "unpaid",
        "amount_cents": amount,
        "check": check_info(db, rec, event, check) if check else None,
        "base_url": settings.BASE_URL,
        "record_url": settings.BASE_URL.rstrip("/") + confirmation_url(rec),
    }


_SUBJECT = {"paid": "Confirmed", "awaiting_check": "Received, awaiting check", "unpaid": "Received, not yet paid"}


async def _send(rec, recipient: str, label: str, event, template: str, context: dict, db) -> bool:
    if not recipient:
        log.warning("No email address on %r; nothing sent", rec)
        return False
    await _deliver(
        f"{label} {_SUBJECT[context['status']]} — {event.title}",
        recipient,
        _render(f"emails/{template}.html", context),
        _render(f"emails/{template}.txt", context),
    )
    if context["status"] == "paid":
        rec.verification_sent = True
        db.commit()
    log.info("Sent %s email for %r", context["status"], rec)
    return True


def email_key(email: str | None) -> str:
    """How entries are matched to one person."""
    return (email or "").strip().lower()


def entries_for(db, event_id: int, body: str, email: str, paid_only: bool = False) -> list[TrialEntry]:
    """One person's entries for AKC or AHBA at an event. A blank address matches nobody."""
    if not email_key(email):
        return []
    entries = db.query(TrialEntry).filter_by(event_id=event_id, governing_body=body)
    return [
        e for e in entries.order_by(TrialEntry.dog_name, TrialEntry.id)
        if email_key(e.handler_email) == email_key(email) and (e.is_paid or not paid_only)
    ]


def _entry_context(db, entry: TrialEntry, event) -> dict:
    selections = []
    for sel in db.query(TrialEntrySelection).filter_by(trial_entry_id=entry.id):
        te = db.get(TrialEvent, sel.trial_event_id)
        if te is None:
            continue
        # Test classes (JHD, instinct tests) have no class level.
        cls = db.get(TrialEventClass, sel.trial_event_class_id) if sel.trial_event_class_id else None
        where = [trial_label(db.get(Trial, te.trial_id), sel.akc_trial_pref)]
        if sel.day_preference:
            where.append(sel.day_preference.title())
        selections.append({
            "event_name": te.name,
            "class_name": cls.name if cls else "Test",
            "where": ", ".join(w for w in where if w),
            "call_number": sel.call_number,
        })
    return {
        "entry": entry, "event": event, "selections": selections,
        "governing_body": entry.governing_body, "balance_cents": balance_cents(db, entry),
        **_payment_context(db, entry, event),
    }


async def send_group_verification(event_id: int, body: str, email: str, paid_only: bool = False) -> bool:
    """One email covering all of a person's AKC (or AHBA) entries at an event."""
    db = SessionLocal()
    try:
        entries = entries_for(db, event_id, body, email, paid_only)
        if not entries:
            log.warning("No %s entries to email at event %s", body, event_id)
            return False
        event = db.get(Event, event_id)
        items = [_entry_context(db, e, event) for e in entries]
        due = [i for i in items if i["status"] != "paid"]
        context = {
            "event": event, "items": items, "governing_body": body, "base_url": settings.BASE_URL,
            "name": entries[0].handler_name, "all_paid": not due,
            "paid_cents": sum(i["amount_cents"] for i in items if i["status"] == "paid"),
            "due_cents": sum(i["amount_cents"] for i in due) + sum(max(i["balance_cents"], 0) for i in items),
            "refund_cents": sum(max(-i["balance_cents"], 0) for i in items),
        }
        await _deliver(
            f"{body} Trial Entries {'Received, payment outstanding' if due else 'Confirmed'} — {event.title}",
            entries[0].handler_email.strip(),
            _render("emails/trial_entries_group.html", context),
            _render("emails/trial_entries_group.txt", context),
        )
        for item in items:
            if item["status"] == "paid":
                item["entry"].verification_sent = True
        db.commit()
        return True
    except Exception:
        log.exception("Failed to send %s group email for event %s", body, event_id)
        return False
    finally:
        db.close()


async def send_trial_entry_verification(entry_id: int) -> bool:
    db = SessionLocal()
    try:
        entry = db.get(TrialEntry, entry_id)
        if not entry:
            log.warning("send_trial_entry_verification: entry %s not found", entry_id)
            return False
        event = db.get(Event, entry.event_id)
        body = entry.governing_body
        context = _entry_context(db, entry, event)
        template = f"trial_entry_verification_{'akc' if body == 'AKC' else 'ahba'}"
        return await _send(entry, entry.handler_email, f"{body} Trial Entry", event, template, context, db)
    except Exception:
        log.exception("Failed to send trial entry email for entry %s", entry_id)
        return False
    finally:
        db.close()


async def send_registration_verification(reg_id: int) -> bool:
    db = SessionLocal()
    try:
        reg = db.get(Registration, reg_id)
        if not reg:
            log.warning("send_registration_verification: reg %s not found", reg_id)
            return False
        event = db.get(Event, reg.event_id)
        dogs = [d.dog_name for d in db.query(RegistrationDog).filter_by(registration_id=reg_id)]
        context = {
            "reg": reg, "event": event, "dog_names": ", ".join(dogs) or reg.dog_name,
            **_payment_context(db, reg, event),
        }
        return await _send(reg, reg.email, "Registration", event, "registration_verification", context, db)
    except Exception:
        log.exception("Failed to send registration email for reg %s", reg_id)
        return False
    finally:
        db.close()


def queue_email(background_tasks, rec) -> None:
    """Queue the email matching the record's current payment state."""
    if isinstance(rec, Registration):
        background_tasks.add_task(send_registration_verification, reg_id=rec.id)
    else:
        background_tasks.add_task(send_trial_entry_verification, entry_id=rec.id)
