from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Dog(Base):
    """Dog saved to a user's account for quick re-use on entry forms."""

    __tablename__ = "dogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Shared (AKC + AHBA)
    dog_name: Mapped[str] = mapped_column(String(255), nullable=False)  # registered name
    dog_breed: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dog_sex: Mapped[str | None] = mapped_column(String(1), nullable=True)   # M / F
    dog_sire: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dog_dam: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dog_breeder: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # AKC-specific
    dog_call_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    akc_number_type: Mapped[str | None] = mapped_column(String(20), nullable=True)   # AKC | PAL_ILP | Foreign
    akc_registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    akc_foreign_country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dog_dob: Mapped[date | None] = mapped_column(Date, nullable=True)

    # AHBA-specific
    ahba_registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dog_place_of_birth: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def __repr__(self) -> str:
        return f"<Dog id={self.id} user={self.user_id} name={self.dog_name!r}>"
