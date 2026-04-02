"""Contractor profile — the account owner."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Contractor(Base):
    __tablename__ = "contractors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    business_name: Mapped[str | None] = mapped_column(String, nullable=True)
    trade: Mapped[str | None] = mapped_column(String, nullable=True)
    license_no: Mapped[str | None] = mapped_column(String, nullable=True)
    insurance_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    labor_rate: Mapped[float] = mapped_column(Float, default=85.0)
    markup_pct: Mapped[float] = mapped_column(Float, default=20.0)
    invoice_terms: Mapped[str] = mapped_column(String, default="Net 15")
    stripe_customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    gmail_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_email: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str] = mapped_column(String, default="America/Chicago")
    onboarding_complete: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    clients: Mapped[list["Client"]] = relationship("Client", back_populates="contractor")  # noqa
    jobs: Mapped[list["Job"]] = relationship("Job", back_populates="contractor")  # noqa
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="contractor")  # noqa

    def __repr__(self):
        return f"<Contractor {self.name} ({self.phone})>"
