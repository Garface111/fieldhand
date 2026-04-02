"""Magic link tokens for passwordless dashboard login."""
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class MagicLink(Base):
    __tablename__ = "magic_links"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contractor_id: Mapped[str] = mapped_column(ForeignKey("contractors.id"), nullable=False)
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False,
                                        default=lambda: secrets.token_urlsafe(48))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc) + timedelta(hours=24)
    )

    @property
    def is_valid(self) -> bool:
        now = datetime.now(timezone.utc)
        exp = self.expires_at.replace(tzinfo=timezone.utc) if self.expires_at.tzinfo is None else self.expires_at
        return not self.used and now < exp


import secrets  # noqa — after class definition
