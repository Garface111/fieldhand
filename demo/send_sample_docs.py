"""
Generate a sample quote PDF and a sample invoice PDF,
then email both to ford.genereaux@dysonswarmtechnologies.com
via the connected Gmail account.
"""
import sys
import os
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
OUTPUT_DIR = Path(__file__).parent.parent / "generated_docs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Sample data ──────────────────────────────────────────────────────

CONTRACTOR = {
    "name": "Jake Morales",
    "business_name": "Morales Plumbing LLC",
    "trade": "Plumber",
    "phone": "(603) 555-0199",
    "email": "jake@moralesplumbing.com",
    "license_no": "PL-2024-00847",
    "invoice_terms": "Net 15",
}

CLIENT = {
    "name": "Sarah Chen",
    "address": "412 Oak St, Manchester, NH 03101",
    "phone": "(617) 555-0182",
    "email": "sarah.chen@email.com",
}

JOB = {
    "id": "demo0001",
    "title": "Water Heater Replacement",
    "description": "Remove and replace existing 40-gal water heater with new 50-gal Bradford White gas unit. Includes disposal of old unit, new flex connectors, expansion tank, and code-required earthquake straps.",
    "address": CLIENT["address"],
}

QUOTE_LINE_ITEMS = [
    {"description": "50-gal Bradford White ProLine Gas Water Heater (BW-50T6FSN)", "qty": 1, "unit_price": 680.00, "amount": 680.00},
    {"description": "Expansion tank (Amtrol ST-5)", "qty": 1, "unit_price": 89.00, "amount": 89.00},
    {"description": "Flex connectors, fittings, solder, misc materials", "qty": 1, "unit_price": 47.00, "amount": 47.00},
    {"description": "Permit (City of Manchester)", "qty": 1, "unit_price": 95.00, "amount": 95.00},
    {"description": "Labor — removal, installation, inspection (4 hrs @ $110/hr)", "qty": 4, "unit_price": 110.00, "amount": 440.00},
    {"description": "Old unit disposal & haul-away", "qty": 1, "unit_price": 49.00, "amount": 49.00},
]

INVOICE_LINE_ITEMS = QUOTE_LINE_ITEMS  # same items for this demo


# ── Quote PDF ────────────────────────────────────────────────────────

def generate_quote_pdf() -> str:
    today = datetime.now()
    valid_until = today + timedelta(days=30)
    subtotal = sum(i["amount"] for i in QUOTE_LINE_ITEMS)
    estimate_number = f"EST-{today.strftime('%Y%m%d')}-DEMO01"

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("quote.html")
    html = template.render(
        business_name=CONTRACTOR["business_name"],
        trade=CONTRACTOR["trade"],
        contractor_phone=CONTRACTOR["phone"],
        contractor_email=CONTRACTOR["email"],
        license_no=CONTRACTOR["license_no"],
        client_name=CLIENT["name"],
        client_address=CLIENT["address"],
        client_phone=CLIENT["phone"],
        client_email=CLIENT["email"],
        date=today.strftime("%B %d, %Y"),
        valid_until=valid_until.strftime("%B %d, %Y"),
        estimate_number=estimate_number,
        description=JOB["description"],
        line_items=QUOTE_LINE_ITEMS,
        subtotal=subtotal,
        tax_rate=None,
        tax_amount=0,
        total=subtotal,
        payment_terms=CONTRACTOR["invoice_terms"],
        deposit_required=False,
        deposit_pct=0,
        deposit_amount=0,
        notes="All work performed to NH plumbing code. Warranty: 1 year labor, manufacturer warranty on parts.",
    )
    path = OUTPUT_DIR / "sample_quote_DEMO01.pdf"
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(path))
    print(f"  Quote PDF: {path}")
    return str(path)


# ── Invoice PDF ──────────────────────────────────────────────────────

