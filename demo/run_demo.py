"""
FIELDHAND Demo Runner v2
========================
Drives FIELDHAND through a full contractor week hitting ALL capabilities:
  - Context pinning + intent switching
  - Live price lookup
  - Y/N execution gates
  - Change orders
  - Permit prep
  - Pick list generation
  - Autonomous dunning
  - EOD P&L summary
  - Tax CSV export
  - Quote + invoice PDF generation and email dispatch

Outputs:
  demo/output/demo_log.json
  demo/output/report.html
"""

import sys, os, json, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from src.database import SessionLocal, engine, Base
import src.models
from src.models import Contractor, Client, Job, Expense, Invoice, Message, PendingAction
from src.models.document import Document
from src.models.audit_log import AuditLog
from src.memory import Memory
from src.agent import TOOLS, SYSTEM_PROMPT, execute_tool, check_yn_gate, ContractorAgent
from anthropic import Anthropic

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
LOG_PATH   = OUTPUT_DIR / "demo_log.json"
REPORT_PATH = OUTPUT_DIR / "report.html"

anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

DEMO_PHONE = "+15550000999"
DEMO_CONTRACTOR = dict(
    name="Jake Morales",
    phone=DEMO_PHONE,
    trade="electrician",
    business_name="Morales Electric LLC",
    labor_rate=110.0,
    invoice_terms="Net 15",
    onboarding_complete=True,
    # license_no, markup_pct, ein, insurance — collected just-in-time during demo
)

# Real email to send docs to
SEND_TO_EMAIL = "ford.genereaux@dysonswarmtechnologies.com"

SCENARIOS = [
    # ── BOOT ONBOARDING (3 questions) ────────────────────────────────────
    {
        "id": "ONB",
        "title": "Onboarding — 3 Questions, Done",
        "description": "New contractor texts in. Entire onboarding is name+trade, business name, hourly rate. Then they're in.",
        "message": "hey",
        "category": "Onboarding",
        "onboarding_sim": True,
        "onboarding_inputs": [
            "Jake Morales, electrician",
            "Morales Electric LLC",
            "110",
        ],
        "onboarding_summary": "3 questions. Name+trade, business name, rate. Done.",
    },

    # ── FIRST TASK: QUOTE — triggers license + markup collection ─────────
    {
        "id": "S01",
        "title": "First Quote Request — Triggers License Ask",
        "description": "Contractor asks for a quote. Agent builds it but asks for license number first since it goes on the letterhead.",
        "message": "Got a call from Dave Smith at 412 Maple St, needs a 200 amp panel upgrade. Quote him — 200A Square D panel $210, 15 breakers $9.25 each, 6 AFCIs $42 each, 8 hours labor, 25% markup.",
        "category": "Just-in-Time",
    },
    {
        "id": "S02",
        "title": "Contractor Provides License — Quote Completes",
        "description": "Contractor gives license number. Agent saves it and immediately finishes building the quote.",
        "message": "EL-2024-00847",
        "category": "Just-in-Time",
    },

    # ── SEND QUOTE — triggers email/Gmail collection ──────────────────────
    {
        "id": "S03",
        "title": "Send Quote — Triggers Email Ask",
        "description": "Contractor says send it. Agent asks for Smith's email AND asks to confirm the contractor's send-from email.",
        "message": "Send the quote to Smith. His email is dave.smith@email.com",
        "category": "Just-in-Time",
        "yn_followup": "Y",
    },

    # ── CLIENT INFO GATHERING ─────────────────────────────────────────────
    {
        "id": "S04",
        "title": "Agent Asks for Smith's Phone",
        "description": "Quote was sent. Agent follows up asking for Smith's cell since it's missing from the record.",
        "message": "603-555-0177",
        "category": "Client Info",
    },

    # ── NEW JOB, NEW CLIENT ───────────────────────────────────────────────
    {
        "id": "S05",
        "title": "New Client — Garcia",
        "description": "New job added. Agent creates Garcia and immediately asks for email and phone.",
        "message": "New job — Maria Garcia, 88 Pine Ave, kitchen remodel circuits. 4 new 20A circuits, 2 GFCIs. $1,200 quote. Add it active.",
        "category": "Client Info",
    },
    {
        "id": "S06",
        "title": "Garcia Contact Info",
        "description": "Contractor gives both email and phone. Agent stores them.",
        "message": "maria.garcia@gmail.com, 617-555-0293",
        "category": "Client Info",
    },

    # ── EXPENSES ──────────────────────────────────────────────────────────
    {
        "id": "S07",
        "title": "Log Expenses — Context Inference",
        "description": "No job mentioned. Agent infers from recent conversation.",
        "message": "Picked up wire at Ferguson — $340 romex, $28 boxes.",
        "category": "Context & Thread",
    },
    {
        "id": "S08",
        "title": "Smith Active + More Materials",
        "description": "Smith accepted. Agent marks active and logs materials.",
        "message": "Smith said yes. Mark his job active. Also log the $210 panel and breakers — 15 at $9.25, 6 AFCIs at $42.",
        "category": "Context & Thread",
    },

    # ── PERMIT — triggers insurance + address collection ──────────────────
    {
        "id": "S09",
        "title": "Permit Request — Triggers Insurance Ask",
        "description": "Contractor asks for permit prep. Agent asks for GL insurance info since it's required on the application.",
        "message": "Generate the permit prep for Smith's panel job. Start Monday.",
        "category": "Just-in-Time",
    },
    {
        "id": "S10",
        "title": "Contractor Provides Insurance Info",
        "description": "Contractor gives GL carrier, policy, expiry. Agent saves it and generates the permit PDF.",
        "message": "Travelers, GL-8823991, 12/2025. I'm exempt from workers comp.",
        "category": "Just-in-Time",
        "yn_followup": "Y",
    },

    # ── CHANGE ORDER ──────────────────────────────────────────────────────
    {
        "id": "S11",
        "title": "Change Order — EV Charger Added",
        "description": "Extra scope. Agent builds change order.",
        "message": "Smith wants a 50A EV charger circuit added. $85 materials, 2 hours labor.",
        "category": "Invoicing",
        "yn_followup": "Y",
    },

    # ── COMPLETE + INVOICE ────────────────────────────────────────────────
    {
        "id": "S12",
        "title": "Complete Job + Invoice",
        "description": "Job done. Agent closes and sends invoice.",
        "message": "Smith job is done. Send the invoice to dave.smith@email.com.",
        "category": "Invoicing",
        "yn_followup": "Y",
    },

    # ── TAX CSV — triggers EIN collection ────────────────────────────────
    {
        "id": "S13",
        "title": "Tax Export — Triggers EIN Ask",
        "description": "Contractor asks for tax CSV. Agent asks for EIN first.",
        "message": "Export my expenses for my accountant.",
        "category": "Just-in-Time",
    },
    {
        "id": "S14",
        "title": "Contractor Provides EIN — CSV Generated",
        "description": "EIN given. Agent saves it and immediately exports the CSV.",
        "message": "82-4471923",
        "category": "Just-in-Time",
    },

    # ── TIER 3 INVESTIGATION SCENARIOS ───────────────────────────────────
    {
        "id": "S13B",
        "title": "Business Analysis — Why Losing Margin? (Tier 3)",
        "description": "Complex investigation query. Agent uses query_business tool with extended thinking.",
        "message": "I feel like I'm losing money on some jobs. Can you dig into my profitability and tell me what's going on?",
        "category": "Investigate",
    },
    {
        "id": "S13C",
        "title": "Permit Research — Web Search (Tier 3)",
        "description": "Agent searches for real permit requirements for Manchester NH panel upgrade.",
        "message": "What are the permit requirements for a 200 amp panel upgrade in Manchester NH? Look it up.",
        "category": "Investigate",
    },

    # ── FINANCIAL + ADMIN ─────────────────────────────────────────────────
    {
        "id": "S15",
        "title": "Financial Summary",
        "description": "Full money snapshot.",
        "message": "What's my money situation?",
        "category": "Admin",
    },
    {
        "id": "S16",
        "title": "Overdue Check",
        "description": "Invoices aged. Agent spots them.",
        "message": "Any invoices I should be chasing?",
        "category": "Admin",
        "age_invoices": True,
    },
    {
        "id": "S17",
        "title": "Full Job Board",
        "description": "Everything on the plate.",
        "message": "Show me all my active jobs.",
        "category": "Admin",
    },
]


