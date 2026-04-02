"""Expense — a cost logged against a job."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


EXPENSE_CATEGORIES = [
    "materials",
    "labor",
    "subcontractor",
    "fuel",
    "tools",
    "permits",
    "other",
]


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    contractor_id: Mapped[str] = mapped_column(ForeignKey("contractors.id"), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String, default="materials")
    vendor: Mapped[str | None] = mapped_column(String, nullable=True)
    receipt_url: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["Job | None"] = relationship("Job", back_populates="expenses")  # noqa

    def __repr__(self):
        return f"<Expense ${self.amount:.2f} — {self.description}>"
