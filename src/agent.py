"""
Core agent loop — Claude Sonnet with full memory context and tool calling.

v2: Added tools for:
  - lookup_price (live material pricing)
  - create_change_order (formal change order with PDF)
  - generate_permit_prep (permit autofill sheet)
  - generate_picklist (supply house order)
  - export_tax_csv (quarterly expense export)
  - send_quote_to_client (sends quote PDF immediately)
"""
import json
import os
import asyncio
from datetime import datetime, timezone
from anthropic import Anthropic
from sqlalchemy.orm import Session
from src.memory import Memory
from src.models import Contractor, Client, Job, JobStatus, Expense, Invoice
from src.audit import log as audit_log
from dotenv import load_dotenv
from src.router import classify, get_tools_for_categories, TOOL_CATEGORIES
from src.cost_tracker import MessageCost
from src.tools.analytics import query_business as _query_business, safe_eval
from src.tools.web_search import web_search_sync
from src.outbound_review import review as outbound_review, format_block_message

load_dotenv()

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are FIELDHAND, a business assistant for {name}, a {trade} contractor.

You know their entire business — every client, job, invoice, and expense.
You handle all business admin: invoicing, client communication, job tracking, expenses, permits, supply orders.
You do NOT tell them how to do their trade. You handle the paperwork side.

Personality:
- Direct and brief. They're busy, often on a job site.
- No corporate speak. Talk like a real person.
- Proactive — if you notice something wrong, say so.
- Remember personal details they've shared.
- Confident on business matters. Deferential on trade matters.

Client information:
- You are ravenous for client contact info. Every client needs email AND phone in the system.
- Whenever you create or reference a client that is missing email or phone, immediately ask for it
  in the same response. Don't wait. e.g. "Added Sarah Chen. What's her email and cell?"
- If they give just one, ask for the other. Once you have both, confirm and move on.
- Client address is required for permit applications — ask for it if missing when a permit is involved.
- Store client_type (homeowner/business/gc/property_manager) whenever you can infer it.
- If a client is not the property owner (e.g. a GC or property manager), ask for the owner's name
  since it's required on permit applications.

Job context inference:
- When a contractor sends a follow-up (materials, expenses, status update), infer which job it applies to from context.
- Use the conversation history and the active_jobs list to determine the most likely job.
- If one job is clearly active/recently discussed, apply to it without asking.
- If there are multiple candidates and it's genuinely ambiguous, ask ONE brief question: "Which job — Smith or Garcia?"
- Never ask if the message makes it obvious from client name, address, trade, or prior conversation.
- When you switch between jobs naturally in conversation, do it silently — don't announce it.

