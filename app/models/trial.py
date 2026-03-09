from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
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
    akc_event_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Registration close datetime (UTC). Computed from start_date but admin-overridable.
    reg_close_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Fee per class entry in cents
    fee_per_class_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<Trial id={self.id} body={self.governing_body!r} event_id={self.event_id}>"


class TrialEvent(Base):
    """A course+stock combination within a trial (e.g., 'Duck A', 'Sheep B')."""

    __tablename__ = "trial_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "Duck A"
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<TrialEvent id={self.id} name={self.name!r}>"


class TrialEventClass(Base):
    """A class level within a trial event (e.g., 'Started', 'Intermediate', 'Advanced')."""

    __tablename__ = "trial_event_classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trial_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)  # AKC: Started/Int/Adv; AHBA: I/II/III
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<TrialEventClass id={self.id} name={self.name!r}>"


class TrialEntry(Base):
    """One handler+dog entry submission for a trial weekend."""

    __tablename__ = "trial_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Optional link to logged-in user; None for mail-in entries
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Handler information (always stored on entry for audit trail)
    handler_name: Mapped[str] = mapped_column(String(255), nullable=False)
    handler_email: Mapped[str] = mapped_column(String(255), nullable=False)
    handler_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")

    # Dog information
    dog_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dog_breed: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dog_registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Payment
    total_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paypal_order_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Manual entry flag (set by admin)
    is_manual_entry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Verification email sent
    verification_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<TrialEntry id={self.id} handler={self.handler_name!r} dog={self.dog_name!r}>"


class TrialEntrySelection(Base):
    """Which class a dog is entered in for a specific trial event."""

    __tablename__ = "trial_entry_selections"
    __table_args__ = (
        UniqueConstraint("trial_entry_id", "trial_event_id", name="uq_entry_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trial_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trial_event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trial_events.id", ondelete="RESTRICT"), nullable=False
    )
    trial_event_class_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trial_event_classes.id", ondelete="RESTRICT"), nullable=False
    )
    # Call number assigned by admin after entry closes
    call_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<TrialEntrySelection entry={self.trial_entry_id} "
            f"event={self.trial_event_id} class={self.trial_event_class_id}>"
        )