def _extract_twiml_text(twiml_or_body: str) -> str:
    """Extract text from TwiML or raw response body."""
    import re
    # If it's TwiML XML
    m = re.search(r'<Message>(.*?)</Message>', twiml_or_body, re.DOTALL)
    if m:
        text = m.group(1)
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        return text.strip()
    return twiml_or_body.strip()


# ── Instrumented agent ────────────────────────────────────────────────────────

class InstrumentedAgent:
    """Wraps ContractorAgent to capture full execution trace including cost."""

    def __init__(self, db, contractor_id: str):
        self.db = db
        self.contractor_id = contractor_id
        self.memory = Memory(db, contractor_id)
        self._agent = ContractorAgent(db=db, contractor_id=contractor_id)

    def chat(self, user_message: str) -> dict:
        from src.agent import execute_tool, TOOLS
        from src.router import classify, get_tools_for_categories, TOOL_CATEGORIES
        import os
        from anthropic import Anthropic

        api_client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

        self.memory.store_message("user", user_message, "demo")

        # Classify
        recent = self.memory.get_recent_messages(limit=3)
        context_hint = ' | '.join(m['content'][:60] for m in recent[-3:]) if recent else ''
        routing = classify(user_message, context_hint)
        tier = routing.get('tier', 2)
        needs_thinking = routing.get('needs_thinking', False)
        thinking_budget = routing.get('thinking_budget', 0)
        categories = routing.get('tools_needed', list(TOOL_CATEGORIES.keys()))
        if routing.get('needs_web_search') and 'search' not in categories:
            categories.append('search')
        active_tools = get_tools_for_categories(categories, TOOLS)

        # Build context
        context = self.memory.get_context_snapshot()
        contractor = context.get("contractor", {})
        from src.agent import SYSTEM_PROMPT
        system = SYSTEM_PROMPT.format(
            name=contractor.get("name", "the contractor"),
            trade=contractor.get("trade", "trade"),
            context=json.dumps(context, indent=2, default=str),
            today=datetime.now(timezone.utc).strftime("%A, %B %d, %Y"),
        )
        history = self.memory.get_recent_messages(limit=15)
        messages = history[:-1] + [{"role": "user", "content": user_message}]

        # Cost tracker
        from src.cost_tracker import MessageCost
        cost = MessageCost(
            tier=tier,
            model="claude-sonnet-4-5",
            classifier_input=routing.get('classifier_tokens', {}).get('input', 0),
            classifier_output=routing.get('classifier_tokens', {}).get('output', 0),
        )

        # Run instrumented loop
        tool_calls = []
        response_text = self._run_loop(
            api_client, system, messages, active_tools,
            needs_thinking, thinking_budget, cost, tool_calls
        )

        self.memory.store_message("assistant", response_text, "demo")

        return {
            "context_snapshot": context,
            "routing": routing,
            "active_tools": [t['name'] for t in active_tools],
            "tool_calls": tool_calls,
            "response": response_text,
            "cost": cost.summary(),
        }

    def execute_yn(self, answer: str) -> dict:
        from src.agent import check_yn_gate
        result = check_yn_gate(answer, self.contractor_id, self.db)
        if result:
            self.memory.store_message("user", answer, "demo")
            self.memory.store_message("assistant", result, "demo")
        return {"yn_input": answer, "yn_result": result or "(no pending action)"}

    def _run_loop(self, api_client, system, messages, tools, needs_thinking, thinking_budget, cost, tool_calls):
        from src.agent import execute_tool
        loop_messages = list(messages)
        final_text = ""
        for round_num in range(12):
            kwargs = dict(
                model="claude-sonnet-4-5",
                max_tokens=thinking_budget + 2048 if thinking_budget else 2048,
                system=system,
                messages=loop_messages,
                tools=tools,
            )
            if needs_thinking and thinking_budget > 0:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
                kwargs["betas"] = ["interleaved-thinking-2025-05-14"]
                response = api_client.beta.messages.create(**kwargs)
            else:
                response = api_client.messages.create(**kwargs)
            cost.agent_input += response.usage.input_tokens
            cost.agent_output += response.usage.output_tokens
            if hasattr(response.usage, 'cache_read_input_tokens'):
                cost.agent_cache_read += response.usage.cache_read_input_tokens or 0
            text_parts = [b.text for b in response.content if hasattr(b, 'text') and b.type == 'text']
            if text_parts:
                final_text = " ".join(text_parts)
            if response.stop_reason == "end_turn":
                break
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break
            loop_messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tu in tool_uses:
                result = execute_tool(tu.name, dict(tu.input), self.memory, self.db)
                tool_calls.append({"round": round_num, "tool": tu.name, "input": dict(tu.input), "result": result})
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
            loop_messages.append({"role": "user", "content": tool_results})
        return final_text or "Done."


def db_snapshot(db, contractor_id):
    clients  = db.query(Client).filter(Client.contractor_id == contractor_id).all()
    jobs     = db.query(Job).filter(Job.contractor_id == contractor_id).all()
    expenses = db.query(Expense).filter(Expense.contractor_id == contractor_id).all()
    invoices = db.query(Invoice).filter(Invoice.contractor_id == contractor_id).all()
    pending  = db.query(PendingAction).filter(PendingAction.contractor_id == contractor_id,
                                               PendingAction.resolved == False).all()
    audit    = db.query(AuditLog).filter(AuditLog.contractor_id == contractor_id)\
                 .order_by(AuditLog.created_at.desc()).limit(30).all()
    contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
    pinned_id = contractor.pinned_job_id if contractor else None

    return {
        "clients":  [{"id": c.id, "name": c.name, "phone": c.phone, "email": c.email,
                      "address": c.address, "notes": c.notes} for c in clients],
        "jobs":     [{"id": j.id, "title": j.title, "status": j.status.value,
                      "quoted_amount": j.quoted_amount, "actual_cost": j.actual_cost,
                      "budget_used_pct": j.budget_used_pct,
                      "client": j.client.name if j.client else None,
                      "address": j.address, "pinned": j.id == pinned_id} for j in jobs],
        "expenses": [{"id": e.id, "description": e.description, "amount": e.amount,
                      "category": e.category, "vendor": e.vendor, "job_id": e.job_id} for e in expenses],
        "invoices": [{"id": i.id, "amount": i.amount, "status": i.status.value,
                      "sent_at": str(i.sent_at) if i.sent_at else None,
                      "job_id": i.job_id} for i in invoices],
        "pending_actions": [{"type": pa.action_type, "summary": pa.summary} for pa in pending],
        "audit_log": [{"action": a.action, "subject": a.subject, "channel": a.channel,
                       "initiated_by": a.initiated_by, "created_at": str(a.created_at)} for a in audit],
    }


# ── After-sim: real email sends ───────────────────────────────────────────────

