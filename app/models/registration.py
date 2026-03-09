from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

PRICING_TIERS = ("pre_member", "pre_general", "late")


class Registration(Base):
    """Fun Run or Smart Dog Day registration."""

    __tablename__ = "registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Registrant information
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")

    dog_name: Mapped[str] = mapped_column(String(255), nullable=False)

    pricing_tier: Mapped[str] = mapped_column(String(20), nullable=False)  # see PRICING_TIERS
    fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    paypal_order_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_manual_entry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<Registration id={self.id} event={self.event_id} name={self.name!r}>"
