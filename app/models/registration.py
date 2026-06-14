from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Standard fun run event options (AKC + AHBA)
FUN_RUN_EVENTS = [
    "Duck A", "Duck B", "Goose B", "Sheep A", "Sheep B", "Sheep C", "Sheep D",
    "Duck or Goose Instinct Test", "Duck or Goose Herding Test", "Duck or Goose Pre-Trial Test",
    "Sheep Instinct Test", "Sheep Herding Test", "Sheep Pre-Trial Test",
    "5 Ducks HTD", "4-5 Geese HTD", "5 Ducks HTAD", "25 Sheep + RLF", "10+ Sheep HRD",
    "3-5 Sheep HTD", "3-5 Sheep HTAD",
    "5 Ducks JHD", "5 Geese JHD", "5 Sheep JHD",
]

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
    # Free-text payment reference: PayPal transaction id for online payments,
    # or a check / cash reference entered by an admin for manual entries.
    payment_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_manual_entry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<Registration id={self.id} event={self.event_id} name={self.name!r}>"


class RegistrationDog(Base):
    """One dog entered in a Fun Run registration (supports up to 4 event selections)."""

    __tablename__ = "registration_dogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    registration_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dog_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dog_breed: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_1: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_2: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_3: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_4: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_1_judged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_2_judged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_3_judged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_4_judged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    @property
    def events(self) -> list[str]:
        return [e for e in [self.event_1, self.event_2, self.event_3, self.event_4] if e]

    @property
    def entries(self) -> list[tuple[str, bool]]:
        """Each non-empty entry as (event_name, judged_flag) pairs."""
        pairs = [
            (self.event_1, self.event_1_judged),
            (self.event_2, self.event_2_judged),
            (self.event_3, self.event_3_judged),
            (self.event_4, self.event_4_judged),
        ]
        return [(name, judged) for name, judged in pairs if name]

    def __repr__(self) -> str:
        return f"<RegistrationDog id={self.id} reg={self.registration_id} dog={self.dog_name!r}>"
