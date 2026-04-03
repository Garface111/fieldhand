"""
Proactive monitoring tasks — runs on schedule via Celery or cron.

v2 changes:
  - Autonomous dunning: actually sends emails to clients at 3/7/14/30 days
    without needing contractor to ask
  - EOD P&L summary: 6pm daily breakdown (jobs, quotes, collections, permits)
  - Morning briefing: unchanged but improved
  - All dunning emails go through contractor's Gmail
"""
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from src.database import SessionLocal
from src.models import Contractor, Job, JobStatus, Invoice, InvoiceStatus, Client
from src.memory import Memory
from dotenv import load_dotenv

load_dotenv()


def send_sms(to: str, body: str):
    from twilio.rest import Client
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    if not all([account_sid, auth_token, from_number]):
        print(f"[SMS to {to}]: {body}")
        return
    twilio = Client(account_sid, auth_token)
    twilio.messages.create(to=to, from_=from_number, body=body)


def send_gmail(contractor: Contractor, to: str, subject: str, body: str):
    """Send email via contractor's connected Gmail account."""
    if not contractor.gmail_refresh_token:
        print(f"[Email to {to}]: {subject}")
        return False
    import asyncio
    from src.email_client import GmailClient
    gmail = GmailClient(contractor.gmail_refresh_token)
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(gmail.send(to=to, subject=subject, body=body))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(gmail.send(to=to, subject=subject, body=body))
        loop.close()
    return True


# ── Dunning messages by tier ─────────────────────────────────────────────────

def _dunning_message(contractor: Contractor, client: Client, job: Job,
                     invoice: Invoice, days_overdue: int) -> tuple[str, str]:
    """Returns (subject, body) for a dunning email to the client."""
    first = contractor.name.split()[0]
    client_first = client.name.split()[0] if client else "there"
    amount = f"${invoice.amount:,.2f}"
    business = contractor.business_name or contractor.name
    phone = contractor.phone

    if days_overdue <= 3:
        subject = f"Quick reminder — Invoice for {job.title}"
        body = (
            f"Hi {client_first},\n\n"
            f"Just a friendly reminder that your invoice for '{job.title}' "
            f"({amount}) is now due.\n\n"
            f"If you have any questions or need to make payment arrangements, "
            f"please don't hesitate to reach out.\n\n"
            f"Thanks,\n{first}\n{business}\n{phone}"
        )
    elif days_overdue <= 7:
        subject = f"Invoice overdue — {job.title} ({amount})"
        body = (
            f"Hi {client_first},\n\n"
            f"This is a follow-up on your outstanding invoice for '{job.title}' "
            f"for {amount}, which is now {days_overdue} days past due.\n\n"
            f"Please arrange payment at your earliest convenience. "
            f"If there's an issue, let me know and we can work something out.\n\n"
            f"Thanks,\n{first}\n{business}\n{phone}"
        )
    elif days_overdue <= 14:
        subject = f"OVERDUE: {job.title} — {amount} past due {days_overdue} days"
        body = (
            f"Hi {client_first},\n\n"
            f"Your invoice for '{job.title}' ({amount}) is now {days_overdue} days overdue. "
            f"I've reached out previously and haven't received payment.\n\n"
            f"Please make payment by the end of this week. "
            f"If you're experiencing difficulty, please contact me directly at {phone} "
            f"to discuss options.\n\n"
            f"Thanks,\n{first}\n{business}\n{phone}"
        )
    else:
        subject = f"FINAL NOTICE — {job.title} — {amount} — {days_overdue} days overdue"
        body = (
            f"Hi {client_first},\n\n"
            f"This is a final notice regarding the outstanding balance of {amount} "
            f"for work completed on '{job.title}', which is now {days_overdue} days overdue.\n\n"
            f"If payment is not received within 7 days, I will need to pursue further collection options.\n\n"
            f"Please contact me immediately at {phone}.\n\n"
            f"Regards,\n{first}\n{business}"
        )
    return subject, body


def _as_utc(dt: datetime) -> datetime:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ── Autonomous dunning ────────────────────────────────────────────────────────

