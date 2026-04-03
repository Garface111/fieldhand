"""Contractor profile — the account owner."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, Text, ForeignKey
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
    license_expiration: Mapped[str | None] = mapped_column(String, nullable=True)  # YYYY-MM-DD
    license_classification: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. C-10, Master Plumber
    business_address: Mapped[str | None] = mapped_column(String, nullable=True)
    ein: Mapped[str | None] = mapped_column(String, nullable=True)  # EIN or SSN last 4
    business_structure: Mapped[str | None] = mapped_column(String, nullable=True)  # sole_prop/llc/s_corp/c_corp
    gl_carrier: Mapped[str | None] = mapped_column(String, nullable=True)
    gl_policy_number: Mapped[str | None] = mapped_column(String, nullable=True)
    gl_expiration: Mapped[str | None] = mapped_column(String, nullable=True)  # YYYY-MM-DD
    wc_carrier: Mapped[str | None] = mapped_column(String, nullable=True)  # workers comp carrier
    wc_policy: Mapped[str | None] = mapped_column(String, nullable=True)
    wc_expiration: Mapped[str | None] = mapped_column(String, nullable=True)
    wc_exempt: Mapped[bool] = mapped_column(default=False)  # has WC exemption certificate
    wc_exempt_number: Mapped[str | None] = mapped_column(String, nullable=True)
    insurance_agent_name: Mapped[str | None] = mapped_column(String, nullable=True)
    insurance_agent_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    service_area: Mapped[str | None] = mapped_column(String, nullable=True)  # zip codes or city names
    onboarding_phase: Mapped[int] = mapped_column(default=0)  # 0=not started, 1=done phase1, 2=done phase2, 3=complete
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
    # Context pinning — active job the agent assumes follow-ups belong to
    pinned_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)

    # Agent self-knowledge — updatable by the agent at any time
    agent_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # e.g. "Client Smith always pays late. Ford prefers quotes rounded to nearest $50."

    # Contractor-set behavior rules — what the contractor has asked the agent to do/not do
    custom_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    # e.g. "Always add 10% buffer to material estimates. Never send invoices on Sundays."

    # Personal context — things the contractor has shared about themselves
    persona_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # e.g. "Has a bad knee — prefers jobs under 2 stories. Wife's name is Maria."

    clients: Mapped[list["Client"]] = relationship("Client", back_populates="contractor")  # noqa
    jobs: Mapped[list["Job"]] = relationship("Job", back_populates="contractor", foreign_keys="Job.contractor_id")  # noqa
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="contractor")  # noqa

    def __repr__(self):
        return f"<Contractor {self.name} ({self.phone})>"
