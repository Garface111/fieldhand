"""Invoice — tracks what's been sent and paid."""
import uuid
from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import String, Float, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    OVERDUE = "overdue"
    PAID = "paid"
    VOID = "void"


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    contractor_id: Mapped[str] = mapped_column(ForeignKey("contractors.id"), nullable=False)
    stripe_invoice_id: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(SAEnum(InvoiceStatus), default=InvoiceStatus.DRAFT)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["Job"] = relationship("Job", back_populates="invoices")  # noqa

    def __repr__(self):
        return f"<Invoice ${self.amount:.2f} [{self.status}]>"