def run_autonomous_dunning(db: Session, contractor: Contractor) -> list[str]:
    """
    For contractors with auto_followup enabled (or by default):
    Automatically send dunning emails to clients at 3, 7, 14, 30 days.
    Sends directly — no contractor approval needed.
    Returns list of actions taken.
    """
    now = datetime.now(timezone.utc)
    actions = []

    overdue_invoices = (
        db.query(Invoice)
        .filter(
            Invoice.contractor_id == contractor.id,
            Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.OVERDUE]),
        )
        .all()
    )

    for inv in overdue_invoices:
        if not inv.sent_at:
            continue
        days_overdue = (now - _as_utc(inv.sent_at)).days
        if days_overdue < 3:
            continue

        job = inv.job
        if not job:
            continue
        client = job.client
        if not client or not client.email:
            # No client email — SMS the contractor instead
            if days_overdue in (3, 7, 14, 30):
                msg = (
                    f"Invoice for '{job.title[:30]}' is {days_overdue} days overdue "
                    f"(${inv.amount:,.2f}). No client email on file — follow up manually."
                )
                send_sms(contractor.phone, msg)
                actions.append(f"SMS to contractor about {job.title} ({days_overdue}d overdue, no client email)")
            continue

        # Only send at specific day thresholds (not every day)
        if days_overdue not in (3, 7, 14, 30):
            # Check if we're within 1 day of a threshold
            if not any(abs(days_overdue - t) <= 1 for t in [3, 7, 14, 30]):
                continue

        subject, body = _dunning_message(contractor, client, job, inv, days_overdue)
        sent = send_gmail(contractor, client.email, subject, body)

        if sent:
            # Escalate invoice status
            if days_overdue >= 7 and inv.status == InvoiceStatus.SENT:
                inv.status = InvoiceStatus.OVERDUE
                db.commit()
            # Notify contractor that we sent a dunning email
            notify = (
                f"Auto-sent {days_overdue}d overdue notice to {client.name} "
                f"for '{job.title[:25]}' (${inv.amount:,.2f})"
            )
            send_sms(contractor.phone, notify)
            actions.append(notify)

    return actions


# ── EOD P&L Summary ──────────────────────────────────────────────────────────