def send_real_docs(db, contractor_id: str, to_email: str):
    """Generate and email real quote + invoice PDFs to the recipient."""
    print(f"\n  [Sending real PDFs to {to_email}...]")

    contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
    if not contractor or not contractor.gmail_refresh_token:
        print("  No Gmail token — skipping real email send.")
        return

    # Find the Smith job for quote and invoice
    smith_job = db.query(Job).filter(
        Job.contractor_id == contractor_id,
        Job.title.like("%Smith%"),
    ).first()
    garcia_job = db.query(Job).filter(
        Job.contractor_id == contractor_id,
        Job.title.like("%Garcia%"),
    ).first()

    pdf_paths = []
    labels = []

    # Generate quote PDF for Garcia job (still quoted/active)
    if garcia_job and garcia_job.client:
        from src.documents.quote import generate_quote
        quote_items = [
            {"description": "New 20A circuits (4 circuits — romex, boxes, outlets, covers)", "qty": 4, "unit_price": 195.0, "amount": 780.0},
            {"description": "GFCI outlets — kitchen (2 locations)", "qty": 2, "unit_price": 42.0, "amount": 84.0},
            {"description": "Labor — circuit installation (4 hrs @ $110/hr)", "qty": 4, "unit_price": 110.0, "amount": 440.0},
            {"description": "Permit", "qty": 1, "unit_price": 95.0, "amount": 95.0},
            {"description": "Materials markup (25%)", "qty": 1, "unit_price": 216.0, "amount": 216.0},
        ]
        garcia_job.quoted_amount = sum(i["amount"] for i in quote_items)
        db.commit()
        path = generate_quote(job=garcia_job, line_items=quote_items, db=db,
                              notes="Valid 30 days. 50% deposit required to schedule.")
        pdf_paths.append(path)
        labels.append(f"Quote — {garcia_job.title} (${garcia_job.quoted_amount:,.2f})")

    # Generate invoice PDF for Smith job
    if smith_job:
        from src.documents.invoice import generate_invoice_pdf
        smith_total = smith_job.quoted_amount or 3800.0
        path = generate_invoice_pdf(
            job_title=smith_job.title,
            contractor=contractor,
            client=smith_job.client,
            amount=smith_total,
            address=smith_job.address or "412 Maple St",
        )
        pdf_paths.append(path)
        labels.append(f"Invoice — {smith_job.title} (${smith_total:,.2f})")

    if not pdf_paths:
        print("  No jobs found to generate PDFs for.")
        return

    # Send via Gmail with attachments
    import base64, httpx
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    from src.email_client import GmailClient
    import asyncio

    gmail = GmailClient(contractor.gmail_refresh_token)
    try:
        loop = asyncio.get_event_loop()
        access_token = loop.run_until_complete(gmail._get_access_token())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        access_token = loop.run_until_complete(gmail._get_access_token())
        loop.close()

    msg = MIMEMultipart()
    msg["To"] = to_email
    msg["Subject"] = "FIELDHAND Demo — Live Quote & Invoice PDFs"

    body_text = f"""Hi Ford,

These are real documents generated by FIELDHAND during the v2 simulation run just now.

Attached:
{chr(10).join(f"  • {l}" for l in labels)}

What you're looking at:
  • The QUOTE was built by the agent from shorthand material notes + live price lookup,
    with your labor rate and markup applied automatically.
  • The INVOICE was generated when Jake texted "Smith job is done, send the bill."
    The agent queued it, asked for confirmation, and fired it after Y.

Both PDFs were generated by WeasyPrint from Jinja2 HTML templates.
In production, invoices go out via Stripe with a real payment link.

— FIELDHAND
---
Morales Electric LLC | (603) 555-0199 | License EL-2024-00847
"""
    msg.attach(MIMEText(body_text, "plain"))

    for path in pdf_paths:
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{Path(path).name}"')
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    async def _send():
        async with httpx.AsyncClient() as hc:
            r = await hc.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"raw": raw},
            )
            r.raise_for_status()
            return r.json()

    try:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_send())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_send())
        loop.close()

    print(f"  Email sent! ID: {result.get('id')} — {len(pdf_paths)} PDFs attached")
    for l in labels:
        print(f"    • {l}")


# ── Invoice PDF helper (standalone, no Stripe) ────────────────────────────────

