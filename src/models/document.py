"""Document — quotes, change orders, lien waivers, contracts."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    contractor_id: Mapped[str] = mapped_column(ForeignKey("contractors.id"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String, nullable=False)  # quote/change_order/lien_waiver/contract
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    hellosign_id: Mapped[str | None] = mapped_column(String, nullable=True)
    signed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["Job | None"] = relationship("Job", back_populates="documents")  # noqa

    def __repr__(self):
        return f"<Document [{self.doc_type}] signed={self.signed}>"
