"""Audit log — every action the agent takes, recorded permanently."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contractor_id: Mapped[str] = mapped_column(ForeignKey("contractors.id"), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    # e.g. "invoice_sent", "email_drafted", "job_created", "expense_logged"
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    # e.g. "Invoice $2,800 to Sarah Mitchell"
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # full detail if needed
    channel: Mapped[str] = mapped_column(String, default="sms")
    # sms / email / system / web
    initiated_by: Mapped[str] = mapped_column(String, default="agent")
    # agent / contractor / system
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def __repr__(self):
        return f"<AuditLog [{self.action}] {self.subject}>"
