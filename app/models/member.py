from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Optional link to a user account
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    state_province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")

    # Year the member paid for — informational only (the year on their check).
    # The source of truth for member-vs-not is the `status` column below.
    membership_year: Mapped[int] = mapped_column(Integer, nullable=False)

    # Membership status: "pending" (paid, not verified), "member" (active),
    # or "expired" (was a member). Absence of a Member row = "no status".
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="member")

    # Statuses that get the member pricing tier on registrations.
    ACTIVE_STATUSES = ("pending", "member")
    ALLOWED_STATUSES = ("pending", "member", "expired")

    def __repr__(self) -> str:
        return f"<Member id={self.id} name={self.name!r} status={self.status!r}>"