INVOICE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body { font-family: Arial, sans-serif; margin: 0; padding: 0; color: #1a1a1a; }
  .header { background: #1a3a2a; color: white; padding: 32px 40px; }
  .header h1 { margin: 0 0 4px 0; font-size: 26px; letter-spacing: 1px; }
  .header .tagline { font-size: 13px; opacity: 0.8; margin: 0; }
  .header .contact { text-align: right; font-size: 13px; margin-top: -48px; }
  .body { padding: 32px 40px; }
  .meta-row { display: flex; justify-content: space-between; margin-bottom: 28px; }
  .meta-block h3 { margin: 0 0 6px 0; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #888; }
  .meta-block p { margin: 0; font-size: 14px; line-height: 1.6; }
  .section-title { font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
                   color: #888; border-bottom: 2px solid #1a3a2a; padding-bottom: 4px; margin: 24px 0 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; padding: 8px 12px; background: #f4f6f9; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  td { padding: 10px 12px; border-bottom: 1px solid #eee; vertical-align: top; }
  td.num { text-align: right; }
  .totals { margin-left: auto; width: 280px; margin-top: 16px; }
  .totals table { width: 100%; }
  .totals td { border: none; padding: 4px 8px; font-size: 14px; }
  .totals .grand-total td { border-top: 2px solid #1a3a2a; font-weight: bold; font-size: 16px; padding-top: 8px; }
  .invoice-badge { display: inline-block; background: #e8f5e9; color: #1a3a2a; border: 1px solid #1a3a2a;
                   padding: 4px 14px; border-radius: 4px; font-size: 12px; font-weight: bold;
                   letter-spacing: 1px; text-transform: uppercase; }
  .due-box { background: #fff8e1; border: 1px solid #f9a825; border-radius: 6px;
             padding: 12px 16px; margin-top: 24px; font-size: 13px; }
  .due-box strong { color: #e65100; }
  .footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid #eee;
            font-size: 12px; color: #888; text-align: center; }
</style>
</head>
<body>
<div class="header">
  <div class="contact">
    {{ contractor_phone }}<br>
    {{ contractor_email }}<br>
    {% if license_no %}License #{{ license_no }}{% endif %}
  </div>
  <h1>{{ business_name }}</h1>
  <p class="tagline">{{ trade }} &mdash; Licensed &amp; Insured</p>
</div>

<div class="body">
  <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:24px;">
    <div>
      <h2 style="margin:0 0 6px; font-size:22px;">INVOICE</h2>
      <span class="invoice-badge">{{ status }}</span>
    </div>
    <div style="text-align:right;">
      <div style="font-size:13px; color:#888;">Invoice #</div>
      <div style="font-size:17px; font-weight:bold;">{{ invoice_number }}</div>
      <div style="font-size:13px; color:#888; margin-top:6px;">Date Issued</div>
      <div style="font-size:14px;">{{ date }}</div>
    </div>
  </div>

  <div class="meta-row">
    <div class="meta-block">
      <h3>Billed To</h3>
      <p><strong>{{ client_name }}</strong><br>
      {{ client_address }}<br>
      {% if client_phone %}{{ client_phone }}<br>{% endif %}
      {% if client_email %}{{ client_email }}{% endif %}</p>
    </div>
    <div class="meta-block">
      <h3>Job</h3>
      <p><strong>{{ job_title }}</strong><br>{{ job_address }}</p>
    </div>
    <div class="meta-block" style="text-align:right;">
      <h3>Due Date</h3>
      <p style="font-size:16px; font-weight:bold; color:#c0392b;">{{ due_date }}</p>
      <p style="font-size:12px; color:#888;">{{ payment_terms }}</p>
    </div>
  </div>

  <div class="section-title">Services &amp; Materials</div>
  <table>
    <thead>
      <tr>
        <th style="width:50%;">Description</th>
        <th class="num">Qty</th>
        <th class="num">Unit Price</th>
        <th class="num">Amount</th>
      </tr>
    </thead>
    <tbody>
      {% for item in line_items %}
      <tr>
        <td>{{ item.description }}</td>
        <td class="num">{{ item.qty }}</td>
        <td class="num">${{ "%.2f"|format(item.unit_price) }}</td>
        <td class="num">${{ "%.2f"|format(item.amount) }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <div class="totals">
    <table>
      <tr><td>Subtotal</td><td class="num">${{ "%.2f"|format(subtotal) }}</td></tr>
      {% if tax_amount %}<tr><td>Tax ({{ tax_rate }}%)</td><td class="num">${{ "%.2f"|format(tax_amount) }}</td></tr>{% endif %}
      <tr class="grand-total"><td>TOTAL DUE</td><td class="num">${{ "%.2f"|format(total) }}</td></tr>
    </table>
  </div>

  <div class="due-box">
    <strong>Payment Due: {{ due_date }}</strong> &mdash; {{ payment_terms }}<br>
    Please make checks payable to <strong>{{ business_name }}</strong> or pay online via the link in your email.
  </div>

  {% if notes %}
  <div class="section-title">Notes</div>
  <p style="font-size:13px; color:#555;">{{ notes }}</p>
  {% endif %}

  <div class="footer">
    {{ business_name }} &bull; {{ contractor_phone }} &bull; {{ contractor_email }}
    {% if license_no %}&bull; License #{{ license_no }}{% endif %}
    <br>Thank you for your business!
  </div>
</div>
</body>
</html>"""


def generate_invoice_pdf() -> str:
    from jinja2 import Template
    today = datetime.now()
    due_date = today + timedelta(days=15)
    subtotal = sum(i["amount"] for i in INVOICE_LINE_ITEMS)
    invoice_number = f"INV-{today.strftime('%Y%m%d')}-DEMO01"

    template = Template(INVOICE_TEMPLATE)
    html = template.render(
        business_name=CONTRACTOR["business_name"],
        trade=CONTRACTOR["trade"],
        contractor_phone=CONTRACTOR["phone"],
        contractor_email=CONTRACTOR["email"],
        license_no=CONTRACTOR["license_no"],
        client_name=CLIENT["name"],
        client_address=CLIENT["address"],
        client_phone=CLIENT["phone"],
        client_email=CLIENT["email"],
        date=today.strftime("%B %d, %Y"),
        due_date=due_date.strftime("%B %d, %Y"),
        invoice_number=invoice_number,
        payment_terms=CONTRACTOR["invoice_terms"],
        status="SENT",
        job_title=JOB["title"],
        job_address=JOB["address"],
        line_items=INVOICE_LINE_ITEMS,
        subtotal=subtotal,
        tax_rate=None,
        tax_amount=0,
        total=subtotal,
        notes="Work completed on " + today.strftime("%B %d, %Y") + ". All materials and labor included as quoted.",
    )

    path = OUTPUT_DIR / "sample_invoice_DEMO01.pdf"
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(path))
    print(f"  Invoice PDF: {path}")
    return str(path)


# ── Send via Gmail ───────────────────────────────────────────────────

async def send_email_with_attachments(refresh_token: str, quote_path: str, invoice_path: str):
    import base64
    import mimetypes
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    from src.email_client import GmailClient

    TO = "ford.genereaux@dysonswarmtechnologies.com"

    gmail = GmailClient(refresh_token)
    access_token = await gmail._get_access_token()

    # Build multipart email with attachments manually
    import httpx

    msg = MIMEMultipart()
    msg["To"] = TO
    msg["Subject"] = "FIELDHAND Demo — Sample Quote & Invoice PDFs"

    body = MIMEText("""Hi Ford,

Here are two sample documents generated by FIELDHAND for a demo job:

  - Quote EST-DEMO01: Water Heater Replacement for Sarah Chen ($1,400.00)
  - Invoice INV-DEMO01: Same job, ready to send after completion

These were generated by the WeasyPrint PDF pipeline. The quote uses
the Jinja2 HTML template in templates/quote.html. The invoice uses
a matching invoice template with a green header scheme.

In production:
  - Quotes get generated and emailed when a contractor says "send the quote to [client]"
  - Invoices are created via Stripe (with a real payment link) OR as PDF fallback

Both are attached as PDFs.

— FIELDHAND

---
Morales Plumbing LLC | jake@moralesplumbing.com | (603) 555-0199
""", "plain")
    msg.attach(body)

    for filepath in [quote_path, invoice_path]:
        with open(filepath, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        filename = Path(filepath).name
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": raw},
        )
        resp.raise_for_status()
        result = resp.json()
        print(f"  Email sent! Message ID: {result.get('id')}")
        return result


# ── Main ─────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  FIELDHAND — Send Sample Quote + Invoice PDFs")
    print("="*60)

    # Load contractor's Gmail token from DB
    from src.database import SessionLocal, engine, Base
    import src.models  # noqa
    from src.models import Contractor

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    contractor = db.query(Contractor).filter(
        Contractor.phone == "+16036673203"
    ).first()

    if not contractor:
        # fallback: find any contractor with a gmail token
        contractor = db.query(Contractor).filter(
            Contractor.gmail_refresh_token.isnot(None)
        ).first()

    if not contractor or not contractor.gmail_refresh_token:
        print("ERROR: No contractor with Gmail connected found in DB.")
        print("Run the server and connect Gmail at /email/connect/<contractor_id>")
        db.close()
        return

    print(f"\nSending as: {contractor.name} ({contractor.work_email or contractor.email or contractor.phone})")
    print(f"To: ford.genereaux@dysonswarmtechnologies.com\n")

    print("Generating PDFs...")
    quote_path = generate_quote_pdf()
    invoice_path = generate_invoice_pdf()

    print("\nSending email...")
    asyncio.run(send_email_with_attachments(
        contractor.gmail_refresh_token,
        quote_path,
        invoice_path,
    ))

    db.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