def eod_summary(contractor: Contractor, db: Session) -> str:
    """
    Generate an end-of-day P&L summary text.
    Sent at 6pm local time.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    name = contractor.name.split()[0]
    memory = Memory(db, contractor.id)

    # Jobs completed today
    jobs_completed_today = (
        db.query(Job)
        .filter(
            Job.contractor_id == contractor.id,
            Job.status == JobStatus.COMPLETE,
            Job.completed_at >= today_start,
        )
        .all()
    )

    # Quotes sent today (invoices moved to DRAFT today — proxy for quotes sent)
    invoices_created_today = (
        db.query(Invoice)
        .filter(
            Invoice.contractor_id == contractor.id,
            Invoice.created_at >= today_start,
        )
        .count()
    )

    # Money collected today
    collected_today = sum(
        inv.amount
        for inv in db.query(Invoice)
        .filter(
            Invoice.contractor_id == contractor.id,
            Invoice.status == InvoiceStatus.PAID,
            Invoice.paid_at >= today_start,
        )
        .all()
    )

    # Active jobs count
    active_count = db.query(Job).filter(
        Job.contractor_id == contractor.id,
        Job.status == JobStatus.ACTIVE,
    ).count()

    # Overdue invoices
    overdue_total = memory.get_outstanding_total()
    overdue_count = len(memory.get_overdue_invoices(min_days=1))

    # Expenses logged today
    from src.models.expense import Expense
    expenses_today = (
        db.query(Expense)
        .filter(
            Expense.contractor_id == contractor.id,
            Expense.created_at >= today_start,
        )
        .all()
    )
    expenses_total = sum(e.amount for e in expenses_today)

    lines = [f"End of day, {name} 📋"]
    if jobs_completed_today:
        lines.append(f"• {len(jobs_completed_today)} job(s) wrapped up: " +
                     ", ".join(j.title[:20] for j in jobs_completed_today))
    if invoices_created_today:
        lines.append(f"• {invoices_created_today} invoice(s) queued")
    if collected_today > 0:
        lines.append(f"• Collected today: ${collected_today:,.2f}")
    else:
        lines.append("• No payments received today")
    if expenses_total > 0:
        lines.append(f"• Expenses logged: ${expenses_total:,.2f}")
    lines.append(f"• Active jobs: {active_count}")
    if overdue_count:
        lines.append(f"• ⚠️ {overdue_count} overdue invoice(s), ${overdue_total:,.2f} outstanding")
    lines.append("Reply 'summary' anytime for full financials.")

    return "\n".join(lines)


# ── Alert generation ─────────────────────────────────────────────────────────

def generate_alerts(contractor: Contractor, db: Session) -> list[str]:
    """Proactive alerts — budget overruns, stale leads, uninvoiced complete jobs."""
    alerts = []
    memory = Memory(db, contractor.id)
    now = datetime.now(timezone.utc)

    # Jobs complete but no invoice after 3 days
    complete_jobs = (
        db.query(Job)
        .filter(Job.contractor_id == contractor.id, Job.status == JobStatus.COMPLETE)
        .all()
    )
    for job in complete_jobs:
        has_invoice = bool(db.query(Invoice).filter(Invoice.job_id == job.id).first())
        if not has_invoice and job.completed_at:
            completed = _as_utc(job.completed_at)
            days_since = (now - completed).days
            if days_since >= 3:
                alerts.append(
                    f"'{job.title[:30]}' done {days_since}d ago, no invoice sent yet. "
                    f"Text 'invoice {job.title[:20]}' to generate one."
                )

    # Jobs trending over budget
    active_jobs = (
        db.query(Job)
        .filter(Job.contractor_id == contractor.id, Job.status == JobStatus.ACTIVE)
        .all()
    )
    for job in active_jobs:
        if job.is_over_budget and job.quoted_amount:
            alerts.append(
                f"⚠️ '{job.title[:30]}' is at {job.budget_used_pct:.0f}% of the "
                f"${job.quoted_amount:,.0f} budget. Consider a change order."
            )

    # Stale leads (14+ days)
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
        names = ", ".join(f"'{j.title[:15]}'" for j in stale_leads[:3])
        alerts.append(
            f"{len(stale_leads)} stale lead(s) with no activity in 14+ days: {names}. "
            f"Follow up or cancel?"
        )

    return alerts


# ── Morning briefing ─────────────────────────────────────────────────────────

def morning_briefing(contractor: Contractor, db: Session) -> str:
    memory = Memory(db, contractor.id)
    name = contractor.name.split()[0]

    active = db.query(Job).filter(
        Job.contractor_id == contractor.id, Job.status == JobStatus.ACTIVE
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
    lines.append("Text me anything to get started.")
    return "\n".join(lines)


# ── Main scheduler entry points ───────────────────────────────────────────────

def run_morning_briefings():
    """Send morning briefings to all contractors. Call at 7am."""
    db = SessionLocal()
    try:
        contractors = db.query(Contractor).filter(Contractor.onboarding_complete == True).all()
        for contractor in contractors:
            msg = morning_briefing(contractor, db)
            send_sms(contractor.phone, msg)
    finally:
        db.close()


def run_eod_summaries():
    """Send EOD summaries. Call at 6pm."""
    db = SessionLocal()
    try:
        contractors = db.query(Contractor).filter(Contractor.onboarding_complete == True).all()
        for contractor in contractors:
            msg = eod_summary(contractor, db)
            send_sms(contractor.phone, msg)
    finally:
        db.close()


def run_dunning():
    """Run autonomous dunning for all contractors. Call hourly or twice daily."""
    db = SessionLocal()
    try:
        contractors = db.query(Contractor).filter(Contractor.onboarding_complete == True).all()
        for contractor in contractors:
            actions = run_autonomous_dunning(db, contractor)
            if actions:
                print(f"[Dunning] {contractor.name}: {len(actions)} action(s)")
    finally:
        db.close()


def run_all_alerts():
    """Check and send proactive alerts. Call every few hours."""
    db = SessionLocal()
    try:
        contractors = db.query(Contractor).filter(Contractor.onboarding_complete == True).all()
        for contractor in contractors:
            alerts = generate_alerts(contractor, db)
            for alert in alerts:
                send_sms(contractor.phone, alert)
    finally:
        db.close()
