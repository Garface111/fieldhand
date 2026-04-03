"""
Stripe invoice generation and management.
Creates a real Stripe invoice with payment link — no custom PDF needed.
"""
import os
import stripe
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from src.models import Job, Client, Contractor, Invoice, InvoiceStatus
from dotenv import load_dotenv

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


def get_or_create_stripe_customer(contractor: Contractor, client: Client) -> str:
    """Get existing Stripe customer ID or create a new one."""
    if client and hasattr(client, 'stripe_customer_id') and client.stripe_customer_id:
        return client.stripe_customer_id

    # Create customer in Stripe
    customer = stripe.Customer.create(
        name=client.name if client else "Unknown Client",
        email=client.email if client else None,
        phone=client.phone if client else None,
        metadata={
            "contractor_id": contractor.id,
            "client_id": client.id if client else "",
        },
    )
    return customer.id


def create_invoice(
    job: Job,
    amount: float,
    db: Session,
    description: str = None,
    due_days: int = 15,
) -> dict:
    """
    Create a Stripe invoice for a job.
    Returns dict with invoice_id and payment_url.
    """
    if not stripe.api_key:
        # Dev mode — return mock
        mock_id = f"inv_mock_{job.id[:8]}"
        inv = Invoice(
            job_id=job.id,
            contractor_id=job.contractor_id,
            amount=amount,
            status=InvoiceStatus.SENT,
            sent_at=datetime.now(timezone.utc),
            stripe_invoice_id=mock_id,
        )
        db.add(inv)
        db.commit()
        return {
            "invoice_id": mock_id,
            "payment_url": f"https://pay.stripe.com/mock/{mock_id}",
            "amount": amount,
        }

    contractor: Contractor = job.contractor
    client: Client = job.client

    # Get/create Stripe customer
    customer_id = get_or_create_stripe_customer(contractor, client)

    # Create invoice
    stripe_invoice = stripe.Invoice.create(
        customer=customer_id,
        collection_method="send_invoice",
        days_until_due=due_days,
        description=description or f"Invoice for: {job.title}",
        metadata={
            "job_id": job.id,
            "contractor_id": job.contractor_id,
        },
    )

    # Add line item
    stripe.InvoiceItem.create(
        customer=customer_id,
        amount=int(amount * 100),  # Stripe uses cents
        currency="usd",
        description=description or job.title,
        invoice=stripe_invoice.id,
    )

    # Finalize and send
    finalized = stripe.Invoice.finalize_invoice(stripe_invoice.id)
    stripe.Invoice.send_invoice(stripe_invoice.id)

    # Record in our DB
    inv = Invoice(
        job_id=job.id,
        contractor_id=job.contractor_id,
        amount=amount,
        status=InvoiceStatus.SENT,
        sent_at=datetime.now(timezone.utc),
        stripe_invoice_id=stripe_invoice.id,
    )
    db.add(inv)
    db.commit()

    return {
        "invoice_id": stripe_invoice.id,
        "payment_url": finalized.get("hosted_invoice_url", ""),
        "amount": amount,
    }


def handle_payment_webhook(stripe_event: dict, db: Session) -> str | None:
    """
    Process a Stripe webhook event for invoice.paid.
    Returns contractor_id if payment was recorded, None otherwise.
    """
    if stripe_event.get("type") != "invoice.paid":
        return None

    stripe_invoice_id = stripe_event["data"]["object"]["id"]
    inv = db.query(Invoice).filter(Invoice.stripe_invoice_id == stripe_invoice_id).first()
    if not inv:
        return None

    inv.status = InvoiceStatus.PAID
    inv.paid_at = datetime.now(timezone.utc)

    # Mark the job as paid
    job = inv.job
    if job:
        from src.models.job import JobStatus
        job.transition_to(JobStatus.PAID)

    db.commit()
    return inv.contractor_id