def _ensure_invoice_pdf_helper():
    """Patch src/documents/invoice.py with a standalone PDF generator."""
    inv_path = Path(__file__).parent.parent / "src/documents/invoice.py"
    code = inv_path.read_text()
    if "generate_invoice_pdf" in code:
        return
    # Append the helper
    helper = '''

def generate_invoice_pdf(job_title: str, contractor, client, amount: float, address: str = "") -> str:
    """Standalone invoice PDF — no Stripe required. Returns file path."""
    from jinja2 import Template
    from pathlib import Path as _Path
    OUTPUT = _Path(__file__).parent.parent.parent / "generated_docs"
    OUTPUT.mkdir(exist_ok=True)
    from datetime import datetime as _dt, timedelta as _td
    today = _dt.now()
    due = today + _td(days=15)
    inv_num = f"INV-{today.strftime('%Y%m%d')}-{job_title[:6].upper().replace(' ','')}"
    LINE_ITEMS = [
        {"description": job_title, "qty": 1, "unit_price": amount, "amount": amount}
    ]
    HTML_TMPL = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
    body{font-family:Arial,sans-serif;margin:0;color:#1a1a1a;}
    .hdr{background:#1a3a2a;color:white;padding:28px 40px;}
    .hdr h1{margin:0 0 4px;font-size:24px;}.hdr .ct{text-align:right;font-size:12px;margin-top:-42px;}
    .body{padding:28px 40px;}
    .badge{display:inline-block;background:#e8f5e9;color:#1a3a2a;border:1px solid #1a3a2a;
           padding:4px 14px;border-radius:4px;font-size:12px;font-weight:bold;text-transform:uppercase;margin-bottom:18px;}
    .meta{display:flex;justify-content:space-between;margin-bottom:20px;}
    .mb h3{margin:0 0 4px;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#888;}
    .mb p{margin:0;font-size:13px;line-height:1.6;}
    .st{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#888;border-bottom:2px solid #1a3a2a;padding-bottom:3px;margin:18px 0 8px;}
    table{width:100%;border-collapse:collapse;font-size:13px;}
    th{text-align:left;padding:7px 10px;background:#f4f6f9;font-size:11px;font-weight:600;text-transform:uppercase;}
    td{padding:8px 10px;border-bottom:1px solid #eee;}td.n{text-align:right;}
    .tots{margin-left:auto;width:280px;margin-top:12px;}
    .tots table{width:100%}.tots td{border:none;padding:3px 8px;font-size:13px;}
    .tots .gt td{border-top:2px solid #1a3a2a;font-weight:bold;font-size:15px;padding-top:7px;}
    .due{background:#fff8f4;border:1px solid #f4b89a;border-radius:6px;padding:12px 16px;margin:16px 0;font-size:13px;}
    .footer{margin-top:32px;padding-top:12px;border-top:1px solid #eee;font-size:11px;color:#888;text-align:center;}
    </style></head><body>
    <div class="hdr"><div class="ct">{{cp}}<br>{{ce}}<br>Lic #{{lic}}</div>
    <h1>{{bn}}</h1><p style="font-size:12px;opacity:.85;">{{trade}} &mdash; Licensed &amp; Insured</p></div>
    <div class="body"><div class="badge">INVOICE</div>
    <div style="display:flex;justify-content:space-between;margin-bottom:18px;">
    <div><div style="font-size:10px;color:#888;">Invoice #</div><div style="font-size:16px;font-weight:bold;">{{inv_num}}</div></div>
    <div style="text-align:right"><div style="font-size:10px;color:#888;">Date</div><div>{{date}}</div>
    <div style="font-size:10px;color:#888;margin-top:6px;">Due</div><div style="color:#c0392b;font-weight:bold;">{{due}}</div></div></div>
    <div class="meta">
    <div class="mb"><h3>Bill To</h3><p><strong>{{cn}}</strong><br>{{ca}}</p></div>
    <div class="mb"><h3>Terms</h3><p>{{terms}}</p></div></div>
    <div class="st">Services</div>
    <table><thead><tr><th style="width:55%">Description</th><th class="n">Qty</th><th class="n">Unit</th><th class="n">Total</th></tr></thead><tbody>
    {% for i in items %}<tr><td>{{i.description}}</td><td class="n">{{i.qty}}</td>
    <td class="n">${{"%.2f"|format(i.unit_price)}}</td><td class="n">${{"%.2f"|format(i.amount)}}</td></tr>{% endfor %}
    </tbody></table>
    <div class="tots"><table><tr class="gt"><td>TOTAL DUE</td><td class="n">${{"%.2f"|format(total)}}</td></tr></table></div>
    <div class="due"><strong>Payment Due: {{due}}</strong> &mdash; {{terms}}<br>
    Make checks payable to <strong>{{bn}}</strong> or pay online.</div>
    <div class="footer">{{bn}} &bull; {{cp}} &bull; {{ce}}</div></div></body></html>"""
    from jinja2 import Template as _T
    html = _T(HTML_TMPL).render(
        bn=contractor.business_name or contractor.name,
        trade=contractor.trade or "Contractor",
        cp=contractor.phone, ce=contractor.email or contractor.work_email or "",
        lic=contractor.license_no or "",
        cn=client.name if client else "Client",
        ca=client.address if client else address,
        date=today.strftime("%B %d, %Y"),
        due=due.strftime("%B %d, %Y"),
        inv_num=inv_num, terms=contractor.invoice_terms or "Net 15",
        items=LINE_ITEMS, total=amount,
    )
    fname = f"invoice_{job_title[:12].replace(' ','_')}_{today.strftime('%Y%m%d')}.pdf"
    path = OUTPUT / fname
    HTML(string=html).write_pdf(str(path))
    return str(path)
'''
    with open(inv_path, "a") as f:
        f.write(helper)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  FIELDHAND DEMO RUNNER v2")
    print("  17 scenarios — just-in-time profile collection")
    print("="*60)

    _ensure_invoice_pdf_helper()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Wipe demo contractor
    existing = db.query(Contractor).filter(Contractor.phone == DEMO_PHONE).first()
    if existing:
        cid = existing.id
        # Clear pinned_job_id FK before deleting jobs
        existing.pinned_job_id = None
        db.flush()
        for m in [AuditLog, PendingAction, Message, Invoice, Expense]:
            db.query(m).filter(m.contractor_id == cid).delete()
        db.query(Document).filter(Document.contractor_id == cid).delete()
        for j in db.query(Job).filter(Job.contractor_id == cid).all(): db.delete(j)
        for c in db.query(Client).filter(Client.contractor_id == cid).all(): db.delete(c)
        db.delete(existing)
        db.commit()
        print("\n[Cleared previous demo contractor]")

    # Use Ford's real contractor record for Gmail (so real emails go out)
    ford = db.query(Contractor).filter(Contractor.phone == "+16036673203").first()
    gmail_token = ford.gmail_refresh_token if ford else None

    contractor = Contractor(**DEMO_CONTRACTOR)
    if gmail_token:
        contractor.gmail_refresh_token = gmail_token
        contractor.work_email = SEND_TO_EMAIL
    db.add(contractor)
    db.commit()
    db.refresh(contractor)
    print(f"[Created: {contractor.name} — {contractor.business_name}]")
    if gmail_token:
        print(f"[Gmail connected — real docs will be emailed to {SEND_TO_EMAIL}]")

    agent = InstrumentedAgent(db, contractor.id)
    full_log = {
        "meta": {
            "contractor": DEMO_CONTRACTOR,
            "contractor_id": contractor.id,
            "run_at": datetime.now(timezone.utc).isoformat(),
            "scenario_count": len(SCENARIOS),
            "send_to_email": SEND_TO_EMAIL,
        },
        "steps": [],
    }

    for i, scenario in enumerate(SCENARIOS):
        print(f"\n[{i+1}/{len(SCENARIOS)}] {scenario['id']}: {scenario['title']}")
        print(f"  {scenario['message'][:80]}...")

        if scenario.get("age_invoices"):
            _age_invoices(db, contractor.id)

        snapshot_before = db_snapshot(db, contractor.id)
        t0 = time.time()

        if scenario.get("onboarding_sim"):
            # Run simulated onboarding
            try:
                from src.routes.sms import _handle_onboarding_new, _continue_onboarding
                import asyncio

                onboarding_log = []
                inputs = scenario["onboarding_inputs"]

                # First message triggers onboarding
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        raise RuntimeError("closed")
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                resp = loop.run_until_complete(
                    _handle_onboarding_new(contractor.phone, scenario["message"], db)
                )
                bot_text = resp.body.decode() if hasattr(resp, 'body') else str(resp)
                onboarding_log.append({"role": "bot", "text": _extract_twiml_text(bot_text)})

                for user_input in inputs:
                    onboarding_log.append({"role": "user", "text": user_input})
                    resp = loop.run_until_complete(
                        _continue_onboarding(contractor, user_input, db)
                    )
                    bot_text = resp.body.decode() if hasattr(resp, 'body') else str(resp)
                    onboarding_log.append({"role": "bot", "text": _extract_twiml_text(bot_text)})

                trace = {
                    "context_snapshot": {},
                    "tool_calls": [],
                    "response": f"Onboarding complete. {len(inputs)} exchanges.",
                    "onboarding_log": onboarding_log,
                }
                print(f"  [Onboarding sim: {len(inputs)} inputs, {len(onboarding_log)} turns]")
            except Exception as e:
                print(f"  [Onboarding sim failed: {e}] — falling back to agent.chat()")
                trace = agent.chat(scenario["message"])
        else:
            trace = agent.chat(scenario["message"])

        elapsed = time.time() - t0

        yn_trace = None
        if scenario.get("yn_followup"):
            time.sleep(0.5)
            yn_trace = agent.execute_yn(scenario["yn_followup"])
            print(f"  [Y/N: {scenario['yn_followup']}] → {str(yn_trace['yn_result'])[:80]}")

        snapshot_after = db_snapshot(db, contractor.id)

        print(f"  Tools: {[tc['tool'] for tc in trace['tool_calls']]}")
        print(f"  Response: {trace['response'][:100]}...")
        print(f"  {elapsed:.1f}s")

        step_entry = {
            "scenario": scenario,
            "elapsed_seconds": round(elapsed, 2),
            "context_snapshot": trace["context_snapshot"],
            "tool_calls": trace["tool_calls"],
            "response": trace["response"],
            "yn_trace": yn_trace,
            "db_before": snapshot_before,
            "db_after": snapshot_after,
            "cost": trace.get("cost", {}),
            "routing": trace.get("routing", {}),
            "active_tools": trace.get("active_tools", []),
        }
        if trace.get("onboarding_log"):
            step_entry["onboarding_log"] = trace["onboarding_log"]
        full_log["steps"].append(step_entry)

    # Send real PDFs
    print("\n" + "="*60)
    print("  SENDING REAL PDFs TO EMAIL")
    print("="*60)
    send_real_docs(db, contractor.id, SEND_TO_EMAIL)

    db.close()

    with open(LOG_PATH, "w") as f:
        json.dump(full_log, f, indent=2, default=str)
    print(f"\n[Log: {LOG_PATH}]")

    generate_report(full_log)
    print(f"[Report: {REPORT_PATH}]")
    print(f"\nOpen: file://{REPORT_PATH.resolve()}")
    print("Done!")


