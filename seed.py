"""
Seed script — populates the database with 2026 ATBA events, trials, admin user,
and sample members.

Run from the project root:
    python seed.py
"""

from datetime import date, datetime, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.database import engine
from app.models.event import Event
from app.models.member import Member
from app.models.trial import Trial, TrialEvent, TrialEventClass
from app.models.user import User
from app.utils.registration_windows import compute_akc_close, compute_ahba_close


LOCATION = "Raspberry Ridge Sheep Farm, Bangor, PA"


def _hash_pw(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def _add_akc_trial_events(db: Session, trial_id: int) -> None:
    """Add the standard AKC trial events and test classes to a trial."""

    # Trial classes — (name, available_days, fee_cents, sort_order)
    trial_classes = [
        ("Duck A",   "either",   6000, 0),
        ("Duck B",   "either",   6000, 1),
        ("Goose B",  "either",   6000, 2),
        ("Sheep A",  "either",   6000, 3),
        ("Sheep B",  "either",   6000, 4),
        ("Sheep C",  "either",   7500, 5),
        ("Sheep D",  "friday",   7500, 6),
    ]
    for name, days, fee, sort in trial_classes:
        te = TrialEvent(
            trial_id=trial_id, name=name, sort_order=sort,
            fee_cents=fee, available_days=days, is_test_class=False,
        )
        db.add(te)
        db.flush()
        for cls_name, cs in [("Started", 0), ("Intermediate", 1), ("Advanced", 2)]:
            db.add(TrialEventClass(trial_event_id=te.id, name=cls_name, sort_order=cs))

    # Test classes — no level selection
    test_classes = [
        ("Duck or Goose Instinct Test",  "either", 7000, 10),
        ("Duck or Goose Herding Test",   "either", 6000, 11),
        ("Duck or Goose Pre-Trial Test", "either", 6000, 12),
        ("Sheep Instinct Test",          "either", 7000, 13),
        ("Sheep Herding Test",           "either", 6000, 14),
        ("Sheep Pre-Trial Test",         "either", 6000, 15),
    ]
    for name, days, fee, sort in test_classes:
        db.add(TrialEvent(
            trial_id=trial_id, name=name, sort_order=sort,
            fee_cents=fee, available_days=days, is_test_class=True,
        ))


def _add_ahba_trial_events(db: Session, trial_id: int) -> None:
    """Add the standard AHBA trial events to a trial."""

    # Trial classes (levels I/II/III) — (name, fee_cents, sort_order)
    trial_classes = [
        ("5 Ducks HTD",       5800, 0),
        ("4-5 Geese HTD",     5800, 1),
        ("5 Ducks HTAD",      5800, 2),
        ("25 Sheep + RLF",    7500, 3),
        ("10+ Sheep HRD",     7500, 4),
        ("3-5 Sheep HTD",     5800, 5),
        ("3-5 Sheep HTAD",    5800, 6),
    ]
    for name, fee, sort in trial_classes:
        te = TrialEvent(
            trial_id=trial_id, name=name, sort_order=sort,
            fee_cents=fee, available_days=None, is_test_class=False,
        )
        db.add(te)
        db.flush()
        for cls_name, cs in [("I", 0), ("II", 1), ("III", 2)]:
            db.add(TrialEventClass(trial_event_id=te.id, name=cls_name, sort_order=cs))

    # Test classes (JHD — no level selection)
    test_classes = [
        ("5 Ducks JHD",  5800, 10),
        ("5 Geese JHD",  5800, 11),
        ("5 Sheep JHD",  5800, 12),
    ]
    for name, fee, sort in test_classes:
        db.add(TrialEvent(
            trial_id=trial_id, name=name, sort_order=sort,
            fee_cents=fee, available_days=None, is_test_class=True,
        ))


def _backfill_trial_fields(db: Session) -> None:
    """One-time backfills for fields/descriptions added after initial seeding.

    Each block updates ONLY when the existing value is NULL or matches the prior
    seed default — preserving any admin edits made via the UI.
    """
    # --- AKC trial numbers for July & October (added 2026-05-23) ---
    for ev_title, num1, num2 in [
        ("2026 July Trial Weekend",    "2026226905", "2026226906"),
        ("2026 October Trial Weekend", "2026226907", "2026226908"),
    ]:
        ev = db.query(Event).filter_by(title=ev_title).first()
        if not ev:
            continue
        akc = db.query(Trial).filter_by(event_id=ev.id, governing_body="AKC").first()
        if akc and akc.akc_event_number is None:
            akc.akc_event_number = num1
        if akc and akc.akc_event_number_2 is None:
            akc.akc_event_number_2 = num2

    # --- AHBA judges (added 2026-05-23) ---
    for ev_title, judge_1, judge_2 in [
        ("2026 April Trial Weekend", "Carolyn Wilki", None),
        ("2026 July Trial Weekend",  "Brian Wistrom", "Carolyn Wilki"),
    ]:
        ev = db.query(Event).filter_by(title=ev_title).first()
        if not ev:
            continue
        ahba = db.query(Trial).filter_by(event_id=ev.id, governing_body="AHBA").first()
        if not ahba:
            continue
        if ahba.ahba_event_1_judge is None and judge_1:
            ahba.ahba_event_1_judge = judge_1
        if ahba.ahba_event_2_judge is None and judge_2:
            ahba.ahba_event_2_judge = judge_2

    # --- Trial weekend descriptions: insert newline between AKC and AHBA paragraphs ---
    # Only rewrite if the description still matches the old single-paragraph default.
    description_backfills = {
        "2026 April Trial Weekend": (
            "AKC Herding Tests & Trials (Events 1 & 2) — Friday April 17 and Saturday April 18. "
            "AHBA Trial — Sunday April 19. Sheep, Ducks, and Geese.",
            "AKC Herding Tests & Trials (Events 1 & 2) — Friday April 17 and Saturday April 18.\n"
            "AHBA Trial — Sunday April 19. Sheep, Ducks, and Geese."
        ),
        "2026 July Trial Weekend": (
            "AKC Herding Tests & Trials (Events 1 & 2) — Friday July 10 and Saturday July 11. "
            "AHBA Trial — Sunday July 12. Sheep and Ducks.",
            "AKC Herding Tests & Trials (Events 1 & 2) — Friday July 10 and Saturday July 11.\n"
            "AHBA Trials (Events 1 & 2) — Saturday July 11 and Sunday July 12. Sheep and Ducks."
        ),
        "2026 October Trial Weekend": (
            "AKC Herding Tests & Trials (Events 1 & 2) — Friday Oct 16 and Saturday Oct 17. "
            "AHBA Trial — Sunday Oct 18. Sheep and Ducks.",
            "AKC Herding Tests & Trials (Events 1 & 2) — Friday Oct 16 and Saturday Oct 17.\n"
            "AHBA Trials (Events 1 & 2) — Saturday Oct 17 and Sunday Oct 18. Sheep and Ducks."
        ),
    }
    for ev_title, (old_desc, new_desc) in description_backfills.items():
        ev = db.query(Event).filter_by(title=ev_title).first()
        if ev and ev.description == old_desc:
            ev.description = new_desc


def seed(db: Session) -> None:
    # ------------------------------------------------------------------ admin user
    if not db.query(User).filter_by(email="admin@atba-herding.org").first():
        db.add(User(
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
        ))

    # ------------------------------------------------------------------ Jan 2026 — membership meeting (past)
    if not db.query(Event).filter_by(title="2026 ATBA Membership Meeting").first():
        db.add(Event(
            title="2026 ATBA Membership Meeting",
            event_type="meeting",
            start_date=date(2026, 1, 3),
            end_date=date(2026, 1, 3),
            location=LOCATION,
            description="ATBA Membership Meeting and Member Training, 10am–4pm. Meeting at Noon with potluck lunch.",
            is_published=True,
        ))

    # ------------------------------------------------------------------ Mar 2026 — training day
    if not db.query(Event).filter_by(title="2026 March Training Day").first():
        db.add(Event(
            title="2026 March Training Day",
            event_type="meeting",
            start_date=date(2026, 3, 21),
            end_date=date(2026, 3, 21),
            location=LOCATION,
            description="ATBA member training day, 10am–4pm.",
            is_published=True,
        ))

    # ------------------------------------------------------------------ Apr 2026 — AKC + AHBA trial weekend
    april_trial = db.query(Event).filter_by(title="2026 April Trial Weekend").first()
    if not april_trial:
        april_trial = Event(
            title="2026 April Trial Weekend",
            event_type="trial",
            start_date=date(2026, 4, 17),
            end_date=date(2026, 4, 19),
            location=LOCATION,
            description=(
                "AKC Herding Tests & Trials (Events 1 & 2) — Friday April 17 and Saturday April 18.\n"
                "AHBA Trial — Sunday April 19. Sheep, Ducks, and Geese."
            ),
            is_published=True,
        )
        db.add(april_trial)
        db.flush()

        akc = Trial(
            event_id=april_trial.id,
            governing_body="AKC",
            akc_event_number="2026226901",
            akc_event_number_2="2026226902",
            reg_close_dt=compute_akc_close(date(2026, 4, 17)),
        )
        db.add(akc)
        db.flush()
        _add_akc_trial_events(db, akc.id)

        ahba = Trial(
            event_id=april_trial.id,
            governing_body="AHBA",
            ahba_event_1_judge="Carolyn Wilki",
            reg_close_dt=compute_ahba_close(date(2026, 4, 19)),
        )
        db.add(ahba)
        db.flush()
        _add_ahba_trial_events(db, ahba.id)

    # ------------------------------------------------------------------ May 2026 — fun match
    fun_run_description = (
        "ATBA Herding Trial Fun Match — open to all breeds. Up to 4 entries per dog; "
        "the same event may be entered more than once, and you can choose whether each "
        "run is judged or unjudged."
    )
    if not db.query(Event).filter_by(title="2026 May Fun Match").first():
        db.add(Event(
            title="2026 May Fun Match",
            event_type="fun_run",
            start_date=date(2026, 5, 9),
            end_date=date(2026, 5, 9),
            location=LOCATION,
            description=fun_run_description,
            fee_pre_member_cents=1000,
            fee_pre_general_cents=2500,
            fee_late_cents=3500,
            pre_entry_close_dt=datetime(2026, 5, 3, 23, 59, 59, tzinfo=timezone.utc),
            is_published=True,
        ))

    # ------------------------------------------------------------------ Jun 2026 — Smart Dog Day
    if not db.query(Event).filter_by(title="2026 Smart Dog Day").first():
        pre_close = datetime(2026, 5, 28, 23, 59, 59, tzinfo=timezone.utc)
        db.add(Event(
            title="2026 Smart Dog Day",
            event_type="smart_dog_day",
            start_date=date(2026, 6, 6),
            end_date=date(2026, 6, 6),
            location=LOCATION,
            description=(
                "AKC Farm Dog Certification, AKC Fetch Dog Certification, "
                "AKC Canine Good Citizen and Trick Titles."
            ),
            fee_pre_member_cents=1000,
            fee_pre_general_cents=2500,
            fee_late_cents=3500,
            pre_entry_close_dt=pre_close,
            is_published=True,
        ))

    # ------------------------------------------------------------------ Jun 2026 — club picnic
    if not db.query(Event).filter_by(title="2026 ATBA Club Picnic").first():
        db.add(Event(
            title="2026 ATBA Club Picnic",
            event_type="picnic",
            start_date=date(2026, 6, 27),
            end_date=date(2026, 6, 27),
            location=LOCATION,
            description="ATBA Club Picnic and member training day, 9am–4pm.",
            is_published=True,
        ))

    # ------------------------------------------------------------------ Jul 2026 — AKC + AHBA trial weekend
    july_trial = db.query(Event).filter_by(title="2026 July Trial Weekend").first()
    if not july_trial:
        july_trial = Event(
            title="2026 July Trial Weekend",
            event_type="trial",
            start_date=date(2026, 7, 10),
            end_date=date(2026, 7, 12),
            location=LOCATION,
            description=(
                "AKC Herding Tests & Trials (Events 1 & 2) — Friday July 10 and Saturday July 11.\n"
                "AHBA Trials (Events 1 & 2) — Saturday July 11 and Sunday July 12. Sheep and Ducks."
            ),
            is_published=True,
        )
        db.add(july_trial)
        db.flush()

    # Ensure July AKC trial + events exist
    july_akc = db.query(Trial).filter_by(event_id=july_trial.id, governing_body="AKC").first()
    if not july_akc:
        july_akc = Trial(
            event_id=july_trial.id,
            governing_body="AKC",
            akc_event_number="2026226905",
            akc_event_number_2="2026226906",
            reg_close_dt=compute_akc_close(date(2026, 7, 10)),
        )
        db.add(july_akc)
        db.flush()
    if not db.query(TrialEvent).filter_by(trial_id=july_akc.id).first():
        _add_akc_trial_events(db, july_akc.id)

    # Ensure July AHBA trial + events exist (two judges = two AHBA trials per weekend)
    july_ahba = db.query(Trial).filter_by(event_id=july_trial.id, governing_body="AHBA").first()
    if not july_ahba:
        july_ahba = Trial(
            event_id=july_trial.id,
            governing_body="AHBA",
            ahba_event_1_judge="Brian Wistrom",
            ahba_event_2_judge="Carolyn Wilki",
            reg_close_dt=compute_ahba_close(date(2026, 7, 12)),
        )
        db.add(july_ahba)
        db.flush()
    if not db.query(TrialEvent).filter_by(trial_id=july_ahba.id).first():
        _add_ahba_trial_events(db, july_ahba.id)

    # ------------------------------------------------------------------ Oct 2026 — fun match
    # No pricing yet — pricing is what gates the homepage "registering" filter,
    # so leaving fees null marks the event as not-yet-open.
    if not db.query(Event).filter_by(title="2026 October Fun Match").first():
        db.add(Event(
            title="2026 October Fun Match",
            event_type="fun_run",
            start_date=date(2026, 10, 3),
            end_date=date(2026, 10, 3),
            location=LOCATION,
            description=fun_run_description,
            is_published=True,
        ))

    # ------------------------------------------------------------------ Oct 2026 — AKC + AHBA trial weekend
    # NOTE: Registration for October is not yet open — Trials seeded with reg_close_dt = None
    # (interpreted by get_trial_status as "not_yet_open"). An admin opens it later by setting the date.
    oct_trial = db.query(Event).filter_by(title="2026 October Trial Weekend").first()
    if not oct_trial:
        oct_trial = Event(
            title="2026 October Trial Weekend",
            event_type="trial",
            start_date=date(2026, 10, 16),
            end_date=date(2026, 10, 18),
            location=LOCATION,
            description=(
                "AKC Herding Tests & Trials (Events 1 & 2) — Friday Oct 16 and Saturday Oct 17.\n"
                "AHBA Trials (Events 1 & 2) — Saturday Oct 17 and Sunday Oct 18. Sheep and Ducks."
            ),
            is_published=True,
        )
        db.add(oct_trial)
        db.flush()

    if not db.query(Trial).filter_by(event_id=oct_trial.id, governing_body="AKC").first():
        db.add(Trial(
            event_id=oct_trial.id,
            governing_body="AKC",
            akc_event_number="2026226907",
            akc_event_number_2="2026226908",
            reg_close_dt=None,
        ))
    if not db.query(Trial).filter_by(event_id=oct_trial.id, governing_body="AHBA").first():
        db.add(Trial(event_id=oct_trial.id, governing_body="AHBA", reg_close_dt=None))

    # ------------------------------------------------------------------ backfills
    # One-off backfill for fields added after initial seeding. Only updates rows
    # where the value is still NULL / matches the OLD seed default, so admin
    # edits via the UI are preserved.
    _backfill_trial_fields(db)

    # ------------------------------------------------------------------ members
    for name, year, status in [
        ("Jane Handler", 2026, "member"),
        ("Bob Herder",   2026, "member"),
        ("Carol Tending", 2025, "expired"),
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
                status=status,
            ))

    db.commit()
    print("Seed complete.")


if __name__ == "__main__":
    with Session(engine) as db:
        seed(db)
