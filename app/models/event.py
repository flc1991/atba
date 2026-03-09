from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

EVENT_TYPES = ("trial", "smart_dog_day", "fun_run", "meeting", "picnic", "other")


class Event(Base):
    """Top-level calendar event (e.g., a trial weekend, a fun run day)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)  # see EVENT_TYPES

    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Fees (stored in cents to avoid floating-point issues) ---
    # Fun Run / Smart Dog Day pricing tiers
    fee_pre_member_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee_pre_general_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee_late_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pre_entry_close_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<Event id={self.id} title={self.title!r} type={self.event_type!r}>"
