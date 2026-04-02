"""Job — a piece of work for a client. Full state machine."""
import uuid
from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import String, Float, DateTime, Text, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    LEAD = "lead"
    QUOTED = "quoted"
    ACTIVE = "active"
    COMPLETE = "complete"
    PAID = "paid"
    CANCELLED = "cancelled"


# Valid transitions
VALID_TRANSITIONS = {
    JobStatus.LEAD:     [JobStatus.QUOTED, JobStatus.ACTIVE, JobStatus.CANCELLED],
    JobStatus.QUOTED:   [JobStatus.ACTIVE, JobStatus.LEAD, JobStatus.CANCELLED],
    JobStatus.ACTIVE:   [JobStatus.COMPLETE, JobStatus.CANCELLED],
    JobStatus.COMPLETE: [JobStatus.PAID, JobStatus.ACTIVE],
    JobStatus.PAID:     [],
    JobStatus.CANCELLED: [],
}


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contractor_id: Mapped[str] = mapped_column(ForeignKey("contractors.id"), nullable=False)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[JobStatus] = mapped_column(SAEnum(JobStatus), default=JobStatus.LEAD)
    quoted_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost: Mapped[float] = mapped_column(Float, default=0.0)
    labor_hours: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contractor: Mapped["Contractor"] = relationship("Contractor", back_populates="jobs")  # noqa
    client: Mapped["Client | None"] = relationship("Client", back_populates="jobs")  # noqa
    expenses: Mapped[list["Expense"]] = relationship("Expense", back_populates="job")  # noqa
    invoices: Mapped[list["Invoice"]] = relationship("Invoice", back_populates="job")  # noqa
    documents: Mapped[list["Document"]] = relationship("Document", back_populates="job")  # noqa

    def transition_to(self, new_status: JobStatus) -> bool:
        """Attempt a status transition. Returns True if successful."""
        allowed = VALID_TRANSITIONS.get(self.status, [])
        if new_status in allowed:
            if new_status == JobStatus.COMPLETE:
                self.completed_at = utcnow()
            self.status = new_status
            return True
        return False

    @property
    def budget_used_pct(self) -> float | None:
        if self.quoted_amount and self.quoted_amount > 0:
            return (self.actual_cost / self.quoted_amount) * 100
        return None

    @property
    def is_over_budget(self) -> bool:
        pct = self.budget_used_pct
        return pct is not None and pct >= 90

    def __repr__(self):
        return f"<Job {self.title} [{self.status}]>"
