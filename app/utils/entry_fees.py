"""Fee changes when an admin adds or removes classes, and what is then owed or refundable."""
from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.trial import TrialEntry, TrialEntrySelection, TrialEvent
from app.utils.payment_checks import open_check, record_kind
from app.utils.payment_records import history
from app.utils.paypal_payments import active_payment


def runs(pref: str | None) -> int:
    """Old "both" rows are one row but two runs."""
    return 2 if pref == "both" else 1


def ensure_fee_can_change(db: Session, entry: TrialEntry) -> None:
    if not entry.is_paid and active_payment(db, entry) is not None:
        raise ValueError("A PayPal payment is being processed. Try again once it finishes.")


def change_fee(db: Session, entry: TrialEntry, delta_cents: int) -> None:
    """Adjust the total. Call after the selections are changed; the caller commits.

    Adjusting, rather than re-adding current prices, keeps an agreed custom or older price.
    """
    if not delta_cents:
        return
    db.flush()
    old = entry.total_fee_cents
    selected = (
        db.query(TrialEvent.fee_cents, TrialEntrySelection.akc_trial_pref)
        .join(TrialEntrySelection, TrialEntrySelection.trial_event_id == TrialEvent.id)
        .filter(TrialEntrySelection.trial_entry_id == entry.id)
        .all()
    )
    new = max(old + delta_cents, 0)
    if not selected:
        new = 0
    elif new == 0 and old > 0:
        # Prices changed since entry. $0 would make the remaining classes free.
        new = sum(fee * runs(pref) for fee, pref in selected)

    if entry.is_paid and not any(
        p.status in ("completed", "refunded", "review") for p in history(db, entry)
    ):
        # Paid before payment history existed: keep what was paid from following the new fee.
        db.add(Payment(
            kind=record_kind(entry), record_id=entry.id, method="other", status="completed",
            entry_cents=old, total_cents=old, received_cents=old,
            note="Added when the fee changed. Original amount not recorded; the old fee is assumed.",
        ))
    entry.total_fee_cents = new
    check = None if entry.is_paid else open_check(db, entry)
    if check is not None:
        check.entry_cents = check.total_cents = new
    db.flush()


def balance_cents(db: Session, entry: TrialEntry) -> int:
    """For a paid entry: above 0 is still owed, below 0 is a refund due. Otherwise 0."""
    if not entry.is_paid:
        return 0
    done = [p for p in history(db, entry) if p.status == "completed" and p.received_cents is not None]
    if not done:
        return 0
    paid = done[-1].received_cents - done[-1].processing_fee_cents
    return entry.total_fee_cents - paid


def fee_message(db: Session, entry: TrialEntry, old_cents: int) -> str:
    """Sentence telling the admin what a change did to the fee."""
    new = entry.total_fee_cents
    if new == old_cents:
        return "The fee is unchanged."
    message = f"Fee changed from ${old_cents / 100:.2f} to ${new / 100:.2f}."
    if entry.is_paid:
        balance = balance_cents(db, entry)
        if balance > 0:
            message += f" Balance due: ${balance / 100:.2f}."
        elif balance < 0:
            message += f" Refund due: ${-balance / 100:.2f}. Issue it in PayPal."
    elif open_check(db, entry):
        message += " The expected check amount was updated; tell the entrant."
    return message
