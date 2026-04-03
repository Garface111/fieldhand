"""Subcontractor — a person or company the contractor hires for specific work."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Subcontractor(Base):
    __tablename__ = "subcontractors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contractor_id: Mapped[str] = mapped_column(String, ForeignKey("contractors.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    trade: Mapped[str | None] = mapped_column(String, nullable=True)   # e.g. drywall, concrete, painting
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    company_name: Mapped[str | None] = mapped_column(String, nullable=True)
    license_no: Mapped[str | None] = mapped_column(String, nullable=True)
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)    # hourly or per-project rate
    rate_type: Mapped[str] = mapped_column(String, default="project")   # hourly / project / sqft
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(nullable=True)           # 1-5 internal rating
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def __repr__(self):
        return f"<Subcontractor {self.name} ({self.trade})>"
