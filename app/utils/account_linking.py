"""Connect orphan TrialEntry / Registration rows to a User by email."""
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.models.registration import Registration
from app.models.trial import TrialEntry
from app.models.user import User


def link_orphan_records(user: User, db: Session) -> tuple[int, int]:
    """Find every TrialEntry and Registration with `user_id IS NULL` whose
    email matches the given user's email (case-insensitive, trimmed) and set
    `user_id = user.id`. Returns (trial_count, reg_count).

    Caller is responsible for committing the session.
    """
    if not user or not user.email:
        return (0, 0)
    target_email = user.email.strip().lower()

    trial_q = db.query(TrialEntry).filter(
        TrialEntry.user_id.is_(None),
        sa_func.lower(sa_func.trim(TrialEntry.handler_email)) == target_email,
    )
    trial_count = trial_q.update({"user_id": user.id}, synchronize_session=False)

    reg_q = db.query(Registration).filter(
        Registration.user_id.is_(None),
        sa_func.lower(sa_func.trim(Registration.email)) == target_email,
    )
    reg_count = reg_q.update({"user_id": user.id}, synchronize_session=False)

    return (trial_count or 0, reg_count or 0)


def find_user_by_email(email: str, db: Session) -> User | None:
    """Look up a User by exact (case-insensitive, trimmed) email match.
    Used at entry-submission time to link guest submissions to existing accounts.
    Returns the matched User or None.
    """
    if not email:
        return None
    target = email.strip().lower()
    if not target:
        return None
    return db.query(User).filter(
        sa_func.lower(sa_func.trim(User.email)) == target
    ).first()
