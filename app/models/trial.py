from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

GOVERNING_BODIES = ("AKC", "AHBA")


class Trial(Base):
    """One judged trial within a trial weekend — either AKC or AHBA."""

    __tablename__ = "trials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    governing_body: Mapped[str] = mapped_column(String(10), nullable=False)  # 'AKC' | 'AHBA'
    # AKC has two simultaneous trials per weekend identified by event number.
    akc_event_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    akc_event_number_2: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # AHBA can also have two simultaneous trials per weekend; differentiated by judge.
    ahba_event_1_judge: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ahba_event_2_judge: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Registration close datetime (UTC). Computed from start_date but admin-overridable.
    reg_close_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Trial id={self.id} body={self.governing_body!r} event_id={self.event_id}>"


class TrialEvent(Base):
    """A course+stock combination within a trial (e.g., 'Duck A', 'Sheep B')."""

    __tablename__ = "trial_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=6000)
    # "either", "friday", "saturday", or None (means event-level doesn't restrict days)
    available_days: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Test classes have no level/class selection (e.g., Instinct Test, JHD)
    is_test_class: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<TrialEvent id={self.id} name={self.name!r}>"


class TrialEventClass(Base):
    """A class level within a trial event (e.g., 'Started', 'Intermediate', 'Advanced')."""

    __tablename__ = "trial_event_classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trial_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<TrialEventClass id={self.id} name={self.name!r}>"


class TrialEntry(Base):
    """One handler+dog entry for one governing body (AKC or AHBA) at a trial weekend."""

    __tablename__ = "trial_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    governing_body: Mapped[str] = mapped_column(String(10), nullable=False, default="AKC")

    # Primary contact / owner address
    # For AKC: this is the handler/submitter; for AHBA: this is the actual owner
    handler_name: Mapped[str] = mapped_column(String(255), nullable=False)
    handler_email: Mapped[str] = mapped_column(String(255), nullable=False)
    handler_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")

    # Dog information (shared fields)
    dog_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dog_call_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dog_breed: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dog_registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dog_sex: Mapped[str | None] = mapped_column(String(1), nullable=True)  # "M" or "F"
    dog_dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    dog_sire: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dog_dam: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dog_breeder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dog_place_of_birth: Mapped[str | None] = mapped_column(String(100), nullable=True)  # AHBA

    # AKC-specific fields
    akc_number_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "AKC", "PAL_ILP", "Foreign"
    akc_foreign_country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    akc_owner_names: Mapped[str | None] = mapped_column(String(255), nullable=True)
    akc_owner_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    akc_handler_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # ring handler if different
    akc_handler_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    akc_separate_entries: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # AHBA-specific fields
    ahba_agent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ahba_agent_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ahba_agent_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Signature (typed name — required for both AKC and AHBA)
    signature: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Payment
    total_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paypal_order_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    # Free-text payment reference: PayPal transaction id for online payments,
    # or a check / cash reference entered by an admin for manual entries.
    payment_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_manual_entry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return (
            f"<TrialEntry id={self.id} body={self.governing_body!r} "
            f"handler={self.handler_name!r} dog={self.dog_name!r}>"
        )


class TrialEntrySelection(Base):
    """Which class a dog is entered in for a specific trial event."""

    __tablename__ = "trial_entry_selections"
    # A handler may enter the same event in BOTH trials of a weekend
    # (Event 1 + Event 2), so the uniqueness key includes akc_trial_pref.
    __table_args__ = (
        UniqueConstraint(
            "trial_entry_id", "trial_event_id", "akc_trial_pref",
            name="uq_entry_event_pref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trial_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trial_event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trial_events.id", ondelete="RESTRICT"), nullable=False
    )
    # Nullable: test classes have no class levels
    trial_event_class_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("trial_event_classes.id", ondelete="RESTRICT"), nullable=True
    )
    # AKC: which event number(s) ("event_1", "event_2", "both")
    akc_trial_pref: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Day preference for "either day" events
    day_preference: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "friday", "saturday"
    # Call number assigned by admin after entry closes
    call_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<TrialEntrySelection entry={self.trial_entry_id} "
            f"event={self.trial_event_id} class={self.trial_event_class_id}>"
        )
