from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, String, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.registration_windows import format_et

# PayPal payments that may already have taken money.
ACTIVE_STATUSES = ("capturing", "pending")
PAYMENT_METHODS = ("paypal", "check", "cash", "other", "free")


class Payment(Base):
    """One payment, or promised check, for a Registration or TrialEntry.

    `is_paid` on the record stays the summary flag; these rows are the history.
    """

    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payments_record", "kind", "record_id"),
        # At most one outstanding check per record.
        Index(
            "uq_payments_open_check", "kind", "record_id", unique=True,
            sqlite_where=text("status = 'awaiting_check'"),
        ),
        # At most one PayPal capture in flight per record, so nobody is charged twice.
        Index(
            "uq_payments_active_paypal", "kind", "record_id", unique=True,
            sqlite_where=text("status IN ('capturing', 'pending')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # "registration" | "entry"
    # NULL once the record is deleted: SQLite reuses row ids, so a stale id could
    # attach this history to somebody else's new entry.
    record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    method: Mapped[str] = mapped_column(String(20), nullable=False)  # see PAYMENT_METHODS
    # Check: "awaiting_check" | "completed" | "void"
    # PayPal: "created" | "capturing" | "pending" | "completed" | "denied" | "refunded"
    #         | "review" (charged but doesn't match) | "void"
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    entry_cents: Mapped[int] = mapped_column(Integer, nullable=False)  # price when created
    processing_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    received_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)  # check #, etc.
    # Our unguessable id for the payment, sent to PayPal as custom_id.
    public_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    paypal_order_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Name and email as text, so history survives the user being deleted.
    recorded_by: Mapped[str | None] = mapped_column(String(520), nullable=True)
    # An admin needs to look at this (refund, double payment, mismatch).
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def created_et(self) -> str:
        return format_et(self.created_at)


class PaypalEvent(Base):
    """A webhook event that has been processed, so a repeat is ignored."""

    __tablename__ = "paypal_events"

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
