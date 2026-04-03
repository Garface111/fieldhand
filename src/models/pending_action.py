"""
PendingAction — stores client-facing actions awaiting Y/N confirmation.

When the agent wants to send a quote, invoice, email, or change order,
it stores a PendingAction and returns a confirmation prompt instead of
firing immediately. The next 'Y' or 'YES' from the contractor triggers it.
"""
import uuid
import json
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contractor_id: Mapped[str] = mapped_column(ForeignKey("contractors.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    # action_type: send_quote | send_invoice | send_email | send_change_order | send_picklist
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON blob
    summary: Mapped[str] = mapped_column(Text, nullable=False)  # Human-readable description shown to contractor
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved: Mapped[bool] = mapped_column(default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)  # "approved" | "rejected"

    def get_payload(self) -> dict:
        return json.loads(self.payload)

    def __repr__(self):
        return f"<PendingAction {self.action_type} [{'resolved' if self.resolved else 'pending'}]>"
