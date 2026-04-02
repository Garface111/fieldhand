"""
Proactive monitoring tasks — runs on schedule via Celery.
Alerts the contractor about things they need to know.
"""
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from src.database import SessionLocal
from src.models import Contractor, Job, JobStatus, Invoice, InvoiceStatus
from src.memory import Memory
from dotenv import load_dotenv

load_dotenv()


def send_sms(to: str, body: str):
    """Send an SMS via Twilio."""
    from twilio.rest import Client
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    if not all([account_sid, auth_token, from_number]):
        print(f"[SMS to {to}]: {body}")
        return
    twilio = Client(account_sid, auth_token)
    twilio.messages.create(to=to, from_=from_number, body=body)


def check_all_contractors():
    """Run all proactive checks for all contractors."""
    db = SessionLocal()
    try:
        contractors = db.query(Contractor).filter(Contractor.onboarding_complete == True).all()
        for contractor in contractors:
            alerts = generate_alerts(contractor, db)
            for alert in alerts:
                send_sms(contractor.phone, alert)
    finally:
        db.close()


def generate_alerts(contractor: Contractor, db: Session) -> list[str]:
    """Generate any proactive alerts that need to go out."""
    alerts = []
    memory = Memory(db, contractor.id)
    now = datetime.now(timezone.utc)

    # 1. Jobs marked complete but no invoice after 3 days
    complete_jobs = (
        db.query(Job)
        .filter(
            Job.contractor_id == contractor.id,
            Job.status == JobStatus.COMPLETE,
        )
        .all()
    )
    for job in complete_jobs:
        has_invoice = bool(db.query(Invoice).filter(Invoice.job_id == job.id).first())
        if not has_invoice:
            days_since = (now - job.completed_at).days if job.completed_at else 0
            if days_since >= 3:
                alerts.append(
                    f"Hey {contractor.name.split()[0]}, "
                    f"'{job.title}' has been done for {days_since} days but no invoice sent. "
                    f"Want me to generate one? Text 'invoice {job.title[:20]}' to do it."
                )

    # 2. Overdue invoices
    overdue_7 = memory.get_overdue_invoices(min_days=7)
    overdue_14 = memory.get_overdue_invoices(min_days=14)
    overdue_30 = memory.get_overdue_invoices(min_days=30)

    if overdue_30:
        total = sum(i.amount for i in overdue_30)
        alerts.append(
            f"FINAL NOTICE: {len(overdue_30)} invoice(s) are 30+ days overdue "
            f"totaling ${total:,.2f}. Want me to send final notices?"
        )
    elif overdue_14:
        new_ones = [i for i in overdue_14 if i not in overdue_30]
        if new_ones:
            total = sum(i.amount for i in new_ones)
            alerts.append(
                f"Reminder: {len(new_ones)} invoice(s) are 14+ days overdue "
                f"totaling ${total:,.2f}. Want me to send reminders?"
            )
    elif overdue_7:
        new_ones = [i for i in overdue_7 if i not in overdue_14]
        if new_ones:
            total = sum(i.amount for i in new_ones)
            alerts.append(
                f"FYI: {len(new_ones)} invoice(s) just hit 7 days unpaid "
                f"totaling ${total:,.2f}. Want me to send friendly reminders?"
            )

    # 3. Jobs trending over budget
    active_jobs = (
        db.query(Job)
        .filter(
            Job.contractor_id == contractor.id,
            Job.status == JobStatus.ACTIVE,
        )
        .all()
    )
    for job in active_jobs:
        if job.is_over_budget and job.quoted_amount:
            alerts.append(
                f"⚠️ '{job.title}' is at {job.budget_used_pct:.0f}% of the "
                f"${job.quoted_amount:,.0f} budget. Consider issuing a change order."
            )

    # 4. Stale leads (no activity 14 days)
    stale_leads = (
        db.query(Job)
        .filter(
            Job.contractor_id == contractor.id,
            Job.status == JobStatus.LEAD,
            Job.created_at <= now - timedelta(days=14),
        )
        .all()
    )
    if stale_leads:
        names = ", ".join(f"'{j.title[:20]}'" for j in stale_leads[:3])
        alerts.append(
            f"You have {len(stale_leads)} stale lead(s) with no activity in 14+ days: {names}. "
            f"Follow up or cancel them?"
        )

    return alerts


def morning_briefing(contractor: Contractor, db: Session) -> str:
    """
    Generate a morning briefing SMS for the contractor.
    Compact, useful, daily.
    """
    memory = Memory(db, contractor.id)
    now = datetime.now(timezone.utc)
    name = contractor.name.split()[0]

    active = db.query(Job).filter(
        Job.contractor_id == contractor.id,
        Job.status == JobStatus.ACTIVE,
    ).count()

    outstanding = memory.get_outstanding_total()
    monthly = memory.get_monthly_income()
    overdue_count = len(memory.get_overdue_invoices(min_days=7))

    lines = [f"Good morning {name} ☀️"]
    lines.append(f"• {active} active job(s)")
    lines.append(f"• ${outstanding:,.0f} outstanding")
    lines.append(f"• ${monthly:,.0f} collected this month")
    if overdue_count:
        lines.append(f"• ⚠️ {overdue_count} invoice(s) overdue")

    return "\n".join(lines)
