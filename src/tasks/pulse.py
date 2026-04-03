"""
FIELDHAND Proactive Pulse — runs every 2 hours per contractor.

Claude looks at the full business snapshot and reasons about what actions
to take to further the contractor's goals: more revenue, less stress,
faster payments, no dropped balls, regulatory compliance.

It then actually executes those actions autonomously — sending dunning emails,
flagging stale jobs, surfacing cash flow issues, etc. — and sends a single
SMS summary of what it did (if anything).

Nothing is sent unless Claude decides it's worth acting on. If everything
looks fine, the contractor hears nothing.
"""
import os
import json
from datetime import datetime, timezone, timedelta
from anthropic import Anthropic
from sqlalchemy.orm import Session
from src.database import SessionLocal
from src.models import Contractor, Job, JobStatus, Invoice, InvoiceStatus
from src.models.expense import Expense
from src.memory import Memory
from src.tasks.monitoring import (
    send_sms, send_gmail, run_autonomous_dunning,
    generate_alerts, _as_utc
)
from src.audit import log as audit_log
from dotenv import load_dotenv

load_dotenv()

_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

PULSE_SYSTEM = """You are FIELDHAND's autonomous business intelligence engine.

Every 2 hours you wake up and review a contractor's full business snapshot.
Your job is to identify anything that needs attention and take action.

You think like a sharp business manager whose goals are:
1. REVENUE — make sure money owed gets collected, quotes convert to jobs, no work goes uninvoiced
2. EFFICIENCY — flag anything that's taking too long or costing more than it should
3. CASH FLOW — identify overdue invoices, upcoming payment crunches, budget blowouts
4. COMPLIANCE — permits needed, license expirations, insurance gaps
5. RELATIONSHIPS — stale leads, clients who haven't heard from the contractor in a while
6. WORKLOAD — is the contractor overloaded? Underloaded? Turning down money?

You will be given the full business data. Reason through it carefully.

Then output a JSON object with:
{
  "assessment": "1-2 sentence summary of the business health right now",
  "actions": [
    {
      "type": "sms_contractor" | "send_dunning" | "log_alert",
      "priority": "high" | "medium" | "low",
      "reason": "why this action is needed",
      "message": "exact text to send (for sms_contractor) or note to log",
      "invoice_id": "UUID if type is send_dunning",
      "client_email": "email if type is send_dunning"
    }
  ],
  "nothing_to_do": true/false
}

Rules:
- Only act on things that genuinely need attention right now
- Don't spam the contractor — batch insights into ONE SMS if multiple things
- Don't send dunning emails more than once per threshold (3/7/14/30 days)
- If everything is healthy, set nothing_to_do: true and actions: []
- Be direct and specific — contractors are busy people on job sites
- Prioritize cash flow issues above everything else"""


def _build_snapshot(contractor: Contractor, db: Session) -> dict:
    """Build a comprehensive business snapshot for Claude to reason over."""
    memory = Memory(db, contractor.id)
    now = datetime.now(timezone.utc)

    # Jobs by status
    jobs = db.query(Job).filter(Job.contractor_id == contractor.id).all()
    jobs_data = []
    for j in jobs:
        expenses = db.query(Expense).filter(Expense.job_id == j.id).all()
        total_expenses = sum(e.amount for e in expenses)
        invoices = db.query(Invoice).filter(Invoice.job_id == j.id).all()
        jobs_data.append({
            "id": str(j.id),
            "title": j.title,
            "status": j.status.value,
            "client": j.client.name if j.client else None,
            "client_email": j.client.email if j.client else None,
            "quoted_amount": j.quoted_amount,
            "total_expenses": round(total_expenses, 2),
            "budget_used_pct": round(j.budget_used_pct or 0, 1),
            "is_over_budget": j.is_over_budget,
            "days_since_created": (now - _as_utc(j.created_at)).days if j.created_at else None,
            "days_since_completed": (now - _as_utc(j.completed_at)).days if j.completed_at else None,
            "has_invoice": bool(invoices),
            "address": j.address,
        })

    # Invoices
    invoices_all = db.query(Invoice).filter(Invoice.contractor_id == contractor.id).all()
    invoices_data = []
    for inv in invoices_all:
        days_since_sent = None
        if inv.sent_at:
            days_since_sent = (now - _as_utc(inv.sent_at)).days
        invoices_data.append({
            "id": str(inv.id),
            "job_title": inv.job.title if inv.job else None,
            "client_name": inv.job.client.name if inv.job and inv.job.client else None,
            "client_email": inv.job.client.email if inv.job and inv.job.client else None,
            "amount": inv.amount,
            "status": inv.status.value,
            "days_since_sent": days_since_sent,
        })

    # Financial summary
    outstanding = memory.get_outstanding_total()
    monthly_income = memory.get_monthly_income()
    overdue = memory.get_overdue_invoices(min_days=1)

    # Contractor profile gaps
    profile_gaps = []
    if not contractor.license_no:
        profile_gaps.append("license_no")
    if not contractor.gl_carrier:
        profile_gaps.append("gl_insurance")
    if not contractor.ein:
        profile_gaps.append("ein")

    return {
        "contractor": {
            "name": contractor.name,
            "trade": contractor.trade,
            "business_name": contractor.business_name,
            "labor_rate": contractor.labor_rate,
            "markup_pct": contractor.markup_pct,
            "gmail_connected": bool(contractor.gmail_refresh_token),
            "profile_gaps": profile_gaps,
        },
        "timestamp": now.isoformat(),
        "financials": {
            "outstanding_total": round(outstanding, 2),
            "monthly_income": round(monthly_income, 2),
            "overdue_count": len(overdue),
        },
        "jobs": jobs_data,
        "invoices": invoices_data,
    }