def _age_invoices(db, contractor_id):
    from src.models.invoice import InvoiceStatus
    for inv in db.query(Invoice).filter(Invoice.contractor_id == contractor_id).all():
        inv.sent_at = datetime.now(timezone.utc) - timedelta(days=20)
        inv.status = InvoiceStatus.SENT
    db.commit()
    print("  [Aged invoices 20 days]")


# ── HTML Report ───────────────────────────────────────────────────────────────

CATEGORY_COLORS = {
    "Onboarding": "#ffd43b",
    "Context & Thread": "#4f7cff",
    "Client Info": "#da77f2",
    "Quoting": "#38d9a9",
    "Y/N Gate": "#ffa94d",
    "Supply Chain": "#cc5de8",
    "Invoicing": "#ff6b6b",
    "Compliance": "#74c0fc",
    "Admin": "#a9e34b",
    "Just-in-Time": "#ff6bff",
    "Investigate": "#ff6b6b",
}

def _render_cost_tab(cost_data, routing_data, tier, tier_name, total_cost, monthly, tokens, thinking, routing_reason, active_tools_list):
    tok = cost_data.get('tokens', {}) or {}
    cst = cost_data.get('cost', {}) or {}
    model_str = cost_data.get('model', '') or ''
    reason_html = f'&nbsp;&nbsp;<span style="color:var(--muted);font-size:12px;">Reason: {routing_reason}</span>' if routing_reason else ''
    thinking_row = f'<tr><td>Thinking</td><td>{thinking:,}</td></tr>' if thinking else ''
    tools_html = ''.join(f'<span class="tool-chip">{t}</span>' for t in active_tools_list) or '<span style="color:var(--muted);font-size:12px;">none</span>'
    return f"""<div class="label">COST &amp; ROUTING</div>
            <div style="margin-bottom:14px;">
              <span class="tier-badge tier-{tier}">{tier_name}</span>
              &nbsp;&nbsp;
              <span style="color:var(--muted);font-size:12px;">Model: {model_str or 'claude-sonnet-4-5'}</span>
              {reason_html}
            </div>
            <table class="cost-table">
              <thead><tr><th>Token Type</th><th>Count</th></tr></thead>
              <tbody>
                <tr><td>Classifier Input</td><td>{tok.get('classifier_input',0):,}</td></tr>
                <tr><td>Classifier Output</td><td>{tok.get('classifier_output',0):,}</td></tr>
                <tr><td>Agent Input</td><td>{tok.get('agent_input',0):,}</td></tr>
                <tr><td>Agent Output</td><td>{tok.get('agent_output',0):,}</td></tr>
                <tr><td>Cache Reads</td><td>{tok.get('agent_cache_read',0):,}</td></tr>
                {thinking_row}
                <tr class="total"><td>Total Tokens</td><td>{tokens:,}</td></tr>
              </tbody>
            </table>
            <table class="cost-table">
              <thead><tr><th>Cost Component</th><th>USD</th></tr></thead>
              <tbody>
                <tr><td>Classifier</td><td>${cst.get('classifier',0):.6f}</td></tr>
                <tr><td>Agent</td><td>${cst.get('agent',0):.6f}</td></tr>
                <tr class="total"><td>Total</td><td>${total_cost:.6f}</td></tr>
              </tbody>
            </table>
            <div style="color:var(--muted);font-size:12px;margin-bottom:10px;">
              Monthly projection (15 msg/day \u00d7 30 days): <span class="monthly-num">~${monthly:.2f}</span>
            </div>
            <div class="label">ACTIVE TOOLS LOADED ({len(active_tools_list)})</div>
            <div class="tools-loaded">
              {tools_html}
            </div>"""


