"""
Memory layer — gives the agent its long-term knowledge.

Two kinds of memory:
  1. Structured (SQL) — clients, jobs, expenses, invoices. Precise queries.
  2. Semantic (vector) — conversation history + notes. Fuzzy recall.

For local/dev we skip the vector store and use recency-based recall.
In prod, swap _semantic_search for pgvector or Pinecone.
"""
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from src.models import (
    Contractor, Client, Job, JobStatus,
    Expense, Invoice, InvoiceStatus, Message
)


def _as_utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware UTC. Handles naive datetimes from SQLite."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Memory:
    def __init__(self, db: Session, contractor_id: str):
        self.db = db
        self.contractor_id = contractor_id

    # ------------------------------------------------------------------ #
    # MESSAGES
    # ------------------------------------------------------------------ #

    def store_message(self, role: str, content: str, channel: str = "sms") -> Message:
        msg = Message(
            contractor_id=self.contractor_id,
            role=role,
            content=content,
            channel=channel,
        )
        self.db.add(msg)
        self.db.commit()
        self.db.refresh(msg)
        return msg

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        msgs = (
            self.db.query(Message)
            .filter(Message.contractor_id == self.contractor_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{"role": m.role, "content": m.content} for m in reversed(msgs)]

    # ------------------------------------------------------------------ #
    # CONTRACTOR CONTEXT (full snapshot for the LLM)
    # ------------------------------------------------------------------ #

    def get_contractor(self) -> Contractor | None:
        return self.db.query(Contractor).filter(Contractor.id == self.contractor_id).first()

    def get_context_snapshot(self) -> dict:
        """
        Build a rich context dict the agent injects into every prompt.
        Keeps it concise — we want tokens spent on reasoning, not data.
        """
        contractor = self.get_contractor()
        if not contractor:
            return {}

        # Active + recent jobs
        active_jobs = (
            self.db.query(Job)
            .filter(
                Job.contractor_id == self.contractor_id,
                Job.status.in_([JobStatus.LEAD, JobStatus.QUOTED, JobStatus.ACTIVE, JobStatus.COMPLETE]),
            )
            .order_by(Job.created_at.desc())
            .limit(10)
            .all()
        )

        # Overdue invoices
        overdue = (
            self.db.query(Invoice)
            .filter(
                Invoice.contractor_id == self.contractor_id,
                Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.OVERDUE]),
            )
            .all()
        )

        # Recent clients
        recent_clients = (
            self.db.query(Client)
            .filter(Client.contractor_id == self.contractor_id)
            .order_by(Client.created_at.desc())
            .limit(10)
            .all()
        )

        # Monthly income (paid invoices this month)
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_paid = (
            self.db.query(Invoice)
            .filter(
                Invoice.contractor_id == self.contractor_id,
                Invoice.status == InvoiceStatus.PAID,
                Invoice.paid_at >= month_start,
            )
            .all()
        )

        return {
            "contractor": {
                "name": contractor.name,
                "business_name": contractor.business_name,
                "trade": contractor.trade,
                "labor_rate": contractor.labor_rate,
                "markup_pct": contractor.markup_pct,
                "invoice_terms": contractor.invoice_terms,
                "license_no": contractor.license_no,
            },
            "active_jobs": [
                {
                    "id": j.id,
                    "title": j.title,
                    "status": j.status.value,
                    "client": j.client.name if j.client else "Unknown",
                    "quoted": j.quoted_amount,
                    "actual_cost": j.actual_cost,
                    "budget_used_pct": round(j.budget_used_pct or 0, 1),
                    "is_over_budget": j.is_over_budget,
                    "address": j.address,
                }
                for j in active_jobs
            ],
            "overdue_invoices": [
                {
                    "id": inv.id,
                    "job_id": inv.job_id,
                    "amount": inv.amount,
                    "status": inv.status.value,
                    "sent_at": inv.sent_at.isoformat() if inv.sent_at else None,
                    "days_overdue": (now - _as_utc(inv.sent_at)).days if inv.sent_at else None,
                }
                for inv in overdue
            ],
            "recent_clients": [
                {
                    "id": c.id,
                    "name": c.name,
                    "phone": c.phone,
                    "email": c.email,
                    "payment_behavior": c.payment_behavior,
                    "notes": c.notes,
                }
                for c in recent_clients
            ],
            "monthly_income": sum(i.amount for i in monthly_paid),
            "total_outstanding": sum(i.amount for i in overdue),
        }

    # ------------------------------------------------------------------ #
    # CLIENTS
    # ------------------------------------------------------------------ #

    def find_client(self, name: str) -> Client | None:
        """Fuzzy name search — returns best match or None."""
        name_lower = name.lower()
        clients = (
            self.db.query(Client)
            .filter(Client.contractor_id == self.contractor_id)
            .all()
        )
        for c in clients:
            if name_lower in c.name.lower() or c.name.lower() in name_lower:
                return c
        return None

    def create_client(self, name: str, phone: str = None, email: str = None,
                      address: str = None, notes: str = None,
                      referral_source: str = None) -> Client:
        client = Client(
            contractor_id=self.contractor_id,
            name=name,
            phone=phone,
            email=email,
            address=address,
            notes=notes,
            referral_source=referral_source,
        )
        self.db.add(client)
        self.db.commit()
        self.db.refresh(client)
        return client

    # ------------------------------------------------------------------ #
    # JOBS
    # ------------------------------------------------------------------ #

    def find_job(self, title_hint: str) -> Job | None:
        """Find a job by partial title match."""
        hint_lower = title_hint.lower()
        jobs = (
            self.db.query(Job)
            .filter(
                Job.contractor_id == self.contractor_id,
                Job.status.notin_([JobStatus.PAID, JobStatus.CANCELLED]),
            )
            .all()
        )
        for j in jobs:
            if hint_lower in j.title.lower() or j.title.lower() in hint_lower:
                return j
        return None

    def create_job(self, title: str, client_id: str = None, description: str = None,
                   address: str = None, quoted_amount: float = None) -> Job:
        job = Job(
            contractor_id=self.contractor_id,
            client_id=client_id,
            title=title,
            description=description,
            address=address,
            quoted_amount=quoted_amount,
            status=JobStatus.LEAD,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self.db.query(Job).filter(Job.id == job_id).first()

    # ------------------------------------------------------------------ #
    # EXPENSES
    # ------------------------------------------------------------------ #

    def log_expense(self, job_id: str, description: str, amount: float,
                    category: str = "materials", vendor: str = None,
                    receipt_url: str = None) -> Expense:
        expense = Expense(
            job_id=job_id,
            contractor_id=self.contractor_id,
            description=description,
            amount=amount,
            category=category,
            vendor=vendor,
            receipt_url=receipt_url,
        )
        self.db.add(expense)

        # Update job actual_cost
        job = self.get_job(job_id)
        if job:
            job.actual_cost = (job.actual_cost or 0) + amount

        self.db.commit()
        self.db.refresh(expense)
        return expense

    # ------------------------------------------------------------------ #
    # FINANCIAL QUERIES
    # ------------------------------------------------------------------ #

    def get_outstanding_total(self) -> float:
        invoices = (
            self.db.query(Invoice)
            .filter(
                Invoice.contractor_id == self.contractor_id,
                Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.OVERDUE]),
            )
            .all()
        )
        return sum(i.amount for i in invoices)

    def get_monthly_income(self, year: int = None, month: int = None) -> float:
        now = datetime.now(timezone.utc)
        y = year or now.year
        m = month or now.month
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        if m == 12:
            end = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(y, m + 1, 1, tzinfo=timezone.utc)
        invoices = (
            self.db.query(Invoice)
            .filter(
                Invoice.contractor_id == self.contractor_id,
                Invoice.status == InvoiceStatus.PAID,
                Invoice.paid_at >= start,
                Invoice.paid_at < end,
            )
            .all()
        )
        return sum(i.amount for i in invoices)

    def get_overdue_invoices(self, min_days: int = 0) -> list[Invoice]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=min_days)
        invoices = (
            self.db.query(Invoice)
            .filter(
                Invoice.contractor_id == self.contractor_id,
                Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.OVERDUE]),
            )
            .order_by(Invoice.sent_at)
            .all()
        )
        # Compare with tz-aware cutoff, normalizing SQLite naive datetimes
        return [inv for inv in invoices if inv.sent_at and _as_utc(inv.sent_at) <= cutoff]
