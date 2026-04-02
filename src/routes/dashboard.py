"""
Dashboard API — read-only views for the web dashboard and accountant portal.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from src.database import get_db
from src.models import Contractor, Job, JobStatus, Invoice, InvoiceStatus, Client, Expense
from src.memory import Memory
from datetime import datetime, timezone

router = APIRouter(prefix="/api")


def get_contractor_or_404(contractor_id: str, db: Session) -> Contractor:
    c = db.query(Contractor).filter(Contractor.id == contractor_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Contractor not found")
    return c


@router.get("/contractors/{contractor_id}/overview")
def overview(contractor_id: str, db: Session = Depends(get_db)):
    """Main dashboard overview."""
    contractor = get_contractor_or_404(contractor_id, db)
    memory = Memory(db, contractor_id)
    return memory.get_context_snapshot()


@router.get("/contractors/{contractor_id}/jobs")
def list_jobs(contractor_id: str, status: str = None, db: Session = Depends(get_db)):
    get_contractor_or_404(contractor_id, db)
    query = db.query(Job).filter(Job.contractor_id == contractor_id)
    if status:
        try:
            query = query.filter(Job.status == JobStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    jobs = query.order_by(Job.created_at.desc()).all()
    return [
        {
            "id": j.id,
            "title": j.title,
            "status": j.status.value,
            "client": j.client.name if j.client else None,
            "address": j.address,
            "quoted_amount": j.quoted_amount,
            "actual_cost": j.actual_cost,
            "budget_used_pct": j.budget_used_pct,
            "is_over_budget": j.is_over_budget,
            "created_at": j.created_at.isoformat(),
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        }
        for j in jobs
    ]


@router.get("/contractors/{contractor_id}/invoices")
def list_invoices(contractor_id: str, db: Session = Depends(get_db)):
    get_contractor_or_404(contractor_id, db)
    invoices = (
        db.query(Invoice)
        .filter(Invoice.contractor_id == contractor_id)
        .order_by(Invoice.created_at.desc())
        .all()
    )
    return [
        {
            "id": inv.id,
            "job_title": inv.job.title if inv.job else None,
            "amount": inv.amount,
            "status": inv.status.value,
            "stripe_invoice_id": inv.stripe_invoice_id,
            "sent_at": inv.sent_at.isoformat() if inv.sent_at else None,
            "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
        }
        for inv in invoices
    ]


@router.get("/contractors/{contractor_id}/clients")
def list_clients(contractor_id: str, db: Session = Depends(get_db)):
    get_contractor_or_404(contractor_id, db)
    clients = (
        db.query(Client)
        .filter(Client.contractor_id == contractor_id)
        .order_by(Client.created_at.desc())
        .all()
    )
    return [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "email": c.email,
            "address": c.address,
            "notes": c.notes,
            "referral_source": c.referral_source,
            "payment_behavior": c.payment_behavior,
            "job_count": len(c.jobs),
        }
        for c in clients
    ]


@router.get("/contractors/{contractor_id}/finances")
def finances(contractor_id: str, db: Session = Depends(get_db)):
    """Financial summary for accountant portal."""
    get_contractor_or_404(contractor_id, db)
    memory = Memory(db, contractor_id)

    now = datetime.now(timezone.utc)
    monthly = {
        m: memory.get_monthly_income(year=now.year, month=m)
        for m in range(1, now.month + 1)
    }

    expenses = (
        db.query(Expense)
        .filter(Expense.contractor_id == contractor_id)
        .order_by(Expense.created_at.desc())
        .all()
    )

    by_category: dict[str, float] = {}
    for e in expenses:
        by_category[e.category] = by_category.get(e.category, 0) + e.amount

    return {
        "monthly_income": monthly,
        "total_income_ytd": sum(monthly.values()),
        "total_outstanding": memory.get_outstanding_total(),
        "expenses_by_category": by_category,
        "total_expenses_ytd": sum(by_category.values()),
    }