Profile collection (just-in-time):
- You collected name, trade, and rate at signup. Everything else gets collected when it's needed.
- Before executing a task that requires missing info, tell the contractor what you need and why, then ask for it. ONE piece of info at a time.
- When they provide it, call update_contractor_profile to save it immediately, then complete the original task.
- Do NOT ask for info you don't need right now. Never front-load questions.
- Triggers:
  * Building a quote/estimate → need: markup_pct (ask if 0 or missing), license_no (for letterhead)
  * Sending anything to a client → need: email (contractor's send-from email / Gmail)
  * Generating a permit → need: license_no, business_address, gl_carrier + gl_policy_number + gl_expiration, wc_carrier OR wc_exempt
  * Tax CSV export → need: ein
  * Supply house order → need: business_address
- Phrase it as: "I can [do X] — I just need [Y] first. [Brief reason]. What is it?"
- After they give it, confirm what you saved and immediately do the original task.

Current business snapshot:
{context}

Today's date: {today}

Your personal notes (things you've observed and remembered):
{agent_notes}

Behavior rules set by this contractor (follow these exactly):
{custom_rules}

When the contractor asks you to do something, use the available tools to actually do it.
After using a tool, confirm what you did in plain language.
If you need more info to complete a task, ask one focused question.

When the contractor tells you to always/never do something, or sets a preference,
immediately call update_behavior_rules to save it. Don't just acknowledge it — save it.
When you learn something worth remembering (a pattern, a preference, a personal detail),
call update_agent_memory to store it. You have a persistent memory — use it.

EMAIL APPROVAL RULE — critical:
Before sending ANY email to a client or outside party, you MUST show the contractor
a preview and ask for confirmation. Use send_email or draft_email to stage the draft —
the system will hold it and ask the contractor "Send it? Reply YES to confirm."
Never send an email without explicit contractor approval. No exceptions.
The only time you send without asking is if the contractor has JUST said "yes", "send it",
"looks good", "confirm", or similar in response to a specific pending email you showed them.
"""

# ─────────────────────────────────────────────────────────────────────────── #
# TOOL DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────── #

TOOLS = [
    {
        "name": "create_client",
        "description": "Create a new client record. Capture as much contact info as possible — email and phone are essential.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string"},
                "address": {"type": "string"},
                "notes": {"type": "string"},
                "referral_source": {"type": "string"},
                "client_type": {"type": "string", "description": "homeowner, business, gc, or property_manager"},
                "property_owner_name": {"type": "string", "description": "If client is not the property owner"},
                "property_type": {"type": "string", "description": "single_family, multi_family, commercial, industrial"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_client",
        "description": "Update an existing client's contact info — email, phone, address, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "address": {"type": "string"},
                "client_type": {"type": "string"},
                "property_owner_name": {"type": "string"},
                "property_type": {"type": "string"},
            },
            "required": ["client_name"],
        },
    },
    {
        "name": "create_job",
        "description": "Create a new job. Creates client too if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short job title e.g. 'Replace panel - Johnson'"},
                "client_name": {"type": "string"},
                "description": {"type": "string"},
                "address": {"type": "string"},
                "quoted_amount": {"type": "number"},
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
                "job_title_hint": {"type": "string"},
                "new_status": {"type": "string"},
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
                "job_title_hint": {"type": "string"},
                "description": {"type": "string"},
                "amount": {"type": "number"},
                "category": {
                    "type": "string",
                    "enum": ["materials", "labor", "subcontractor", "fuel", "tools", "permits", "other"],
                },
                "vendor": {"type": "string"},
            },
            "required": ["job_title_hint", "description", "amount"],
        },
    },
    {
        "name": "lookup_price",
        "description": "Look up current material pricing from supplier databases. Use this when building estimates so costs are accurate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "List of items to price",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "qty": {"type": "number"},
                        },
                        "required": ["description"],
                    },
                },
                "zip_code": {"type": "string", "description": "Zip code for localized pricing"},
            },
            "required": ["items"],
        },
    },
    {
        "name": "get_financial_summary",
        "description": "Get a financial snapshot: income this month, total outstanding, overdue invoices.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_jobs",
        "description": "List jobs filtered by status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
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
        "description": "Queue an invoice to be generated. Does NOT send until confirmed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_title_hint": {"type": "string"},
                "amount": {"type": "number"},
            },
            "required": ["job_title_hint"],
        },
    },
    {
        "name": "send_invoice_to_client",
        "description": "Send a final invoice to the client via email and record it in the system. Executes immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_title_hint": {"type": "string"},
                "amount": {"type": "number"},
                "client_email": {"type": "string"},
            },
            "required": ["job_title_hint"],
        },
    },
    {
        "name": "send_quote_to_client",
        "description": "Generate a quote PDF and send it to the client via email. Also sends a copy to the contractor. Executes immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_title_hint": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "qty": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "amount": {"type": "number"},
                        },
                        "required": ["description", "qty", "unit_price", "amount"],
                    },
                },
                "notes": {"type": "string"},
                "client_email": {"type": "string", "description": "Override client email for this quote"},
            },
            "required": ["job_title_hint", "line_items"],
        },
    },
    {
        "name": "create_change_order",
        "description": "Create a formal change order for additional work/materials on an existing job. Updates the quoted amount and generates a PDF for client signature.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_title_hint": {"type": "string"},
                "reason": {"type": "string", "description": "Why the change order is needed"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "qty": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "amount": {"type": "number"},
                        },
                        "required": ["description", "qty", "unit_price", "amount"],
                    },
                },
            },
            "required": ["job_title_hint", "reason", "line_items"],
        },
    },
    {
        "name": "generate_permit_prep",
        "description": "Generate a permit application prep sheet PDF for a job. Extracts address, scope, license number, and contractor info into a pre-filled document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_title_hint": {"type": "string"},
                "start_date": {"type": "string", "description": "Estimated start date e.g. 'Tuesday' or 'April 15'"},
            },
            "required": ["job_title_hint"],
        },
    },
    {
        "name": "generate_picklist",
        "description": "Generate a material pick-list for a job and email it to the supply house for will-call pickup. Uses the job's line items or provided materials list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_title_hint": {"type": "string"},
                "materials": {
                    "type": "array",
                    "description": "Materials needed. If omitted, uses job expenses.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "qty": {"type": "number"},
                            "unit": {"type": "string"},
                        },
                        "required": ["description", "qty"],
                    },
                },
                "supply_house_email": {"type": "string", "description": "Email address of supply house"},
                "pickup_date": {"type": "string"},
            },
            "required": ["job_title_hint"],
        },
    },
    {
        "name": "export_tax_csv",
        "description": "Export all categorized expenses to a CSV file for tax prep / CPA. Can filter by date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD, defaults to start of current year"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
            },
            "required": [],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for permit requirements, code references, supplier stock, local pricing, or any real-world information needed to complete a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results, default 3"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_business",
        "description": "Run open-ended analytics against the contractor's business data. Use for: profitability analysis, client rankings, expense breakdowns, revenue trends, capacity/workload assessment, cash flow analysis. Use this when the contractor asks 'why', 'which', 'how much', or wants a business analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The business question to analyze"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "calculate",
        "description": "Evaluate a math expression precisely. Use for job costing, markup calculations, margin analysis, hours x rate. Returns exact numeric result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression e.g. '(210 + 138.75) * 1.25 + 8 * 110'"},
            },
            "required": ["expression"],
        },
    },
    {
        "name": "check_email",
        "description": "Read the contractor's inbox and summarize unread emails.",
        "input_schema": {
            "type": "object",
            "properties": {"max_results": {"type": "integer"}},
            "required": [],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email on behalf of the contractor. Only use when contractor has explicitly said to send (not just draft).",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "draft_email",
        "description": "Send an email on behalf of the contractor. Use this for client follow-ups, dunning, and general correspondence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "context": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "update_agent_memory",
        "description": "Save a note to your own memory about this contractor, a client, a pattern you've noticed, or anything worth remembering for future conversations. Use this proactively whenever you learn something useful. Also use this to store personal details the contractor shares (family, preferences, habits).",
        "input_schema": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "The note to save. Be specific and concise."},
                "category": {
                    "type": "string",
                    "enum": ["business", "client", "personal", "pattern", "reminder"],
                    "description": "What kind of note this is."
                },
            },
            "required": ["note"],
        },
    },
    {
        "name": "update_behavior_rules",
        "description": "Update the rules that govern how you behave for this contractor. Use this when the contractor tells you to always/never do something, change how you work, or set a preference. Examples: 'always add 15% buffer to estimates', 'never send emails on weekends', 'round all quotes to nearest $100'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rule": {"type": "string", "description": "The new rule or preference to add or update."},
                "action": {
                    "type": "string",
                    "enum": ["add", "remove", "replace_all"],
                    "description": "add: append to existing rules. remove: delete a specific rule. replace_all: overwrite all rules."
                },
                "rules_text": {"type": "string", "description": "Full rules text (required for replace_all)."},
            },
            "required": ["rule", "action"],
        },
    },
    {
        "name": "update_contractor_profile",
        "description": "Save contractor profile info collected mid-conversation: license number, email, business address, EIN, insurance info, markup, payment terms. Use this whenever the contractor provides any of these details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "license_no": {"type": "string"},
                "license_expiration": {"type": "string"},
                "business_address": {"type": "string"},
                "email": {"type": "string"},
                "markup_pct": {"type": "number"},
                "invoice_terms": {"type": "string"},
                "ein": {"type": "string"},
                "gl_carrier": {"type": "string"},
                "gl_policy_number": {"type": "string"},
                "gl_expiration": {"type": "string"},
                "wc_exempt": {"type": "boolean"},
                "wc_carrier": {"type": "string"},
                "wc_policy": {"type": "string"},
                "wc_expiration": {"type": "string"},
                "insurance_agent_name": {"type": "string"},
                "insurance_agent_phone": {"type": "string"},
                "service_area": {"type": "string"},
                "labor_rate": {"type": "number"},
            },
            "required": [],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────── #
# TOOL EXECUTION
# ─────────────────────────────────────────────────────────────────────────── #

def execute_tool(tool_name: str, tool_input: dict, memory: Memory, db: Session) -> str:

    if tool_name == "create_client":
        existing = memory.find_client(tool_input["name"])
        if existing:
            # Update any new fields that were provided
            updated = False
            for field in ["email", "phone", "address", "client_type", "property_owner_name", "property_type"]:
                if tool_input.get(field) and not getattr(existing, field, None):
                    setattr(existing, field, tool_input[field])
                    updated = True
            if updated:
                db.commit()
            missing = [f for f, v in [("email", existing.email), ("phone", existing.phone)] if not v]
            miss_str = f" Still need: {', '.join(missing)}." if missing else ""
            return f"Client {existing.name} already exists.{miss_str}"
        c = memory.create_client(
            name=tool_input["name"],
            phone=tool_input.get("phone"),
            email=tool_input.get("email"),
            address=tool_input.get("address"),
            notes=tool_input.get("notes"),
            referral_source=tool_input.get("referral_source"),
        )
        # Set extra fields
        for field in ["client_type", "property_owner_name", "property_type"]:
            if tool_input.get(field):
                setattr(c, field, tool_input[field])
        db.commit()
        missing = [f for f, v in [("email", c.email), ("phone", c.phone)] if not v]
        miss_str = f" Missing: {', '.join(missing)} — ask the contractor." if missing else ""
        return f"Created client: {c.name}.{miss_str}"

    elif tool_name == "update_client":
        c = memory.find_client(tool_input["client_name"])
        if not c:
            return f"Client '{tool_input['client_name']}' not found."
        for field in ["email", "phone", "address", "client_type", "property_owner_name", "property_type"]:
            if tool_input.get(field):
                setattr(c, field, tool_input[field])
        db.commit()
        missing = [f for f, v in [("email", c.email), ("phone", c.phone)] if not v]
        miss_str = f" Still missing: {', '.join(missing)}." if missing else " Contact info complete."
        return f"Updated {c.name}.{miss_str}"

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
            return f"Unknown status '{tool_input['new_status']}'."
        old = job.status.value
        success = job.transition_to(new_status)
        if success:
            db.commit()
            return f"Job '{job.title}' moved from {old} -> {new_status.value}."
        return f"Cannot move '{job.title}' from {old} to {new_status.value}. Not a valid transition."

    elif tool_name == "log_expense":
        hint = tool_input.get("job_title_hint", "")
        job = None
        if not hint:
            return "Which job should I log this expense against?"
        job = memory.find_job(hint)
        if not job:
            return f"Could not find job matching '{hint}'."
        expense = memory.log_expense(
            job_id=job.id,
            description=tool_input["description"],
            amount=tool_input["amount"],
            category=tool_input.get("category", "materials"),
            vendor=tool_input.get("vendor"),
        )
        db.refresh(job)
        warning = ""
        if job.is_over_budget:
            warning = f" ⚠️ Job is at {job.budget_used_pct:.0f}% of ${job.quoted_amount:,.0f} budget — consider a change order."
        return f"Logged ${expense.amount:.2f} ({expense.category}) for '{job.title}'.{warning}"

    elif tool_name == "lookup_price":
        items = tool_input.get("items", [])
        zip_code = tool_input.get("zip_code", "10001")
        if not items:
            return "No items provided."
        from src.tools.price_lookup import lookup_multiple
        try:
            loop = asyncio.get_event_loop()
            results = loop.run_until_complete(lookup_multiple(
                [{"description": i["description"], "qty": i.get("qty", 1), "zip": zip_code}
                 for i in items]
            ))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(lookup_multiple(
                [{"description": i["description"], "qty": i.get("qty", 1), "zip": zip_code}
                 for i in items]
            ))
            loop.close()
        lines = []
        total = 0
        for r in results:
            if r["price"]:
                lines.append(f"  {r['item']}: ${r['price']:.2f}/{r['unit']} x{r.get('quantity',1)} = ${r['total']:.2f} [{r['confidence']}]")
                total += r["total"] or 0
            else:
                lines.append(f"  {r['item']}: price not found — {r.get('note','')}")
        lines.append(f"  TOTAL: ${total:.2f}")
        return "Price lookup:\n" + "\n".join(lines)

    elif tool_name == "get_financial_summary":
        outstanding = memory.get_outstanding_total()
        monthly = memory.get_monthly_income()
        overdue = memory.get_overdue_invoices(min_days=7)
        overdue_str = ""
        if overdue:
            overdue_str = "\nOverdue invoices:\n" + "\n".join(
                f"  - ${inv.amount:.2f} ({(datetime.now(timezone.utc) - (inv.sent_at.replace(tzinfo=timezone.utc) if inv.sent_at.tzinfo is None else inv.sent_at)).days}d overdue)"
                for inv in overdue
                if inv.sent_at
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
                    JobStatus.LEAD, JobStatus.QUOTED, JobStatus.ACTIVE, JobStatus.COMPLETE
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
            return f"No amount specified and job '{job.title}' has no quoted amount."
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

    elif tool_name == "send_invoice_to_client":
        job = memory.find_job(tool_input["job_title_hint"])
        if not job:
            return f"Could not find job matching '{tool_input['job_title_hint']}'."
        amount = tool_input.get("amount") or job.quoted_amount
        if not amount:
            return f"No amount for '{job.title}'. What's the invoice amount?"
        client_obj = job.client
        contractor = memory.get_contractor()
        to_email = tool_input.get("client_email") or (client_obj.email if client_obj else None)

        # ── Outbound review ──────────────────────────────────────────────────
        review_result = outbound_review(
            action_type="invoice",
            recipient=to_email or "unknown",
            content={"job_title": job.title, "amount": amount,
                     "terms": contractor.invoice_terms if contractor else "N/A",
                     "body_preview": f"Invoice for {job.title} — ${amount:,.2f}"},
            contractor_name=contractor.name if contractor else "",
            client_name=client_obj.name if client_obj else "",
        )
        audit_log(db, memory.contractor_id, "outbound_review",
                  subject=f"Invoice for '{job.title}' — {'APPROVED' if review_result.approved else 'BLOCKED'}",
                  detail=review_result.raw, channel="agent", initiated_by="agent")
        if not review_result.approved:
            return format_block_message(review_result, "invoice")
        # ────────────────────────────────────────────────────────────────────

        # Record invoice in DB
        from src.models.invoice import Invoice, InvoiceStatus
        inv = db.query(Invoice).filter(
            Invoice.job_id == job.id,
            Invoice.status == InvoiceStatus.DRAFT
        ).first()
        if not inv:
            inv = Invoice(job_id=job.id, contractor_id=memory.contractor_id, amount=amount)
            db.add(inv)
        inv.status = InvoiceStatus.SENT
        inv.sent_at = datetime.now(timezone.utc)
        inv.amount = amount
        db.commit()

        if to_email and contractor and contractor.gmail_refresh_token:
            try:
                from src.email_client import GmailClient
                gmail = GmailClient(contractor.gmail_refresh_token)
                client_first = client_obj.name.split()[0] if client_obj else "there"
                body = (
                    f"Hi {client_first},\n\n"
                    f"Your invoice for '{job.title}' is ready.\n\n"
                    f"Amount Due: ${amount:,.2f}\n"
                    f"Terms: {contractor.invoice_terms}\n\n"
                    f"Please reply to arrange payment or call {contractor.phone}.\n\n"
                    f"Thanks,\n{contractor.name}\n{contractor.business_name}"
                )
                loop = asyncio.get_event_loop()
                try:
                    loop.run_until_complete(gmail.send(
                        to=to_email,
                        subject=f"Invoice — {job.title} — ${amount:,.2f}",
                        body=body,
                    ))
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(gmail.send(to=to_email, subject=f"Invoice — {job.title} — ${amount:,.2f}", body=body))
                    loop.close()
                audit_log(db, memory.contractor_id, "invoice_sent",
                          subject=f"Invoice for '{job.title}' to {to_email} — ${amount:,.2f}",
                          channel="email", initiated_by="agent")
                return f"Invoice sent to {to_email} — ${amount:,.2f}."
            except Exception as e:
                return f"Invoice recorded but email failed: {e}. Send manually to {to_email}."
        return f"Invoice recorded (${amount:,.2f}) but no client email on file. What's {client_obj.name if client_obj else 'the client'}'s email?"

    elif tool_name == "send_quote_to_client":
        job = memory.find_job(tool_input["job_title_hint"])
        if not job:
            return f"Could not find job matching '{tool_input['job_title_hint']}'."
        line_items = tool_input.get("line_items", [])
        if not line_items:
            return "No line items provided for the quote."
        total = sum(i["amount"] for i in line_items)
        client_obj = job.client
        contractor = memory.get_contractor()

        # ── Outbound review ──────────────────────────────────────────────────
        review_result = outbound_review(
            action_type="quote",
            recipient=tool_input.get("client_email") or (client_obj.email if client_obj else "unknown"),
            content={"job_title": job.title, "total": total, "line_items": line_items, "notes": tool_input.get("notes")},
            contractor_name=contractor.name if contractor else "",
            client_name=client_obj.name if client_obj else "",
        )
        audit_log(db, memory.contractor_id, "outbound_review",
                  subject=f"Quote for '{job.title}' — {'APPROVED' if review_result.approved else 'BLOCKED'}",
                  detail=review_result.raw, channel="agent", initiated_by="agent")
        if not review_result.approved:
            return format_block_message(review_result, "quote")
        # ────────────────────────────────────────────────────────────────────

        # Generate PDF
        from src.documents.quote import generate_quote
        from pathlib import Path
        pdf_path = generate_quote(
            job=job,
            line_items=line_items,
            db=db,
            notes=tool_input.get("notes"),
        )

        # Stage quote for contractor approval
        client_email = tool_input.get("client_email") or (client_obj.email if client_obj else None)
        if client_email and contractor and contractor.gmail_refresh_token:
            import json as _json
            client_first = client_obj.name if client_obj else "there"
            quote_body = (
                f"Hi {client_first},\n\n"
                f"Please find your estimate attached for '{job.title}'.\n\n"
                f"Total: ${total:,.2f}\n"
                f"Valid for 30 days.\n\n"
                f"Reply to approve or call to discuss.\n\n"
                f"{contractor.name}\n{contractor.business_name}\n{contractor.phone}"
            )
            contractor.pending_email = _json.dumps({
                "to": client_email,
                "subject": f"Estimate — {job.title}",
                "body": quote_body,
                "pdf_path": pdf_path,
            })
            db.commit()
            audit_log(db, memory.contractor_id, "email_staged",
                      subject=f"Quote pending approval: {job.title} → {client_email}",
                      channel="agent", initiated_by="agent")
            preview = quote_body[:280]
            return (
                f"Quote ready — ${total:,.2f} for {client_obj.name if client_obj else 'client'}.\n\n"
                f"To: {client_email}\n"
                f"Subject: Estimate — {job.title}\n\n"
                f"{preview}\n\n"
                f"Reply YES to send, or tell me what to change."
            )

        results = []
        results.append(f"No client email — PDF saved at {pdf_path}")

        # Send preview copy to contractor
        contractor_email = contractor.work_email or contractor.email if contractor else None
        if contractor_email and contractor and contractor.gmail_refresh_token:
            try:
                _send_quote_email(
                    gmail_token=contractor.gmail_refresh_token,
                    to=contractor_email,
                    subject=f"[Your Copy] Quote sent to {client_obj.name if client_obj else 'client'} — {job.title} — ${total:,.2f}",
                    body=(
                        f"A copy of the quote you just sent to {client_obj.name if client_obj else 'your client'}.\n\n"
                        f"Job: {job.title}\nTotal: ${total:,.2f}\n\n— FIELDHAND"
                    ),
                    pdf_path=pdf_path,
                    pdf_filename=f"YOUR_COPY_{Path(pdf_path).name}",
                )
                results.append(f"Your copy sent to {contractor_email}")
            except Exception:
                pass  # Contractor copy failure is non-critical

        # Update job quoted amount if not set
        if not job.quoted_amount:
            job.quoted_amount = total
            db.commit()

        return " | ".join(results)

    elif tool_name == "create_change_order":
        job = memory.find_job(tool_input["job_title_hint"])
        if not job:
            return f"Could not find job matching '{tool_input['job_title_hint']}'."
        contractor = memory.get_contractor()
        line_items = tool_input["line_items"]
        co_total = sum(i["amount"] for i in line_items)
        revised_total_preview = (job.quoted_amount or 0) + co_total

        # ── Outbound review ──────────────────────────────────────────────────
        client_obj = job.client
        review_result = outbound_review(
            action_type="change_order",
            recipient=client_obj.email if client_obj else "unknown",
            content={"job_title": job.title, "reason": tool_input["reason"],
                     "co_total": co_total, "revised_total": revised_total_preview,
                     "line_items": line_items},
            contractor_name=contractor.name if contractor else "",
            client_name=client_obj.name if client_obj else "",
        )
        audit_log(db, memory.contractor_id, "outbound_review",
                  subject=f"Change order for '{job.title}' — {'APPROVED' if review_result.approved else 'BLOCKED'}",
                  detail=review_result.raw, channel="agent", initiated_by="agent")
        if not review_result.approved:
            return format_block_message(review_result, "change order")
        # ────────────────────────────────────────────────────────────────────

        from src.documents.change_order import generate_change_order
        pdf_path, co_number, revised_total = generate_change_order(
            job=job,
            contractor=contractor,
            client=job.client,
            reason=tool_input["reason"],
            line_items=line_items,
        )
        # Update job quoted amount
        job.quoted_amount = (job.quoted_amount or 0) + co_total
        db.commit()

        # Email to client if possible
        client_obj = job.client
        client_email = client_obj.email if client_obj else None
        sent_str = ""
        if client_email and contractor and contractor.gmail_refresh_token:
            try:
                _send_quote_email(
                    gmail_token=contractor.gmail_refresh_token,
                    to=client_email,
                    subject=f"Change Order {co_number} — {job.title}",
                    body=(
                        f"Hi {client_obj.name.split()[0]},\n\n"
                        f"Please find the change order for additional work on '{job.title}'.\n\n"
                        f"Additional amount: ${co_total:,.2f}\n"
                        f"Revised total: ${revised_total:,.2f}\n\n"
                        f"Please sign and return at your earliest convenience.\n\n"
                        f"{contractor.name}\n{contractor.business_name}\n{contractor.phone}"
                    ),
                    pdf_path=pdf_path,
                    pdf_filename=f"{co_number}.pdf",
                )
                sent_str = f" Sent to {client_email}."
            except Exception:
                sent_str = f" Email failed — PDF at {pdf_path}."
        else:
            sent_str = f" No client email — PDF at {pdf_path}."

        audit_log(db, memory.contractor_id, "change_order_created",
                  subject=f"{co_number} for '{job.title}' +${co_total:,.2f}",
                  channel="agent", initiated_by="agent")
        return f"Change order {co_number} created. +${co_total:,.2f} → revised total ${revised_total:,.2f}.{sent_str}"

    elif tool_name == "generate_permit_prep":
        job = memory.find_job(tool_input["job_title_hint"])
        if not job:
            return f"Could not find job matching '{tool_input['job_title_hint']}'."
        contractor = memory.get_contractor()
        client_obj = job.client
        from src.documents.permit import generate_permit_prep
        path = generate_permit_prep(job=job, contractor=contractor, client=client_obj,
                                    start_date=tool_input.get("start_date"))
        audit_log(db, memory.contractor_id, "permit_prep_generated",
                  subject=f"Permit prep for '{job.title}'", detail=path,
                  channel="agent", initiated_by="agent")
        # Email to contractor
        contractor_email = contractor.work_email or contractor.email if contractor else None
        if contractor_email and contractor and contractor.gmail_refresh_token:
            try:
                _send_quote_email(
                    gmail_token=contractor.gmail_refresh_token,
                    to=contractor_email,
                    subject=f"Permit Prep — {job.title}",
                    body=f"Permit prep sheet for '{job.title}' attached. Review and submit to your local building department.",
                    pdf_path=path,
                    pdf_filename=f"permit_prep_{job.title[:20].replace(' ','_')}.pdf",
                )
                return f"Permit prep generated and emailed to you — {path}"
            except Exception:
                pass
        return f"Permit prep generated: {path}"

    elif tool_name == "generate_picklist":
        job = memory.find_job(tool_input["job_title_hint"])
        if not job:
            return f"Could not find job matching '{tool_input['job_title_hint']}'."
        materials = tool_input.get("materials")
        if not materials:
            expenses = db.query(Expense).filter(
                Expense.job_id == job.id, Expense.category == "materials"
            ).all()
            materials = [{"description": e.description, "qty": 1, "unit": "each"} for e in expenses]
        if not materials:
            return f"No materials found for '{job.title}'. Provide a materials list."
        contractor = memory.get_contractor()
        supply_email = tool_input.get("supply_house_email")
        pickup_date = tool_input.get("pickup_date", "ASAP")
        lines = [
            f"PICK LIST — {job.title}",
            f"Contractor: {contractor.name} / {contractor.business_name}",
            f"Pickup: {pickup_date}",
            "─" * 40,
        ]
        for m in materials:
            lines.append(f"  [{m['qty']} {m.get('unit','ea')}]  {m['description']}")
        picklist_text = "\n".join(lines)
        if supply_email and contractor and contractor.gmail_refresh_token:
            try:
                from src.email_client import GmailClient
                gmail = GmailClient(contractor.gmail_refresh_token)
                loop = asyncio.get_event_loop()
                try:
                    loop.run_until_complete(gmail.send(
                        to=supply_email,
                        subject=f"Will-Call Pick List — {job.title} — {pickup_date}",
                        body=picklist_text,
                    ))
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(gmail.send(to=supply_email, subject=f"Will-Call Pick List — {job.title}", body=picklist_text))
                    loop.close()
                return f"Pick list sent to {supply_email} ({len(materials)} items, pickup {pickup_date})."
            except Exception as e:
                return f"Pick list ready but email failed: {e}\n{picklist_text}"
        return f"Pick list for '{job.title}':\n{picklist_text}"

    elif tool_name == "export_tax_csv":
        import csv
        import io
        from src.models.expense import Expense as ExpenseModel
        from datetime import date

        start_str = tool_input.get("start_date")
        end_str = tool_input.get("end_date")
        now = datetime.now(timezone.utc)

        if start_str:
            start_dt = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)
        else:
            start_dt = datetime(now.year, 1, 1, tzinfo=timezone.utc)

        if end_str:
            end_dt = datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc)
        else:
            end_dt = now

        expenses = (
            db.query(ExpenseModel)
            .filter(
                ExpenseModel.contractor_id == memory.contractor_id,
                ExpenseModel.created_at >= start_dt,
                ExpenseModel.created_at <= end_dt,
            )
            .order_by(ExpenseModel.created_at)
            .all()
        )

        output_path = f"/tmp/fieldhand_expenses_{now.strftime('%Y%m%d_%H%M%S')}.csv"
        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Category", "Description", "Vendor", "Amount", "Job"])
            total = 0
            by_category = {}
            for e in expenses:
                job_title = e.job.title if e.job else "Overhead"
                writer.writerow([
                    e.created_at.strftime("%Y-%m-%d") if e.created_at else "",
                    e.category,
                    e.description,
                    e.vendor or "",
                    f"{e.amount:.2f}",
                    job_title,
                ])
                total += e.amount
                by_category[e.category] = by_category.get(e.category, 0) + e.amount

        breakdown = " | ".join(f"{cat}: ${amt:,.2f}" for cat, amt in sorted(by_category.items()))
        audit_log(db, memory.contractor_id, "tax_csv_exported",
                  subject=f"Expense CSV {start_str or str(now.year)+'-01-01'} to {end_str or 'today'}",
                  channel="agent", initiated_by="agent")
        return (
            f"CSV exported: {output_path}\n"
            f"{len(expenses)} expenses, ${total:,.2f} total\n"
            f"By category: {breakdown}"
        )

    elif tool_name == "web_search":
        return web_search_sync(tool_input["query"], tool_input.get("max_results", 3))

    elif tool_name == "query_business":
        return _query_business(tool_input["question"], memory.contractor_id, db)

    elif tool_name == "calculate":
        try:
            result = safe_eval(tool_input["expression"])
            return f"{tool_input['expression']} = {result:,.4f}".rstrip('0').rstrip('.')
        except Exception as e:
            return f"Math error: {e}"

    elif tool_name == "check_email":
        contractor = memory.get_contractor()
        if not contractor or not contractor.gmail_refresh_token:
            return "Email not connected."
        from src.email_client import GmailClient
        gmail = GmailClient(contractor.gmail_refresh_token)
        try:
            loop = asyncio.get_event_loop()
            emails = loop.run_until_complete(gmail.get_unread(max_results=tool_input.get("max_results", 10)))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            emails = loop.run_until_complete(gmail.get_unread(max_results=tool_input.get("max_results", 10)))
            loop.close()
        if not emails:
            return "Inbox is clear — no unread emails."
        lines = [f"Found {len(emails)} unread email(s):\n"]
        for e in emails:
            lines.append(f"From: {e.sender_name}\nSubject: {e.subject}\nDate: {e.received_at}\nPreview: {e.body[:200].strip()}\n")
        return "\n".join(lines)

    elif tool_name == "send_email":
        contractor = memory.get_contractor()
        if not contractor or not contractor.gmail_refresh_token:
            return "Email not connected."

        # ── Outbound review ──────────────────────────────────────────────────
        _rev = outbound_review(
            action_type="email",
            recipient=tool_input["to"],
            content={"subject": tool_input["subject"], "body": tool_input["body"]},
            contractor_name=contractor.name if contractor else "",
        )
        audit_log(db, memory.contractor_id, "outbound_review",
                  subject=f"Email to {tool_input['to']} — {'APPROVED' if _rev.approved else 'BLOCKED'}",
                  detail=_rev.raw, channel="agent", initiated_by="agent")
        if not _rev.approved:
            return format_block_message(_rev, "email")
        # ────────────────────────────────────────────────────────────────────

        # Stage the email — save as pending and ask contractor to confirm
        import json as _json
        contractor.pending_email = _json.dumps({
            "to": tool_input["to"],
            "subject": tool_input["subject"],
            "body": tool_input["body"],
            "pdf_path": None,
        })
        db.commit()
        audit_log(db, memory.contractor_id, "email_staged",
                  subject=f"Pending approval: to {tool_input['to']} — {tool_input['subject']}",
                  channel="agent", initiated_by="agent")
        preview = tool_input["body"][:300] + ("..." if len(tool_input["body"]) > 300 else "")
        return (
            f"Draft ready to send to {tool_input['to']}:\n"
            f"Subject: {tool_input['subject']}\n\n"
            f"{preview}\n\n"
            f"Reply YES to send, or tell me what to change."
        )

    elif tool_name == "draft_email":
        contractor = memory.get_contractor()
        if not contractor or not contractor.gmail_refresh_token:
            return f"Email not connected. Can't send to {tool_input['to']}."

        # ── Outbound review ──────────────────────────────────────────────────
        _rev2 = outbound_review(
            action_type="email",
            recipient=tool_input["to"],
            content={"subject": tool_input["subject"], "body": tool_input["body"]},
            contractor_name=contractor.name if contractor else "",
        )
        audit_log(db, memory.contractor_id, "outbound_review",
                  subject=f"Draft email to {tool_input['to']} — {'APPROVED' if _rev2.approved else 'BLOCKED'}",
                  detail=_rev2.raw, channel="agent", initiated_by="agent")
        if not _rev2.approved:
            return format_block_message(_rev2, "email")
        # ────────────────────────────────────────────────────────────────────

        # Stage the email — same as send_email, requires contractor approval
        import json as _json
        contractor.pending_email = _json.dumps({
            "to": tool_input["to"],
            "subject": tool_input["subject"],
            "body": tool_input["body"],
            "pdf_path": None,
        })
        db.commit()
        audit_log(db, memory.contractor_id, "email_staged",
                  subject=f"Pending approval: to {tool_input['to']} — {tool_input['subject']}",
                  channel="agent", initiated_by="agent")
        preview = tool_input["body"][:300] + ("..." if len(tool_input["body"]) > 300 else "")
        return (
            f"Draft ready to send to {tool_input['to']}:\n"
            f"Subject: {tool_input['subject']}\n\n"
            f"{preview}\n\n"
            f"Reply YES to send, or tell me what to change."
        )

    elif tool_name == "update_contractor_profile":
        contractor = memory.get_contractor()
        if not contractor:
            return "Contractor not found."
        updatable_fields = [
            "license_no", "license_expiration", "business_address", "email",
            "markup_pct", "invoice_terms", "ein", "gl_carrier", "gl_policy_number",
            "gl_expiration", "wc_exempt", "wc_carrier", "wc_policy", "wc_expiration",
            "insurance_agent_name", "insurance_agent_phone", "service_area", "labor_rate",
        ]
        saved = []
        for field in updatable_fields:
            if field in tool_input and tool_input[field] is not None:
                setattr(contractor, field, tool_input[field])
                saved.append(field)
        if saved:
            db.commit()
            audit_log(db, memory.contractor_id, "profile_updated",
                      subject=f"Fields updated: {', '.join(saved)}",
                      channel="agent", initiated_by="agent")
            return f"Profile updated: {', '.join(saved)}."
        return "No fields provided to update."

    elif tool_name == "update_agent_memory":
        contractor = memory.get_contractor()
        if not contractor:
            return "Contractor not found."
        note = tool_input["note"]
        category = tool_input.get("category", "business")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = f"[{timestamp}][{category}] {note}"
        existing = contractor.agent_notes or ""
        contractor.agent_notes = (existing + "\n" + entry).strip()
        db.commit()
        audit_log(db, memory.contractor_id, "agent_memory_updated",
                  subject=f"Note saved [{category}]", detail=note,
                  channel="agent", initiated_by="agent")
        return f"Noted: {note}"

    elif tool_name == "update_behavior_rules":
        contractor = memory.get_contractor()
        if not contractor:
            return "Contractor not found."
        action = tool_input["action"]
        rule = tool_input.get("rule", "")
        existing = contractor.custom_rules or ""

        if action == "add":
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            entry = f"- [{timestamp}] {rule}"
            contractor.custom_rules = (existing + "\n" + entry).strip()
            result = f"Rule added: {rule}"
        elif action == "remove":
            lines = [l for l in existing.split("\n") if rule.lower() not in l.lower()]
            contractor.custom_rules = "\n".join(lines).strip()
            result = f"Rule removed: {rule}"
        elif action == "replace_all":
            contractor.custom_rules = tool_input.get("rules_text", "").strip()
            result = "All rules replaced."
        else:
            result = "Unknown action."

        db.commit()
        audit_log(db, memory.contractor_id, "behavior_rules_updated",
                  subject=f"Rules {action}: {rule[:60]}",
                  channel="agent", initiated_by="agent")
        return result

    return f"Unknown tool: {tool_name}"


# ─────────────────────────────────────────────────────────────────────────── #
# PROFILE GAP CHECK
# ─────────────────────────────────────────────────────────────────────────── #

def _check_profile_gaps(contractor, task_type: str) -> list[str]:
    """Return list of missing fields needed for the given task type.

    task_type can be: 'quote', 'send', 'permit', 'tax_csv', 'supply_order'
    """
    missing = []
    if task_type == "quote":
        if not contractor.license_no:
            missing.append("license_no")
        if not contractor.markup_pct or contractor.markup_pct == 0:
            missing.append("markup_pct")
    elif task_type == "send":
        if not contractor.email and not contractor.work_email:
            missing.append("email")
    elif task_type == "permit":
        if not contractor.license_no:
            missing.append("license_no")
        if not contractor.business_address:
            missing.append("business_address")
        if not contractor.gl_carrier:
            missing.append("gl_carrier")
        if not contractor.gl_policy_number:
            missing.append("gl_policy_number")
        if not contractor.gl_expiration:
            missing.append("gl_expiration")
        if not contractor.wc_carrier and not contractor.wc_exempt:
            missing.append("wc_carrier_or_exempt")
    elif task_type == "tax_csv":
        if not contractor.ein:
            missing.append("ein")
    elif task_type == "supply_order":
        if not contractor.business_address:
            missing.append("business_address")
    return missing





def _detect_patterns(memory, db, contractor_id: str) -> list[str]:
    """
    Scan for business patterns worth surfacing proactively.
    Called during every agent turn — lightweight, no extra API calls.
    Returns list of alert strings (empty = nothing to flag).
    """
    from src.models.invoice import InvoiceStatus
    from src.models.job import JobStatus
    from src.memory import _as_utc
    from datetime import datetime, timezone

    alerts = []
    now = datetime.now(timezone.utc)
    contractor_id_str = contractor_id

    # Over-budget jobs trending badly
    jobs = db.query(Job).filter(
        Job.contractor_id == contractor_id_str,
        Job.status == JobStatus.ACTIVE,
    ).all()
    for job in jobs:
        if job.budget_used_pct and job.budget_used_pct >= 90:
            alerts.append(
                f"⚠ {job.title[:25]} is at {job.budget_used_pct:.0f}% of budget — consider a change order before you go deeper."
            )

    # Client with 3+ invoices always slow-paying
    invoices = db.query(Invoice).filter(Invoice.contractor_id == contractor_id_str).all()
    client_pay_days = {}
    for inv in invoices:
        if inv.status == InvoiceStatus.PAID and inv.paid_at and inv.sent_at:
            days = (_as_utc(inv.paid_at) - _as_utc(inv.sent_at)).days
            job = inv.job
            if job and job.client_id:
                if job.client_id not in client_pay_days:
                    client_pay_days[job.client_id] = []
                client_pay_days[job.client_id].append((days, job.client.name if job.client else 'Client'))
    for cid, records in client_pay_days.items():
        if len(records) >= 3:
            avg = sum(d for d, _ in records) / len(records)
            name = records[0][1]
            if avg > 25:
                alerts.append(
                    f"Pattern: {name} averages {avg:.0f} days to pay across {len(records)} invoices. Consider requiring a deposit."
                )

    # Multiple jobs over budget this month
    over_budget_count = sum(1 for j in jobs if j.is_over_budget)
    if over_budget_count >= 2:
        alerts.append(
            f"Pattern: {over_budget_count} active jobs are over budget. Your estimates may need a buffer increase."
        )

    return alerts


def _send_quote_email(gmail_token: str, to: str, subject: str, body: str, pdf_path: str, pdf_filename: str):
    """Send an email with a PDF attachment via Gmail."""
    import base64, httpx as _httpx
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    from src.email_client import GmailClient

    gmail = GmailClient(gmail_token)
    try:
        loop = asyncio.get_event_loop()
        access_token = loop.run_until_complete(gmail._get_access_token())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        access_token = loop.run_until_complete(gmail._get_access_token())
        loop.close()

    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    with open(pdf_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{pdf_filename}"')
    msg.attach(part)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    async def _send():
        async with _httpx.AsyncClient() as hc:
            r = await hc.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"raw": raw},
            )
            r.raise_for_status()

    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_send())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_send())
        loop.close()


# ─────────────────────────────────────────────────────────────────────────── #
# MAIN AGENT CLASS
# ─────────────────────────────────────────────────────────────────────────── #

class ContractorAgent:
    def __init__(self, db: Session, contractor_id: str):
        self.db = db
        self.contractor_id = contractor_id
        self.memory = Memory(db, contractor_id)

    def chat(self, user_message: str, channel: str = "sms") -> tuple[str, MessageCost]:
        """
        Process a message. Returns (response_text, MessageCost).
        Now returns cost data so callers can log/display it.
        """
        self.memory.store_message("user", user_message, channel)

        # ── Step 1: Classify with Haiku ───────────────────────────────────
        # Build a brief context hint for the classifier
        recent = self.memory.get_recent_messages(limit=3)
        context_hint = ' | '.join(m['content'][:60] for m in recent[-3:]) if recent else ''

        routing = classify(user_message, context_hint)
        tier = routing.get('tier', 2)
        needs_thinking = routing.get('needs_thinking', False)
        thinking_budget = routing.get('thinking_budget', 0)
        needs_search = routing.get('needs_web_search', False)
        categories = routing.get('tools_needed', list(TOOL_CATEGORIES.keys() if False else []))

        # If web search needed, make sure 'search' category included
        if needs_search and 'search' not in categories:
            categories.append('search')

        # ── Step 2: Select only needed tools ─────────────────────────────
        active_tools = get_tools_for_categories(categories, TOOLS)

        # ── Step 3: Build context + system prompt ─────────────────────────
        context = self.memory.get_context_snapshot()
        contractor = context.get("contractor", {})

        # Static prefix (cache-friendly): base instructions + tool schemas
        # Dynamic suffix: context snapshot + date (changes every call)
        contractor_obj = self.memory.get_contractor()
        agent_notes = contractor_obj.agent_notes or "None yet."
        custom_rules = contractor_obj.custom_rules or "None set."

        system = SYSTEM_PROMPT.format(
            name=contractor.get("name", "the contractor"),
            trade=contractor.get("trade", "trade"),
            context=json.dumps(context, indent=2, default=str),
            today=datetime.now(timezone.utc).strftime("%A, %B %d, %Y"),
            agent_notes=agent_notes,
            custom_rules=custom_rules,
        )

        history = self.memory.get_recent_messages(limit=15)
        messages = history[:-1] + [{"role": "user", "content": user_message}]

        # ── Step 4: Initialize cost tracker ──────────────────────────────
        cost = MessageCost(
            tier=tier,
            model="claude-sonnet-4-5",
            classifier_input=routing.get('classifier_tokens', {}).get('input', 0),
            classifier_output=routing.get('classifier_tokens', {}).get('output', 0),
        )

        # ── Step 5: Run agentic loop ──────────────────────────────────────
        response_text = self._run_agentic_loop(
            system=system,
            messages=messages,
            tools=active_tools,
            needs_thinking=needs_thinking,
            thinking_budget=thinking_budget,
            cost=cost,
        )

        # ── Step 6: Proactive pattern detection ───────────────────────────
        patterns = _detect_patterns(self.memory, self.db, self.contractor_id)
        if patterns:
            # Append to response if there are short, relevant patterns
            pattern_str = '\n'.join(f'\n{p}' for p in patterns[:2])  # max 2 per turn
            response_text = response_text + pattern_str

        self.memory.store_message("assistant", response_text, channel)
        return response_text, cost

    def _run_agentic_loop(
        self,
        system: str,
        messages: list,
        tools: list,
        needs_thinking: bool,
        thinking_budget: int,
        cost: MessageCost,
    ) -> str:
        loop_messages = list(messages)
        final_text = ""
        max_rounds = 12 if needs_thinking else 8

        for _ in range(max_rounds):
            kwargs = dict(
                model="claude-sonnet-4-5",
                max_tokens=thinking_budget + 2048 if thinking_budget else 2048,
                system=system,
                messages=loop_messages,
                tools=tools if tools else TOOLS,
            )

            # Enable extended thinking for tier 2/3
            if needs_thinking and thinking_budget > 0:
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget,
                }
                # Extended thinking requires the beta client
                response = client.beta.messages.create(
                    **kwargs,
                    betas=["interleaved-thinking-2025-05-14"],
                )
            else:
                response = client.messages.create(**kwargs)

            # Accumulate token costs
            cost.agent_input += response.usage.input_tokens
            cost.agent_output += response.usage.output_tokens
            if hasattr(response.usage, 'cache_read_input_tokens'):
                cost.agent_cache_read += response.usage.cache_read_input_tokens or 0
            if hasattr(response.usage, 'cache_creation_input_tokens'):
                cost.agent_cache_write += response.usage.cache_creation_input_tokens or 0

            # Collect thinking token count
            for block in response.content:
                if hasattr(block, 'type') and block.type == 'thinking':
                    cost.thinking_tokens += getattr(block, 'thinking_token_count', 0) or len(getattr(block, 'thinking', '')) // 4

            # Collect text
            text_parts = [b.text for b in response.content if hasattr(b, 'text')]
            if text_parts:
                final_text = " ".join(text_parts)

            if response.stop_reason == "end_turn":
                break

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break

            loop_messages.append({"role": "assistant", "content": response.content})

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
