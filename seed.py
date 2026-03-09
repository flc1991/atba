"""
Seed script: populates the database with the 2026 trial weekend, a fun run,
a smart dog day, one admin user, and a few sample members.

Run from the project root:
    python seed.py
"""

from datetime import date, datetime, timezone, timedelta

import bcrypt
from sqlalchemy.orm import Session

from app.database import engine
from app.models.event import Event
from app.models.member import Member
from app.models.trial import Trial, TrialEvent, TrialEventClass
from app.models.user import User
from app.utils.registration_windows import compute_akc_close, compute_ahba_close

def _hash_pw(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def seed(db: Session) -> None:
    # ------------------------------------------------------------------ users
    if not db.query(User).filter_by(email="admin@atba-herding.org").first():
        admin = User(
            email="admin@atba-herding.org",
            password_hash=_hash_pw("ChangeMe123!"),
            role="admin",
            is_trial_secretary=True,
            name="ATBA Admin",
            address_line1="123 Farm Rd",
            city="Anytown",
            state_province="VA",
            postal_code="22001",
            country="US",
        )
        db.add(admin)

    # ------------------------------------------------------------------ 2026 April trial weekend
    trial_weekend = db.query(Event).filter_by(title="2026 April Trial Weekend").first()
    if not trial_weekend:
        trial_weekend = Event(
            title="2026 April Trial Weekend",
            event_type="trial",
            start_date=date(2026, 4, 17),
            end_date=date(2026, 4, 19),
            location="ATBA Trial Grounds",
            description=(
                "Annual spring trial weekend. AKC trials April 17–18; AHBA trial April 19."
            ),
            is_published=True,
        )
        db.add(trial_weekend)
        db.flush()  # get trial_weekend.id

        # AKC Trial 1
        akc1 = Trial(
            event_id=trial_weekend.id,
            governing_body="AKC",
            akc_event_number="2026001",
            reg_close_dt=compute_akc_close(date(2026, 4, 17)),
            fee_per_class_cents=3500,
        )
        db.add(akc1)
        db.flush()

        for stock, sort in [("Duck A", 0), ("Sheep A", 1), ("Cattle A", 2)]:
            te = TrialEvent(trial_id=akc1.id, name=stock, sort_order=sort)
            db.add(te)
            db.flush()
            for cls_name, cs in [("Started", 0), ("Intermediate", 1), ("Advanced", 2)]:
                db.add(TrialEventClass(trial_event_id=te.id, name=cls_name, sort_order=cs))

        # AKC Trial 2
        akc2 = Trial(
            event_id=trial_weekend.id,
            governing_body="AKC",
            akc_event_number="2026002",
            reg_close_dt=compute_akc_close(date(2026, 4, 17)),
            fee_per_class_cents=3500,
        )
        db.add(akc2)
        db.flush()

        for stock, sort in [("Duck B", 0), ("Sheep B", 1)]:
            te = TrialEvent(trial_id=akc2.id, name=stock, sort_order=sort)
            db.add(te)
            db.flush()
            for cls_name, cs in [("Started", 0), ("Intermediate", 1), ("Advanced", 2)]:
                db.add(TrialEventClass(trial_event_id=te.id, name=cls_name, sort_order=cs))

        # AHBA Trial
        ahba = Trial(
            event_id=trial_weekend.id,
            governing_body="AHBA",
            reg_close_dt=compute_ahba_close(date(2026, 4, 19)),
            fee_per_class_cents=3000,
        )
        db.add(ahba)
        db.flush()

        for stock, sort in [("Duck", 0), ("Sheep", 1)]:
            te = TrialEvent(trial_id=ahba.id, name=stock, sort_order=sort)
            db.add(te)
            db.flush()
            for cls_name, cs in [("I", 0), ("II", 1), ("III", 2)]:
                db.add(TrialEventClass(trial_event_id=te.id, name=cls_name, sort_order=cs))

    # ------------------------------------------------------------------ Fun Run
    if not db.query(Event).filter_by(title="2026 Spring Fun Run").first():
        pre_close = datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc)
        db.add(Event(
            title="2026 Spring Fun Run",
            event_type="fun_run",
            start_date=date(2026, 4, 5),
            end_date=date(2026, 4, 5),
            location="ATBA Trial Grounds",
            description="Relaxed fun run — all skill levels welcome.",
            fee_pre_member_cents=1500,
            fee_pre_general_cents=2000,
            fee_late_cents=2500,
            pre_entry_close_dt=pre_close,
            is_published=True,
        ))

    # ------------------------------------------------------------------ Smart Dog Day
    if not db.query(Event).filter_by(title="2026 Smart Dog Day").first():
        pre_close = datetime(2026, 5, 14, 23, 59, 59, tzinfo=timezone.utc)
        db.add(Event(
            title="2026 Smart Dog Day",
            event_type="smart_dog_day",
            start_date=date(2026, 5, 21),
            end_date=date(2026, 5, 21),
            location="ATBA Trial Grounds",
            description="Educational instinct testing and training day.",
            fee_pre_member_cents=2000,
            fee_pre_general_cents=2500,
            fee_late_cents=3000,
            pre_entry_close_dt=pre_close,
            is_published=True,
        ))

    # ------------------------------------------------------------------ Members
    for name, year in [
        ("Jane Handler", 2026),
        ("Bob Herder", 2026),
        ("Carol Tending", 2025),  # expired
    ]:
        if not db.query(Member).filter_by(name=name).first():
            db.add(Member(
                name=name,
                email=f"{name.lower().replace(' ', '.')}@example.com",
                address_line1="456 Pasture Ln",
                city="Springfield",
                state_province="VA",
                postal_code="22150",
                country="US",
                membership_year=year,
            ))

    db.commit()
    print("Seed complete.")


if __name__ == "__main__":
    with Session(engine) as db:
        seed(db)
