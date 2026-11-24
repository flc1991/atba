"""PayPal processing fee: charged per run, never on checks or free entries."""
from sqlalchemy.orm import Session

from app.models.registration import Registration, RegistrationDog
from app.models.trial import TrialEntry, TrialEntrySelection, TrialEvent
from app.utils.payment_checks import record_amount_cents

PERCENT = 4

# Optional overrides, as (lowest run price, highest run price, fee), all in cents and
# inclusive. Example: (5000, 6000, 250) charges $2.50 on a $50 to $60 run.
FEE_TIERS: list[tuple[int, int, int]] = []


def processing_fee_cents(run_cents: int) -> int:
    """Fee for one run: 4% to the nearest cent, half a cent rounding up."""
    if run_cents <= 0:
        return 0
    for low, high, fee in FEE_TIERS:
        if low <= run_cents <= high:
            return fee
    return (run_cents * PERCENT + 50) // 100


def run_prices(db: Session, rec: Registration | TrialEntry) -> list[int]:
    """Price of each run. Always adds up to the record's own total."""
    total = record_amount_cents(rec)
    if isinstance(rec, TrialEntry):
        prices = [
            fee for (fee,) in db.query(TrialEvent.fee_cents)
            .join(TrialEntrySelection, TrialEntrySelection.trial_event_id == TrialEvent.id)
            .filter(TrialEntrySelection.trial_entry_id == rec.id)
        ]
        # Prices or selections may have been edited since the total was agreed.
        if sum(prices) == total:
            return prices
        runs = len(prices)
    else:
        dogs = db.query(RegistrationDog).filter_by(registration_id=rec.id).all()
        runs = sum(len(d.events) for d in dogs)
    runs = max(runs, 1)
    base, extra = divmod(total, runs)
    return [base + 1] * extra + [base] * (runs - extra)


def record_processing_fee_cents(db: Session, rec: Registration | TrialEntry) -> int:
    return sum(processing_fee_cents(price) for price in run_prices(db, rec))
