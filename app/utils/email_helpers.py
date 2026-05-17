"""
Email verification helpers.

Sends verification emails for trial entries and registrations via fastapi-mail.
Called from admin BackgroundTasks so failures don't block the HTTP response.
"""
import logging

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from jinja2 import Environment, FileSystemLoader

from app.config import settings
from app.database import SessionLocal
from app.models.event import Event
from app.models.registration import Registration
from app.models.trial import Trial, TrialEntry, TrialEntrySelection, TrialEvent, TrialEventClass

log = logging.getLogger(__name__)

_mail_config = ConnectionConfig(
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

_jinja_env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=True,
)


def _render(template_name: str, context: dict) -> str:
    return _jinja_env.get_template(template_name).render(**context)


async def send_trial_entry_verification(entry_id: int) -> None:
    """Send a verification email for a trial entry (called from BackgroundTasks)."""
    db = SessionLocal()
    try:
        entry = db.get(TrialEntry, entry_id)
        if not entry:
            log.warning("send_trial_entry_verification: entry %s not found", entry_id)
            return

        event = db.get(Event, entry.event_id)
        selections = db.query(TrialEntrySelection).filter_by(trial_entry_id=entry_id).all()

        # Build per-governing-body selection lists
        sel_by_body: dict[str, list[dict]] = {}
        for sel in selections:
            te = db.get(TrialEvent, sel.trial_event_id)
            cls = db.get(TrialEventClass, sel.trial_event_class_id)
            trial = db.get(Trial, te.trial_id)
            body = trial.governing_body
            sel_by_body.setdefault(body, []).append({
                "event_name": te.name,
                "class_name": cls.name,
                "call_number": sel.call_number,
            })

        # Try to find trial secretary email
        from app.models.user import User
        secretary = db.query(User).filter_by(is_trial_secretary=True, is_active=True).first()
        from_email = secretary.email if secretary else settings.MAIL_FROM

        for body, sels in sel_by_body.items():
            template_key = "akc" if body == "AKC" else "ahba"
            ctx = {
                "entry": entry,
                "event": event,
                "selections": sels,
                "governing_body": body,
                "base_url": settings.BASE_URL,
            }
            html_body = _render(f"emails/trial_entry_verification_{template_key}.html", ctx)
            plain_body = _render(f"emails/trial_entry_verification_{template_key}.txt", ctx)

            message = MessageSchema(
                subject=f"Your {body} Trial Entry — {event.title}",
                recipients=[entry.handler_email],
                body=html_body,
                subtype=MessageType.html,
                alternative_body=plain_body,
            )
            fm = FastMail(_mail_config)
            await fm.send_message(message)
            log.info("Sent %s trial entry verification to %s", body, entry.handler_email)

    except Exception:
        log.exception("Failed to send trial entry verification for entry %s", entry_id)
    finally:
        db.close()


async def send_registration_verification(reg_id: int) -> None:
    """Send a verification email for a Fun Run / Smart Dog Day registration."""
    db = SessionLocal()
    try:
        reg = db.get(Registration, reg_id)
        if not reg:
            log.warning("send_registration_verification: reg %s not found", reg_id)
            return

        event = db.get(Event, reg.event_id)
        ctx = {
            "reg": reg,
            "event": event,
            "base_url": settings.BASE_URL,
        }
        html_body = _render("emails/registration_verification.html", ctx)
        plain_body = _render("emails/registration_verification.txt", ctx)

        message = MessageSchema(
            subject=f"Registration Confirmed — {event.title}",
            recipients=[reg.email],
            body=html_body,
            subtype=MessageType.html,
            alternative_body=plain_body,
        )
        fm = FastMail(_mail_config)
        await fm.send_message(message)
        log.info("Sent registration verification to %s", reg.email)

    except Exception:
        log.exception("Failed to send registration verification for reg %s", reg_id)
    finally:
        db.close()
