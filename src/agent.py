"""
Core agent loop — Claude Sonnet with full memory context and tool calling.

The agent can:
  - Answer any question about the contractor's business
  - Create/update clients, jobs, expenses
  - Transition job statuses
  - Queue documents for generation
  - Flag proactive alerts
"""
import json
import os
from datetime import datetime, timezone
from anthropic import Anthropic
from sqlalchemy.orm import Session
from src.memory import Memory
from src.models import Contractor, Client, Job, JobStatus, Expense, Invoice
from dotenv import load_dotenv

load_dotenv()

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are FIELDHAND, a business assistant for {name}, a {trade} contractor.

You know their entire business — every client, job, invoice, and expense.
You handle all business admin: invoicing, client communication, job tracking, expenses.
You do NOT tell them how to do their trade. You handle the paperwork side.

Personality:
- Direct and brief. They're busy, often on a job site.
- No corporate speak. Talk like a real person.
- Proactive — if you notice something wrong, say so.
- Remember personal details they've shared.
- Confident on business matters. Deferential on trade matters.

Current business snapshot:
{context}

Today's date: {today}

When the contractor asks you to do something, use the available tools to actually do it.
After using a tool, confirm what you did in plain language.
If you need more info to complete a task, ask one focused question.
"""

# ------------------------------------------------------------------ #
# TOOL DEFINITIONS
# ------------------------------------------------------------------ #

TOOLS = [
    {
        "name": "create_client",
        "description": "Create a new client record in the system.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Client's full name"},
                "phone": {"type": "string", "description": "Phone number"},
                "email": {"type": "string", "description": "Email address"},
                "address": {"type": "string", "description": "Job site or home address"},
                "notes": {"type": "string", "description": "Any notes about this client"},
                "referral_source": {"type": "string", "description": "How did they find the contractor"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_job",
        "description": "Create a new job. Creates client too if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short job title e.g. 'Replace panel - Johnson'"},
                "client_name": {"type": "string", "description": "Client's name — will find or create"},
                "description": {"type": "string", "description": "Full job description"},
                "address": {"type": "string", "description": "Job site address"},
                "quoted_amount": {"type": "number", "description": "Quoted price in dollars"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_job_status",
        "description": "Move a job to a new status. Valid: lead, quoted, active, complete, paid, cancelled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_title_hint": {"type": "string", "description": "Part of the job title to find it"},
                "new_status": {"type": "string", "description": "New status"},
            },
            "required": ["job_title_hint", "new_status"],
        },
    },
    {
        "name": "log_expense",
        "description": "Log a material, fuel, tool, or other expense against a job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_title_hint": {"type": "string", "description": "Part of the job title"},
                "description": {"type": "string", "description": "What was purchased"},
                "amount": {"type": "number", "description": "Dollar amount"},
                "category": {
                    "type": "string",
                    "enum": ["materials", "labor", "subcontractor", "fuel", "tools", "permits", "other"],
                },
                "vendor": {"type": "string", "description": "Where it was purchased"},
            },
            "required": ["job_title_hint", "description", "amount"],
        },
    },
    {
        "name": "get_financial_summary",
        "description": "Get a financial snapshot: income this month, total outstanding, overdue invoices.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_jobs",
        "description": "List jobs filtered by status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status. Omit for all active.",
                    "enum": ["lead", "quoted", "active", "complete", "paid", "cancelled", "all"],
                },
            },
            "required": [],
        },
    },
    {
        "name": "add_client_note",
        "description": "Add or update a note on a client record.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["client_name", "note"],
        },
    },
    {
        "name": "queue_invoice",
        "description": "Queue an invoice to be generated and sent for a job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_title_hint": {"type": "string"},
                "amount": {"type": "number", "description": "Invoice amount. Defaults to quoted amount."},
            },
            "required": ["job_title_hint"],
        },
    },
]


# ------------------------------------------------------------------ #
# TOOL EXECUTION
# ------------------------------------------------------------------ #

def execute_tool(tool_name: str, tool_input: dict, memory: Memory, db: Session) -> str:
    """Run a tool and return a plain-text result."""

    if tool_name == "create_client":
        existing = memory.find_client(tool_input["name"])
        if existing:
            return f"Client {existing.name} already exists (id: {existing.id})."
        c = memory.create_client(**tool_input)
        return f"Created client: {c.name} (id: {c.id})"

    elif tool_name == "create_job":
        client_name = tool_input.pop("client_name", None)
        client_id = None
        if client_name:
            c = memory.find_client(client_name)
            if not c:
                c = memory.create_client(name=client_name)
            client_id = c.id
        job = memory.create_job(client_id=client_id, **tool_input)
        return f"Created job: '{job.title}' (id: {job.id}) status=LEAD"

    elif tool_name == "update_job_status":
        job = memory.find_job(tool_input["job_title_hint"])
        if not job:
            return f"Could not find job matching '{tool_input['job_title_hint']}'."
        try:
            new_status = JobStatus(tool_input["new_status"].lower())
        except ValueError:
            return f"Unknown status '{tool_input['new_status']}'. Valid: lead, quoted, active, complete, paid, cancelled."
        old = job.status.value
        success = job.transition_to(new_status)
        if success:
            db.commit()
            return f"Job '{job.title}' moved from {old} -> {new_status.value}."
        else:
            return f"Cannot move '{job.title}' from {old} to {new_status.value}. Not a valid transition."

    elif tool_name == "log_expense":
        job = memory.find_job(tool_input["job_title_hint"])
        if not job:
            return f"Could not find job matching '{tool_input['job_title_hint']}'."
        expense = memory.log_expense(
            job_id=job.id,
            description=tool_input["description"],
            amount=tool_input["amount"],
            category=tool_input.get("category", "materials"),
            vendor=tool_input.get("vendor"),
        )
        over_budget_warning = ""
        db.refresh(job)
        if job.is_over_budget:
            over_budget_warning = f" ⚠️  Job is now at {job.budget_used_pct:.0f}% of quoted budget (${job.quoted_amount:.2f})."
        return f"Logged ${expense.amount:.2f} ({expense.category}) for '{job.title}'.{over_budget_warning}"

    elif tool_name == "get_financial_summary":
        outstanding = memory.get_outstanding_total()
        monthly = memory.get_monthly_income()
        overdue = memory.get_overdue_invoices(min_days=7)
        overdue_str = ""
        if overdue:
            overdue_str = "\nOverdue invoices:\n" + "\n".join(
                f"  - ${inv.amount:.2f} (sent {inv.sent_at.strftime('%b %d') if inv.sent_at else 'unknown'})"
                for inv in overdue
            )
        return (
            f"Monthly income (this month): ${monthly:,.2f}\n"
            f"Total outstanding: ${outstanding:,.2f}"
            + overdue_str
        )

    elif tool_name == "list_jobs":
        status_filter = tool_input.get("status", "active")
        query = db.query(Job).filter(Job.contractor_id == memory.contractor_id)
        if status_filter != "all":
            if status_filter == "active":
                query = query.filter(Job.status.in_([
                    JobStatus.LEAD, JobStatus.QUOTED,
                    JobStatus.ACTIVE, JobStatus.COMPLETE
                ]))
            else:
                try:
                    query = query.filter(Job.status == JobStatus(status_filter))
                except ValueError:
                    return f"Unknown status '{status_filter}'."
        jobs = query.order_by(Job.created_at.desc()).limit(20).all()
        if not jobs:
            return "No jobs found."
        lines = []
        for j in jobs:
            client_name = j.client.name if j.client else "No client"
            budget_info = f" ({j.budget_used_pct:.0f}% of budget)" if j.budget_used_pct else ""
            lines.append(f"  [{j.status.value.upper()}] {j.title} — {client_name}{budget_info}")
        return "Jobs:\n" + "\n".join(lines)

    elif tool_name == "add_client_note":
        c = memory.find_client(tool_input["client_name"])
        if not c:
            return f"Client '{tool_input['client_name']}' not found."
        existing = c.notes or ""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        c.notes = f"{existing}\n[{timestamp}] {tool_input['note']}".strip()
        db.commit()
        return f"Note added to {c.name}."

    elif tool_name == "queue_invoice":
        job = memory.find_job(tool_input["job_title_hint"])
        if not job:
            return f"Could not find job matching '{tool_input['job_title_hint']}'."
        amount = tool_input.get("amount") or job.quoted_amount
        if not amount:
            return f"No amount specified and job '{job.title}' has no quoted amount. Please specify an amount."
        # Store pending invoice record
        from src.models.invoice import Invoice, InvoiceStatus
        inv = Invoice(
            job_id=job.id,
            contractor_id=memory.contractor_id,
            amount=amount,
            status=InvoiceStatus.DRAFT,
        )
        db.add(inv)
        db.commit()
        return f"Invoice queued for '{job.title}': ${amount:,.2f}. Ready to send — confirm to fire it off."

    return f"Unknown tool: {tool_name}"


# ------------------------------------------------------------------ #
# MAIN AGENT CLASS
# ------------------------------------------------------------------ #

class ContractorAgent:
    def __init__(self, db: Session, contractor_id: str):
        self.db = db
        self.contractor_id = contractor_id
        self.memory = Memory(db, contractor_id)

    def chat(self, user_message: str, channel: str = "sms") -> str:
        """
        Process a message from the contractor and return a response.
        Stores both sides of the exchange in memory.
        """
        # Store incoming message
        self.memory.store_message("user", user_message, channel)

        # Build context snapshot
        context = self.memory.get_context_snapshot()
        contractor = context.get("contractor", {})

        system = SYSTEM_PROMPT.format(
            name=contractor.get("name", "the contractor"),
            trade=contractor.get("trade", "trade"),
            context=json.dumps(context, indent=2, default=str),
            today=datetime.now(timezone.utc).strftime("%A, %B %d, %Y"),
        )

        # Build message history for this conversation
        history = self.memory.get_recent_messages(limit=15)
        # The last message is the one we just stored — already in history
        # Build Claude messages list (exclude the last since it's the current turn)
        messages = history[:-1] + [{"role": "user", "content": user_message}]

        # Agentic loop — keep running until no more tool calls
        response_text = self._run_agentic_loop(system, messages)

        # Store assistant response
        self.memory.store_message("assistant", response_text, channel)

        return response_text

    def _run_agentic_loop(self, system: str, messages: list[dict]) -> str:
        """Run Claude with tool calling until we get a final text response."""
        loop_messages = list(messages)
        final_text = ""

        for _ in range(10):  # max 10 tool call rounds
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1024,
                system=system,
                messages=loop_messages,
                tools=TOOLS,
            )

            # Collect text from this response
            text_parts = [b.text for b in response.content if hasattr(b, "text")]
            if text_parts:
                final_text = " ".join(text_parts)

            # If no tool use, we're done
            if response.stop_reason == "end_turn":
                break

            # Process tool calls
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break

            # Add assistant message with all content blocks
            loop_messages.append({"role": "assistant", "content": response.content})

            # Execute tools and build tool_result blocks
            tool_results = []
            for tool_use in tool_uses:
                result = execute_tool(
                    tool_use.name,
                    dict(tool_use.input),
                    self.memory,
                    self.db,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result,
                })

            loop_messages.append({"role": "user", "content": tool_results})

        return final_text or "Done."
