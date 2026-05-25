from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # Nullable so admin-created "unregistered" placeholder users can exist
    # without a usable login until the person claims the account.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")  # 'user' | 'admin'
    is_trial_secretary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # True for admin-created placeholders. Cannot log in until claimed by signup.
    is_unregistered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    state_province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")  # ISO 3166-1 alpha-2
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"