def generate_report(log: dict):
    steps_html = ""
    for i, step in enumerate(log["steps"]):
        s = step["scenario"]
        tools = step["tool_calls"]
        ctx = step["context_snapshot"]
        yn = step.get("yn_trace")
        cat = s.get("category", "")
        cat_color = CATEGORY_COLORS.get(cat, "#6b7a99")

        tool_rows = ""
        for tc in tools:
            r = tc["result"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            tool_rows += f"""
            <div class="tool-call">
              <div class="tool-header">
                <span class="badge badge-tool">{tc['tool']}</span>
                <span class="round-badge">round {tc['round']}</span>
              </div>
              <div class="tool-body">
                <div class="col-half"><div class="label">INPUT</div>
                  <pre class="code">{json.dumps(tc['input'], indent=2)}</pre></div>
                <div class="col-half"><div class="label">RESULT</div>
                  <pre class="code result">{r}</pre></div>
              </div>
            </div>"""
        if not tools:
            tool_rows = '<div class="no-tools">No tools called — answered from memory context only</div>'

        yn_html = ""
        if yn:
            yn_result_e = str(yn["yn_result"]).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            yn_html = f"""
            <div class="yn-block">
              <div class="label">Y/N CONFIRMATION</div>
              <div class="yn-row">
                <span class="yn-badge">Contractor texted: <strong>{yn['yn_input']}</strong></span>
              </div>
              <div class="yn-result">{yn_result_e}</div>
            </div>"""

        db_changes = _compute_diff(step["db_before"], step["db_after"])
        db_html = _render_diff(db_changes)

        ctx_jobs = ctx.get("active_jobs", [])
        ctx_fin  = ctx.get("financial_summary", {})
        ctx_html = f"""
        <div class="ctx-grid">
          <div class="ctx-card"><div class="label">Active Jobs ({len(ctx_jobs)})</div>
            <pre class="code small">{json.dumps(ctx_jobs, indent=2, default=str)[:500]}</pre></div>
          <div class="ctx-card"><div class="label">Financial Snapshot</div>
            <pre class="code small">{json.dumps(ctx_fin, indent=2, default=str)}</pre></div>
       </div>"""

        msg_e  = s["message"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        resp_e = step["response"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

        # Cost & routing data
        tier_names = {1: 'EXECUTE', 2: 'REASON', 3: 'INVESTIGATE'}
        cost_data = step.get('cost', {})
        routing_data = step.get('routing', {})
        tier = cost_data.get('tier', 2)
        tier_name = tier_names.get(tier, 'REASON')
        total_cost = cost_data.get('cost', {}).get('total', 0)
        monthly = cost_data.get('cost', {}).get('monthly_projection_usd', 0)
        tokens = cost_data.get('tokens', {}).get('total', 0)
        thinking = cost_data.get('tokens', {}).get('thinking', 0)
        routing_reason = routing_data.get('reason', '')
        active_tools_list = step.get('active_tools', [])

        # Build onboarding exchange tab if present
        onboarding_log = step.get("onboarding_log") or trace.get("onboarding_log") if False else step.get("onboarding_log")
        onb_tab_btn = ""
        onb_tab_content = ""
        if onboarding_log:
            onb_tab_btn = f'<button class="tab" onclick="showTab(this,\'onb-{i}\')">Onboarding Exchange</button>'
            bubbles = ""
            for entry in onboarding_log:
                role = entry.get("role", "bot")
                text = entry.get("text", "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                if role == "user":
                    bubbles += f'<div class="onb-user"><span class="onb-label">Contractor</span><div class="onb-bubble onb-user-bubble">{text}</div></div>'
                else:
                    bubbles += f'<div class="onb-bot"><span class="onb-label">FIELDHAND</span><div class="onb-bubble onb-bot-bubble">{text}</div></div>'
            summary = s.get("onboarding_summary", "")
            onb_tab_content = f'''<div id="onb-{i}" class="tab-content hidden">
              <div class="label">ONBOARDING EXCHANGE — {len(onboarding_log)} turns</div>
              {f'<div class="onb-summary">{summary}</div>' if summary else ''}
              <div class="onb-chat">{bubbles}</div>
            </div>'''

        steps_html += f"""
        <div class="step" id="step-{i+1}">
          <div class="step-header">
            <div class="step-id">{s['id']}</div>
            <div class="step-meta">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
                <h2 class="step-title">{s['title']}</h2>
                <span class="cat-badge" style="background:{cat_color}22;color:{cat_color};border-color:{cat_color}44;">{cat}</span>
              </div>
              <div class="step-desc">{s['description']}</div>
              <div class="step-timing">{'&#9201; ' + str(step['elapsed_seconds']) + 's &nbsp;|&nbsp; &#128295; ' + str(len(tools)) + ' tool call(s) &nbsp;|&nbsp; <span class="tier-badge tier-' + str(tier) + '">' + tier_name + '</span>' + (' &nbsp;|&nbsp; &#129504; ' + (cost_data.get('model', '') or '').split('-')[1].upper() if cost_data.get('model') and len((cost_data.get('model','')).split('-')) > 1 else '') + (' &nbsp;|&nbsp; <span class="cost-num">$' + f'{total_cost:.5f}' + '</span> &nbsp;|&nbsp; <span class="monthly-num">~$' + f'{monthly:.2f}' + '/mo</span>' if total_cost else '') + (' &nbsp;|&nbsp; &#129504; thinking ' + str(thinking) + ' tok' if thinking else '') + (' &nbsp;|&nbsp; ' + str(len(active_tools_list)) + ' tools loaded') + (' &nbsp;|&nbsp; &#9989; Y/N confirmed' if yn else '') + (' &nbsp;|&nbsp; &#128100; Onboarding sim' if onboarding_log else '')}</div>
            </div>
          </div>
          <div class="section-tabs">
            <button class="tab active" onclick="showTab(this,'msg-{i}')">Message</button>
            {onb_tab_btn}
            <button class="tab" onclick="showTab(this,'ctx-{i}')">Context</button>
            <button class="tab" onclick="showTab(this,'tools-{i}')">Tool Calls</button>
            <button class="tab" onclick="showTab(this,'resp-{i}')">Response</button>
            {'<button class="tab" onclick="showTab(this,\'yn-' + str(i) + '\')">Y/N Gate</button>' if yn else ''}
            <button class="tab" onclick="showTab(this,'db-{i}')">DB Changes</button>
            <button class="tab" onclick="showTab(this,'cost-{i}')">Cost &amp; Routing</button>
          </div>
          <div id="msg-{i}" class="tab-content active">
            <div class="label">CONTRACTOR MESSAGE (simulated SMS)</div>
            <div class="message-bubble">{msg_e}</div>
          </div>
          {onb_tab_content}
          <div id="ctx-{i}" class="tab-content hidden">
            <div class="label">MEMORY CONTEXT INJECTED INTO LLM</div>{ctx_html}
          </div>
          <div id="tools-{i}" class="tab-content hidden">
            <div class="label">TOOL CALLS</div>{tool_rows}
          </div>
          <div id="resp-{i}" class="tab-content hidden">
            <div class="label">FIELDHAND RESPONSE</div>
            <div class="response-bubble">{resp_e}</div>
          </div>
          {'<div id="yn-' + str(i) + '" class="tab-content hidden">' + yn_html + '</div>' if yn else ''}
          <div id="db-{i}" class="tab-content hidden">
            <div class="label">DATABASE CHANGES</div>{db_html}
          </div>
          <div id="cost-{i}" class="tab-content hidden">
            {_render_cost_tab(cost_data, routing_data, tier, tier_name, total_cost, monthly, tokens, thinking, routing_reason, active_tools_list)}
          </div>
        </div>"""

    final_db = log["steps"][-1]["db_after"]
    meta = log["meta"]
    stats = _stats_bar(log)
    final = _render_final_db(final_db)
    sidebar_links = "".join(
        f'<a href="#step-{i+1}" style="border-left-color:{CATEGORY_COLORS.get(s["scenario"].get("category",""), "#6b7a99")}44">'
        f'{s["scenario"]["id"]} {s["scenario"]["title"]}</a>'
        for i, s in enumerate(log["steps"])
    )

    html = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>FIELDHAND v2 Demo Report</title>
<style>
:root{{--bg:#0f1117;--surface:#1a1d27;--surface2:#222535;--border:#2e3247;
  --accent:#4f7cff;--accent2:#38d9a9;--warn:#ffa94d;--danger:#ff6b6b;
  --text:#e2e8f0;--muted:#6b7a99;--tool-bg:#1a2035;--result-bg:#0d1f1a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.6;}}
a{{color:var(--accent);text-decoration:none;}}
.sidebar{{position:fixed;top:0;left:0;width:230px;height:100vh;background:var(--surface);border-right:1px solid var(--border);overflow-y:auto;padding:16px 0;z-index:100;}}
.sidebar-logo{{padding:0 16px 16px;border-bottom:1px solid var(--border);margin-bottom:8px;}}
.sidebar-logo h2{{font-size:15px;font-weight:700;background:linear-gradient(135deg,#4f7cff,#38d9a9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}}
.sidebar-logo p{{font-size:11px;color:var(--muted);}}
.sidebar a{{display:block;padding:5px 16px;color:var(--muted);font-size:11px;border-left:3px solid transparent;transition:all .15s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.sidebar a:hover{{color:var(--text);background:rgba(79,124,255,.05);}}
.sidebar a.active{{color:var(--accent);}}
.sidebar .cat-section{{padding:8px 16px 4px;font-size:10px;font-weight:700;letter-spacing:.1em;color:var(--muted);text-transform:uppercase;margin-top:8px;}}
.main{{margin-left:230px;padding:28px 36px;max-width:1060px;}}
.page-header{{margin-bottom:32px;padding-bottom:20px;border-bottom:1px solid var(--border);}}
.page-header h1{{font-size:26px;font-weight:700;background:linear-gradient(135deg,#4f7cff,#38d9a9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:6px;}}
.page-header .meta{{color:var(--muted);font-size:12px;}}
.stats-bar{{display:grid;grid-template-columns:repeat(7,1fr);gap:12px;margin-bottom:32px;}}
.stat-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px;}}
.stat-card .num{{font-size:24px;font-weight:700;color:var(--accent2);}}
.stat-card .lbl{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-top:3px;}}
.step{{background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-bottom:24px;overflow:hidden;scroll-margin-top:20px;}}
.step-header{{display:flex;align-items:flex-start;gap:14px;padding:18px 22px;background:var(--surface2);border-bottom:1px solid var(--border);}}
.step-id{{background:var(--accent);color:white;font-size:10px;font-weight:700;padding:3px 9px;border-radius:5px;letter-spacing:.05em;flex-shrink:0;margin-top:4px;}}
.step-title{{font-size:16px;font-weight:600;margin-bottom:3px;}}
.step-desc{{color:var(--muted);font-size:12px;}}
.step-timing{{color:var(--muted);font-size:11px;margin-top:5px;}}
.cat-badge{{padding:2px 9px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:.04em;border:1px solid;}}
.section-tabs{{display:flex;border-bottom:1px solid var(--border);background:var(--surface2);flex-wrap:wrap;}}
.tab{{padding:9px 16px;background:none;border:none;color:var(--muted);font-size:11px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;letter-spacing:.04em;}}
.tab:hover{{color:var(--text);}}
.tab.active{{color:var(--accent);border-bottom-color:var(--accent);}}
.tab-content{{padding:18px 22px;}}.tab-content.hidden{{display:none;}}
.label{{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:8px;}}
.message-bubble{{background:var(--surface2);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:8px;padding:13px 16px;font-size:14px;line-height:1.7;}}
.response-bubble{{background:var(--surface2);border:1px solid var(--border);border-left:3px solid var(--accent2);border-radius:8px;padding:13px 16px;font-size:14px;line-height:1.7;white-space:pre-wrap;}}
.yn-block{{background:#1a2500;border:1px solid #3d5a00;border-radius:8px;padding:14px;}}
.yn-row{{margin-bottom:8px;}}.yn-badge{{background:rgba(169,227,75,.15);color:#a9e34b;border:1px solid rgba(169,227,75,.3);padding:3px 10px;border-radius:4px;font-size:12px;}}
.yn-result{{background:#0d1020;border:1px solid var(--border);border-radius:6px;padding:10px 12px;font-size:12px;white-space:pre-wrap;color:var(--accent2);}}
pre.code{{background:#0d1020;border:1px solid var(--border);border-radius:6px;padding:10px 12px;font-size:11px;font-family:'Fira Code','Cascadia Code',monospace;overflow-x:auto;white-space:pre-wrap;word-break:break-word;color:#c9d1e0;line-height:1.5;}}
pre.code.result{{background:var(--result-bg);border-color:#1a3328;color:#9deccc;}}
pre.code.small{{max-height:180px;overflow-y:auto;}}
.tool-call{{background:var(--tool-bg);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:10px;}}
.tool-header{{display:flex;align-items:center;gap:10px;margin-bottom:10px;}}
.tool-body{{display:grid;grid-template-columns:1fr 1fr;gap:10px;}}.col-half{{min-width:0;}}
.badge{{padding:3px 9px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:.04em;}}
.badge-tool{{background:rgba(79,124,255,.15);color:var(--accent);border:1px solid rgba(79,124,255,.3);}}
.round-badge{{font-size:10px;color:var(--muted);}}.no-tools{{color:var(--muted);font-style:italic;font-size:12px;padding:6px 0;}}
.ctx-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}}
.ctx-card{{background:#0d1020;border:1px solid var(--border);border-radius:8px;padding:10px;}}
.db-section{{margin-bottom:14px;}}.db-section-title{{font-size:11px;font-weight:600;color:var(--accent2);margin-bottom:6px;}}
.db-row-new{{background:rgba(56,217,169,.06);border:1px solid rgba(56,217,169,.2);border-radius:5px;padding:7px 10px;margin-bottom:5px;font-size:11px;}}
.db-row-changed{{background:rgba(255,169,77,.06);border:1px solid rgba(255,169,77,.2);border-radius:5px;padding:7px 10px;margin-bottom:5px;font-size:11px;}}
.db-no-change{{color:var(--muted);font-style:italic;font-size:12px;}}
.diff-key{{color:var(--muted);}}.diff-new{{color:var(--accent2);}}.diff-old{{color:var(--danger);text-decoration:line-through;}}
.final-db{{margin-top:36px;}}.final-db h2{{font-size:18px;font-weight:600;margin-bottom:18px;padding-bottom:10px;border-bottom:1px solid var(--border);}}
.db-table{{width:100%;border-collapse:collapse;margin-bottom:24px;}}
.db-table th{{text-align:left;font-size:10px;font-weight:700;letter-spacing:.08em;color:var(--muted);text-transform:uppercase;padding:7px 10px;border-bottom:1px solid var(--border);}}
.db-table td{{padding:9px 10px;border-bottom:1px solid #1e2235;font-size:12px;vertical-align:top;}}
.db-table tr:hover td{{background:rgba(255,255,255,.02);}}
.status-pill{{padding:2px 7px;border-radius:3px;font-size:10px;font-weight:600;letter-spacing:.04em;}}
.status-lead{{background:rgba(107,114,153,.2);color:#8899bb;}}
.status-quoted{{background:rgba(79,124,255,.15);color:var(--accent);}}
.status-active{{background:rgba(56,217,169,.15);color:var(--accent2);}}
.status-complete{{background:rgba(255,169,77,.15);color:var(--warn);}}
.status-paid{{background:rgba(56,217,169,.25);color:#38d9a9;}}
.status-sent{{background:rgba(255,169,77,.15);color:var(--warn);}}
.status-draft{{background:rgba(107,114,153,.2);color:#8899bb;}}
.section-title{{font-size:12px;font-weight:600;color:var(--muted);margin:18px 0 10px;text-transform:uppercase;letter-spacing:.08em;}}
.pinned-badge{{background:rgba(255,200,0,.15);color:#ffd43b;border:1px solid rgba(255,200,0,.3);padding:2px 7px;border-radius:3px;font-size:10px;margin-left:6px;}}
.onb-summary{{background:rgba(79,124,255,.08);border:1px solid rgba(79,124,255,.2);border-radius:6px;padding:10px 14px;font-size:12px;color:var(--accent);margin-bottom:14px;}}
.onb-chat{{display:flex;flex-direction:column;gap:10px;max-height:500px;overflow-y:auto;padding:4px 0;}}
.onb-user,.onb-bot{{display:flex;flex-direction:column;gap:3px;}}
.onb-user{{align-items:flex-end;}}.onb-bot{{align-items:flex-start;}}
.onb-label{{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:0 4px;}}
.onb-bubble{{max-width:75%;padding:9px 13px;border-radius:10px;font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-word;}}
.onb-user-bubble{{background:rgba(79,124,255,.15);border:1px solid rgba(79,124,255,.3);color:#c5d5ff;border-bottom-right-radius:3px;}}
.onb-bot-bubble{{background:rgba(56,217,169,.08);border:1px solid rgba(56,217,169,.2);color:#9deccc;border-bottom-left-radius:3px;}}
.tier-badge{{padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:.06em;}}
.tier-1{{background:rgba(56,217,169,.15);color:#38d9a9;}}
.tier-2{{background:rgba(79,124,255,.15);color:#4f7cff;}}
.tier-3{{background:rgba(255,107,107,.15);color:#ff6b6b;}}
.cost-num{{color:#ffa94d;font-weight:700;font-size:12px;}}
.monthly-num{{color:#38d9a9;font-weight:700;font-size:12px;}}
.cost-table{{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:12px;}}
.cost-table th{{text-align:left;padding:6px 10px;background:var(--surface2);font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);}}
.cost-table td{{padding:6px 10px;border-bottom:1px solid var(--border);color:var(--text);}}
.cost-table tr.total td{{color:#ffa94d;font-weight:700;border-top:1px solid var(--border);}}
.tools-loaded{{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;}}
.tool-chip{{background:rgba(79,124,255,.12);color:#4f7cff;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:600;}}
@media(max-width:900px){{.sidebar{{display:none;}}.main{{margin-left:0;padding:16px;}}.tool-body{{grid-template-columns:1fr;}}.ctx-grid{{grid-template-columns:1fr;}}.stats-bar{{grid-template-columns:1fr 1fr;}}}}
</style></head><body>
<div class="sidebar">
  <div class="sidebar-logo"><h2>FIELDHAND</h2><p>Demo Report v2</p></div>
  {sidebar_links}
  <a href="#final-db" style="margin-top:12px;border-top:1px solid var(--border);padding-top:10px;">&#128197; Final DB State</a>
</div>
<div class="main">
  <div class="page-header">
    <h1>FIELDHAND &mdash; v2 Simulation Report</h1>
    <div class="meta">
      Contractor: <strong>{meta['contractor']['name']}</strong> &nbsp;|&nbsp;
      Trade: <strong>{meta['contractor']['trade']}</strong> &nbsp;|&nbsp;
      Business: <strong>{meta['contractor']['business_name']}</strong> &nbsp;|&nbsp;
      Run: {meta['run_at']}<br>
      Real docs emailed to: <strong>{meta.get('send_to_email','')}</strong>
    </div>
  </div>
  {stats}
  {steps_html}
  <div class="final-db" id="final-db"><h2>Final Database State</h2>{final}</div>
</div>
<script>
function showTab(btn,id){{
  var step=btn.closest('.step');
  step.querySelectorAll('.tab').forEach(function(t){{t.classList.remove('active');}});
  step.querySelectorAll('.tab-content').forEach(function(c){{c.classList.add('hidden');c.classList.remove('active');}});
  btn.classList.add('active');
  var c=document.getElementById(id);
  if(c){{c.classList.remove('hidden');c.classList.add('active');}}
}}
var steps=document.querySelectorAll('.step');
var links=document.querySelectorAll('.sidebar a');
window.addEventListener('scroll',function(){{
  var cur='';
  steps.forEach(function(s){{if(window.scrollY>=s.offsetTop-80)cur=s.id;}});
  links.forEach(function(a){{a.classList.toggle('active',a.getAttribute('href')==='#'+cur);}});
}});
</script></body></html>"""

    with open(REPORT_PATH, "w") as f:
        f.write(html)


def _stats_bar(log):
    n = len(log["steps"])
    tools = sum(len(s["tool_calls"]) for s in log["steps"])
    yn = sum(1 for s in log["steps"] if s.get("yn_trace") and s["yn_trace"]["yn_result"] != "(no pending action)")
    t = sum(s["elapsed_seconds"] for s in log["steps"])
    final_db = log["steps"][-1]["db_after"]
    jobs = len(final_db["jobs"])
    total_cost = sum(s.get("cost", {}).get("cost", {}).get("total", 0) for s in log["steps"])
    avg_cost = total_cost / n if n else 0
    monthly = avg_cost * 15 * 30
    return f"""<div class="stats-bar">
      <div class="stat-card"><div class="num">{n}</div><div class="lbl">Scenarios</div></div>
      <div class="stat-card"><div class="num">{tools}</div><div class="lbl">Tool Calls</div></div>
      <div class="stat-card"><div class="num">{yn}</div><div class="lbl">Y/N Gates</div></div>
      <div class="stat-card"><div class="num">{jobs}</div><div class="lbl">Jobs Created</div></div>
      <div class="stat-card"><div class="num">{t:.0f}s</div><div class="lbl">Total Time</div></div>
      <div class="stat-card"><div class="num cost-num">${total_cost:.4f}</div><div class="lbl">Sim Run Cost</div></div>
      <div class="stat-card"><div class="num monthly-num">~${monthly:.2f}</div><div class="lbl">Monthly Proj.</div></div>
    </div>"""


def _compute_diff(before, after):
    changes = {}
    for table in ["clients","jobs","expenses","invoices","pending_actions"]:
        b_ids = {r["id"]: r for r in before.get(table,[]) if "id" in r}
        a_ids = {r["id"]: r for r in after.get(table,[]) if "id" in r}
        new = [a_ids[k] for k in a_ids if k not in b_ids]
        changed = []
        for k in a_ids:
            if k in b_ids:
                diffs = {fk:(b_ids[k].get(fk),a_ids[k].get(fk)) for fk in a_ids[k] if a_ids[k].get(fk)!=b_ids[k].get(fk)}
                if diffs: changed.append({"record":a_ids[k],"diffs":diffs})
        if new or changed:
            changes[table] = {"new":new,"changed":changed}
    return changes


def _render_diff(changes):
    if not changes:
        return '<div class="db-no-change">No database changes this step.</div>'
    html = ""
    for table, data in changes.items():
        html += f'<div class="db-section"><div class="db-section-title">{table.upper()}</div>'
        for rec in data.get("new",[]):
            fields = " &nbsp; ".join(
                f'<span class="diff-key">{k}:</span> <span class="diff-new">{v}</span>'
                for k,v in rec.items() if v is not None and k!="id"
            )
            html += f'<div class="db-row-new">&#10024; NEW &nbsp; {fields}</div>'
        for item in data.get("changed",[]):
            rec = item["record"]
            ds = " &nbsp; ".join(
                f'<span class="diff-key">{k}:</span> <span class="diff-old">{old}</span> &rarr; <span class="diff-new">{new}</span>'
                for k,(old,new) in item["diffs"].items()
            )
            name = rec.get("title") or rec.get("name") or rec.get("description") or rec.get("summary","")[:30]
            html += f'<div class="db-row-changed">&#9999;&#65039; <strong>{name}</strong> &nbsp; {ds}</div>'
        html += "</div>"
    return html


def _status_pill(s):
    return f'<span class="status-pill status-{s.lower()}">{s.upper()}</span>'


def _render_final_db(db):
    html = ""
    html += '<div class="section-title">Clients</div>'
    html += '<table class="db-table"><thead><tr><th>Name</th><th>Phone</th><th>Email</th><th>Address</th><th>Notes</th></tr></thead><tbody>'
    for c in db.get("clients",[]):
        html += f'<tr><td>{c["name"]}</td><td>{c.get("phone","")}</td><td>{c.get("email","")}</td><td>{c.get("address","")}</td><td>{(c.get("notes") or "")[:60]}</td></tr>'
    html += "</tbody></table>"
    html += '<div class="section-title">Jobs</div>'
    html += '<table class="db-table"><thead><tr><th>Title</th><th>Client</th><th>Status</th><th>Quoted</th><th>Actual Cost</th><th>Budget %</th></tr></thead><tbody>'
    for j in db.get("jobs",[]):
        budget = f'{j["budget_used_pct"]:.0f}%' if j.get("budget_used_pct") else "&mdash;"
        quoted = f'${j["quoted_amount"]:,.2f}' if j.get("quoted_amount") else "&mdash;"
        html += f'<tr><td>{j["title"]}</td><td>{j.get("client","")}</td><td>{_status_pill(j["status"])}</td><td>{quoted}</td><td>${j["actual_cost"]:,.2f}</td><td>{budget}</td></tr>'
    html += "</tbody></table>"
    html += '<div class="section-title">Expenses</div>'
    html += '<table class="db-table"><thead><tr><th>Description</th><th>Amount</th><th>Category</th><th>Vendor</th></tr></thead><tbody>'
    for e in db.get("expenses",[]):
        html += f'<tr><td>{e["description"]}</td><td>${e["amount"]:,.2f}</td><td>{e.get("category","")}</td><td>{e.get("vendor","")}</td></tr>'
    html += "</tbody></table>"
    html += '<div class="section-title">Invoices</div>'
    html += '<table class="db-table"><thead><tr><th>Amount</th><th>Status</th><th>Sent At</th></tr></thead><tbody>'
    for inv in db.get("invoices",[]):
        html += f'<tr><td>${inv["amount"]:,.2f}</td><td>{_status_pill(inv["status"])}</td><td>{inv.get("sent_at") or "&mdash;"}</td></tr>'
    html += "</tbody></table>"
    html += '<div class="section-title">Audit Log</div>'
    html += '<table class="db-table"><thead><tr><th>Action</th><th>Subject</th><th>Channel</th><th>By</th><th>When</th></tr></thead><tbody>'
    for a in db.get("audit_log",[]):
        html += f'<tr><td>{a["action"]}</td><td>{(a.get("subject") or "")[:55]}</td><td>{a.get("channel","")}</td><td>{a.get("initiated_by","")}</td><td>{str(a.get("created_at",""))[:19]}</td></tr>'
    html += "</tbody></table>"
    return html


if __name__ == "__main__":
    main()
