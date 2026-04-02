"""Consent record — proof that a user opted in to receive SMS."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    phone: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    agreed_to_terms: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_in_method: Mapped[str] = mapped_column(String, default="web_form")  # web_form / sms_keyword
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def __repr__(self):
        return f"<Consent {self.phone} at {self.created_at}>"