def run_pulse_for_contractor(contractor: Contractor, db: Session) -> dict:
    """
    Run the proactive pulse for a single contractor.
    Returns a dict of actions taken.
    """
    snapshot = _build_snapshot(contractor, db)

    # Ask Claude to reason over the snapshot
    try:
        resp = _client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=PULSE_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Here is the current business snapshot for {contractor.name}:\n\n"
                    f"{json.dumps(snapshot, indent=2, default=str)}\n\n"
                    f"Reason through this and tell me what actions to take, if any."
                )
            }]
        )
        raw = resp.content[0].text.strip()
    except Exception as e:
        audit_log(db, contractor.id, "pulse_error",
                  subject=f"Pulse API error: {e}", channel="system")
        return {"error": str(e)}

    # Parse Claude's response
    try:
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            decision = json.loads(match.group())
        else:
            decision = json.loads(raw)
    except Exception:
        audit_log(db, contractor.id, "pulse_error",
                  subject="Failed to parse pulse response", detail=raw, channel="system")
        return {"error": "parse_failed", "raw": raw}

    # Log the assessment
    audit_log(db, contractor.id, "pulse_ran",
              subject=decision.get("assessment", "Pulse complete"),
              detail=raw, channel="system", initiated_by="agent")

    if decision.get("nothing_to_do"):
        return {"actions_taken": [], "assessment": decision.get("assessment")}

    actions = decision.get("actions", [])
    taken = []
    sms_parts = []  # batch SMS messages

    for action in actions:
        action_type = action.get("type")
        priority = action.get("priority", "medium")
        reason = action.get("reason", "")

        if action_type == "sms_contractor":
            # Collect into batch
            msg = action.get("message", "")
            if msg:
                sms_parts.append(msg)
            taken.append(f"queued_sms: {msg[:50]}")

        elif action_type == "send_dunning":
            # Run autonomous dunning for this contractor
            dunning_actions = run_autonomous_dunning(db, contractor)
            taken.extend(dunning_actions)

        elif action_type == "log_alert":
            # Just log it — lower priority items
            audit_log(db, contractor.id, "pulse_alert",
                      subject=reason,
                      detail=action.get("message", ""),
                      channel="system", initiated_by="agent")
            taken.append(f"logged: {reason[:50]}")

    # Send batched SMS if anything worth telling the contractor
    high_priority = [a for a in actions if a.get("priority") == "high"]
    if sms_parts:
        # Only send if there's at least one high/medium priority item
        if any(a.get("priority") in ("high", "medium") for a in actions):
            full_msg = "\n\n".join(sms_parts)
            # Cap SMS length
            if len(full_msg) > 1400:
                full_msg = full_msg[:1400] + "..."
            send_sms(contractor.phone, full_msg)
            audit_log(db, contractor.id, "pulse_sms_sent",
                      subject=f"Pulse SMS — {len(sms_parts)} item(s)",
                      detail=full_msg, channel="sms", initiated_by="agent")

    return {
        "actions_taken": taken,
        "assessment": decision.get("assessment"),
        "sms_sent": len(sms_parts) > 0,
    }


def run_schedule_reminders():
    """
    Send day-before reminders for jobs scheduled tomorrow.
    Called once daily by the scheduler.
    """
    from src.models.job import Job, JobStatus
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        tomorrow_start = (now + timedelta(hours=20)).replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_end = tomorrow_start + timedelta(days=1)

        jobs = (
            db.query(Job)
            .filter(
                Job.scheduled_start >= tomorrow_start,
                Job.scheduled_start < tomorrow_end,
                Job.reminder_sent == False,
                Job.status.notin_([JobStatus.CANCELLED, JobStatus.PAID]),
            )
            .all()
        )

        for job in jobs:
            contractor_id = job.contractor_id
            contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
            if not contractor:
                continue
            client_name = job.client.name if job.client else "No client"
            address = job.address or ""
            msg = (
                f"Tomorrow: {job.title} — {client_name}\n"
                f"{address}\n"
                f"Starts: {job.scheduled_start.strftime('%I:%M %p')}"
            ).strip()
            send_sms(contractor.phone, msg)
            job.reminder_sent = True
            db.commit()
            print(f"[Schedule Reminder] {contractor.name}: {job.title}")
    finally:
        db.close()


def run_pulse_all():
    """
    Entry point — run the pulse for every active contractor.
    Called every 2 hours by the scheduler.
    """
    db = SessionLocal()
    try:
        contractors = (
            db.query(Contractor)
            .filter(Contractor.onboarding_complete == True)
            .all()
        )
        print(f"[Pulse] Running for {len(contractors)} contractor(s) at {datetime.now(timezone.utc).isoformat()}")
        for contractor in contractors:
            try:
                result = run_pulse_for_contractor(contractor, db)
                taken = result.get("actions_taken", [])
                print(f"[Pulse] {contractor.name}: {result.get('assessment')} | {len(taken)} action(s)")
            except Exception as e:
                print(f"[Pulse] ERROR for {contractor.name}: {e}")
    finally:
        db.close()
