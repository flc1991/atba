from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

DOCUMENT_TYPES = ("premium_list", "schedule", "results", "other")


class EventPdf(Base):
    """Tracks uploaded PDFs associated with an event."""

    __tablename__ = "event_pdfs"
    __table_args__ = (
        UniqueConstraint("event_id", "governing_body", "document_type", name="uq_event_pdf"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    governing_body: Mapped[str] = mapped_column(String(10), nullable=False)  # 'AKC' | 'AHBA' | 'ATBA'
    document_type: Mapped[str] = mapped_column(String(30), nullable=False)   # see DOCUMENT_TYPES
    filename: Mapped[str] = mapped_column(String(255), nullable=False)        # stored name on disk

    def __repr__(self) -> str:
        return f"<EventPdf id={self.id} event={self.event_id} type={self.document_type!r}>"
