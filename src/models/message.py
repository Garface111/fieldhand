"""Message — conversation history for the agent."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contractor_id: Mapped[str] = mapped_column(ForeignKey("contractors.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # user / assistant / system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String, default="sms")  # sms / email / web
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contractor: Mapped["Contractor"] = relationship("Contractor", back_populates="messages")  # noqa

    def __repr__(self):
        return f"<Message [{self.role}] {self.content[:40]}>"