def generate_invoice_pdf(job_title: str, contractor, client, amount: float, address: str = "") -> str:
    """Standalone invoice PDF — no Stripe required. Returns file path."""
    from jinja2 import Template
    from weasyprint import HTML as WP_HTML
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timedelta as _td
    OUTPUT = _Path(__file__).parent.parent.parent / "generated_docs"
    OUTPUT.mkdir(exist_ok=True)
    today = _dt.now()
    due = today + _td(days=15)
    inv_num = f"INV-{today.strftime('%Y%m%d')}-{job_title[:6].upper().replace(' ','')}"
    HTML_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 13px; line-height: 1.55; color: #1c1c1e; background: #fff; }
  .page { max-width: 780px; margin: 0 auto; }
  .header { padding: 36px 48px 28px; border-bottom: 3px solid #0f2744; display: flex; justify-content: space-between; align-items: flex-start; }
  .brand-name { font-size: 22px; font-weight: 700; color: #0f2744; letter-spacing: -0.3px; margin-bottom: 3px; }
  .brand-trade { font-size: 11px; color: #8e8e93; text-transform: uppercase; letter-spacing: 1.2px; }
  .header-contact { text-align: right; font-size: 12px; color: #48484a; line-height: 1.7; }
  .header-contact .license { display: inline-block; margin-top: 5px; font-size: 10px; color: #8e8e93; letter-spacing: 0.5px; text-transform: uppercase; }
  .doc-strip { background: #0f2744; padding: 10px 48px; display: flex; justify-content: space-between; align-items: center; }
  .doc-type { font-size: 11px; font-weight: 700; color: #fff; letter-spacing: 2px; text-transform: uppercase; }
  .doc-number { font-size: 11px; color: rgba(255,255,255,0.6); letter-spacing: 0.5px; }
  .body { padding: 32px 48px; }
  .meta-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0; border: 1px solid #e5e5ea; border-radius: 8px; overflow: hidden; margin-bottom: 32px; }
  .meta-cell { padding: 14px 18px; border-right: 1px solid #e5e5ea; }
  .meta-cell:last-child { border-right: none; }
  .meta-label { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px; color: #8e8e93; margin-bottom: 5px; }
  .meta-value { font-size: 13px; color: #1c1c1e; font-weight: 500; line-height: 1.5; }
  .meta-value.accent { color: #0f2744; font-weight: 700; }
  .meta-value.due { color: #c0392b; font-weight: 700; }
  .status-badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px; background: #fff3e0; color: #e65100; border: 1px solid #ffcc80; }
  .section-heading { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; color: #8e8e93; margin: 28px 0 10px; padding-bottom: 6px; border-bottom: 1px solid #e5e5ea; }
  .items-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .items-table thead tr { background: #0f2744; color: #fff; }
  .items-table thead th { padding: 9px 12px; font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; text-align: left; }
  .items-table thead th.r { text-align: right; }
  .items-table tbody tr { border-bottom: 1px solid #f2f2f7; }
  .items-table tbody tr:nth-child(even) { background: #f9f9fb; }
  .items-table tbody td { padding: 10px 12px; vertical-align: top; color: #1c1c1e; }
  .items-table tbody td.r { text-align: right; color: #48484a; }
  .totals-wrap { display: flex; justify-content: flex-end; margin-top: 20px; }
  .totals-box { width: 260px; border: 1px solid #e5e5ea; border-radius: 8px; overflow: hidden; }
  .totals-row { display: flex; justify-content: space-between; padding: 8px 14px; font-size: 12px; border-bottom: 1px solid #f2f2f7; color: #48484a; }
  .totals-row:last-child { border-bottom: none; }
  .totals-row.grand { background: #0f2744; color: #fff; font-size: 14px; font-weight: 700; padding: 11px 14px; }
  .payment-box { margin-top: 28px; padding: 16px 20px; background: #fff8f4; border: 1px solid #ffcc80; border-left: 4px solid #e65100; border-radius: 0 8px 8px 0; font-size: 12.5px; color: #48484a; line-height: 1.7; }
  .payment-box strong { color: #1c1c1e; }
  .footer { margin-top: 40px; padding: 14px 48px; background: #f9f9fb; border-top: 1px solid #e5e5ea; display: flex; justify-content: space-between; align-items: center; font-size: 10px; color: #8e8e93; }
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div>
      <div class="brand-name">{{ bn }}</div>
      <div class="brand-trade">{{ trade }} Services</div>
    </div>
    <div class="header-contact">
      {{ cp }}<br>
      {% if ce %}{{ ce }}<br>{% endif %}
      {% if lic %}<span class="license">License #{{ lic }}</span>{% endif %}
    </div>
  </div>
  <div class="doc-strip">
    <span class="doc-type">Invoice</span>
    <span class="doc-number">{{ inv_num }}</span>
  </div>
  <div class="body">
    <div class="meta-grid">
      <div class="meta-cell">
        <div class="meta-label">Bill To</div>
        <div class="meta-value"><strong>{{ cn }}</strong></div>
        {% if ca %}<div class="meta-value" style="font-size:11px;color:#48484a;margin-top:3px;">{{ ca }}</div>{% endif %}
        {% if cp_client %}<div class="meta-value" style="font-size:11px;color:#48484a;">{{ cp_client }}</div>{% endif %}
        {% if ce_client %}<div class="meta-value" style="font-size:11px;color:#48484a;">{{ ce_client }}</div>{% endif %}
      </div>
      <div class="meta-cell">
        <div class="meta-label">Invoice Date</div>
        <div class="meta-value">{{ date }}</div>
        <div class="meta-label" style="margin-top:10px;">Due Date</div>
        <div class="meta-value due">{{ due }}</div>
        <div class="meta-label" style="margin-top:10px;">Terms</div>
        <div class="meta-value">{{ terms }}</div>
      </div>
      <div class="meta-cell">
        <div class="meta-label">Amount Due</div>
        <div class="meta-value accent" style="font-size:22px;">${{ "%.2f"|format(total) }}</div>
        <div style="margin-top:10px;"><span class="status-badge">Awaiting Payment</span></div>
      </div>
    </div>
    <div class="section-heading">For Services Rendered</div>
    <table class="items-table">
      <thead>
        <tr>
          <th>Description</th>
          <th class="r">Qty</th>
          <th class="r">Unit Price</th>
          <th class="r">Amount</th>
        </tr>
      </thead>
      <tbody>
        {% for item in items %}
        <tr>
          <td>{{ item.description }}</td>
          <td class="r">{{ item.qty }}</td>
          <td class="r">${{ "%.2f"|format(item.unit_price) }}</td>
          <td class="r">${{ "%.2f"|format(item.amount) }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    <div class="totals-wrap">
      <div class="totals-box">
        <div class="totals-row grand">
          <span>Total Due</span>
          <span>${{ "%.2f"|format(total) }}</span>
        </div>
      </div>
    </div>
    <div class="payment-box">
      <strong>Payment Due: {{ due }}</strong> &mdash; {{ terms }}<br>
      Make checks payable to <strong>{{ bn }}</strong>, or pay online if a payment link was provided.<br>
      Questions? Contact us at {{ cp }}{% if ce %} or {{ ce }}{% endif %}.
    </div>
  </div>
  <div class="footer">
    <div>{{ bn }} &bull; {{ cp }}{% if ce %} &bull; {{ ce }}{% endif %}</div>
    <div>{{ inv_num }}</div>
  </div>
</div>
</body>
</html>"""
    html = Template(HTML_TMPL).render(
        bn=contractor.business_name or contractor.name,
        trade=contractor.trade or "Contractor",
        cp=contractor.phone,
        ce=contractor.email or getattr(contractor, 'work_email', '') or "",
        lic=contractor.license_no or "",
        cn=client.name if client else "Client",
        ca=client.address if client else address,
        cp_client=client.phone if client else "",
        ce_client=client.email if client else "",
        date=today.strftime("%B %d, %Y"),
        due=due.strftime("%B %d, %Y"),
        inv_num=inv_num,
        terms=contractor.invoice_terms or "Net 15",
        job_title=job_title,
        items=[{"description": job_title, "qty": 1, "unit_price": amount, "amount": amount}],
        total=amount,
    )
    fname = f"invoice_{job_title[:14].replace(' ','_')}_{today.strftime('%Y%m%d')}.pdf"
    path = OUTPUT / fname
    WP_HTML(string=html).write_pdf(str(path))
    return str(path)
