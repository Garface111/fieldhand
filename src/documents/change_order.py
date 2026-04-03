"""
Change Order PDF generator.
A formal document that amends the original quote with additional scope/cost.
"""
import uuid
from datetime import datetime
from pathlib import Path
from jinja2 import Template
from weasyprint import HTML

OUTPUT_DIR = Path(__file__).parent.parent.parent / "generated_docs"
OUTPUT_DIR.mkdir(exist_ok=True)

CHANGE_ORDER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 13px; line-height: 1.55; color: #1c1c1e; background: #fff; }
  .page { max-width: 780px; margin: 0 auto; }
  .header { padding: 36px 48px 28px; border-bottom: 3px solid #7b2d00; display: flex; justify-content: space-between; align-items: flex-start; }
  .brand-name { font-size: 22px; font-weight: 700; color: #7b2d00; letter-spacing: -0.3px; margin-bottom: 3px; }
  .brand-trade { font-size: 11px; color: #8e8e93; text-transform: uppercase; letter-spacing: 1.2px; }
  .header-contact { text-align: right; font-size: 12px; color: #48484a; line-height: 1.7; }
  .header-contact .license { font-size: 10px; color: #8e8e93; letter-spacing: 0.5px; text-transform: uppercase; }
  .doc-strip { background: #7b2d00; padding: 10px 48px; display: flex; justify-content: space-between; align-items: center; }
  .doc-type { font-size: 11px; font-weight: 700; color: #fff; letter-spacing: 2px; text-transform: uppercase; }
  .doc-number { font-size: 11px; color: rgba(255,255,255,0.6); }
  .body { padding: 32px 48px; }
  .meta-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0; border: 1px solid #e5e5ea; border-radius: 8px; overflow: hidden; margin-bottom: 28px; }
  .meta-cell { padding: 14px 18px; border-right: 1px solid #e5e5ea; }
  .meta-cell:last-child { border-right: none; }
  .meta-label { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px; color: #8e8e93; margin-bottom: 5px; }
  .meta-value { font-size: 13px; color: #1c1c1e; font-weight: 500; line-height: 1.5; }
  .meta-value.accent { color: #7b2d00; font-weight: 700; }
  .section-heading { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; color: #8e8e93; margin: 24px 0 10px; padding-bottom: 6px; border-bottom: 1px solid #e5e5ea; }
  .reason-box { background: #fff8f4; border-left: 4px solid #7b2d00; padding: 12px 16px; border-radius: 0 6px 6px 0; font-size: 12.5px; color: #3a3a3c; line-height: 1.65; margin-bottom: 20px; }
  .original-box { background: #f9f9fb; border: 1px solid #e5e5ea; border-radius: 8px; padding: 12px 18px; font-size: 12.5px; color: #48484a; margin-bottom: 20px; }
  .items-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .items-table thead tr { background: #7b2d00; color: #fff; }
  .items-table thead th { padding: 9px 12px; font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; text-align: left; }
  .items-table thead th.r { text-align: right; }
  .items-table tbody tr { border-bottom: 1px solid #f2f2f7; }
  .items-table tbody tr:nth-child(even) { background: #f9f9fb; }
  .items-table tbody td { padding: 10px 12px; vertical-align: top; }
  .items-table tbody td.r { text-align: right; color: #48484a; }
  .totals-wrap { display: flex; justify-content: flex-end; margin-top: 20px; }
  .totals-box { width: 290px; border: 1px solid #e5e5ea; border-radius: 8px; overflow: hidden; }
  .totals-row { display: flex; justify-content: space-between; padding: 8px 14px; font-size: 12px; border-bottom: 1px solid #f2f2f7; color: #48484a; }
  .totals-row:last-child { border-bottom: none; }
  .totals-row.grand { background: #7b2d00; color: #fff; font-size: 14px; font-weight: 700; padding: 11px 14px; }
  .auth-section { margin-top: 36px; border-top: 1px solid #e5e5ea; padding-top: 24px; }
  .auth-heading { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px; color: #8e8e93; margin-bottom: 8px; }
  .auth-text { font-size: 12px; color: #48484a; margin-bottom: 20px; line-height: 1.65; }
  .sig-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; }
  .sig-line { border-bottom: 1px solid #1c1c1e; height: 36px; margin-bottom: 6px; }
  .sig-label { font-size: 10px; color: #8e8e93; text-transform: uppercase; letter-spacing: 0.8px; }
  .footer { margin-top: 40px; padding: 14px 48px; background: #f9f9fb; border-top: 1px solid #e5e5ea; display: flex; justify-content: space-between; font-size: 10px; color: #8e8e93; }
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div>
      <div class="brand-name">{{ business_name }}</div>
      <div class="brand-trade">{{ trade }} &mdash; Change Order</div>
    </div>
    <div class="header-contact">
      {{ contractor_phone }}<br>
      {% if contractor_email %}{{ contractor_email }}<br>{% endif %}
      {% if license_no %}<span class="license">License #{{ license_no }}</span>{% endif %}
    </div>
  </div>
  <div class="doc-strip">
    <span class="doc-type">Change Order</span>
    <span class="doc-number">{{ co_number }}</span>
  </div>
  <div class="body">
    <div class="meta-grid">
      <div class="meta-cell">
        <div class="meta-label">Client</div>
        <div class="meta-value"><strong>{{ client_name }}</strong></div>
        {% if client_address %}<div class="meta-value" style="font-size:11px;color:#48484a;margin-top:3px;">{{ client_address }}</div>{% endif %}
        {% if client_phone %}<div class="meta-value" style="font-size:11px;color:#48484a;">{{ client_phone }}</div>{% endif %}
        {% if client_email %}<div class="meta-value" style="font-size:11px;color:#48484a;">{{ client_email }}</div>{% endif %}
      </div>
      <div class="meta-cell">
        <div class="meta-label">Project</div>
        <div class="meta-value"><strong>{{ job_title }}</strong></div>
        {% if job_address %}<div class="meta-value" style="font-size:11px;color:#48484a;margin-top:3px;">{{ job_address }}</div>{% endif %}
        <div class="meta-label" style="margin-top:10px;">Original Estimate</div>
        <div class="meta-value">{{ original_estimate_number }}</div>
      </div>
      <div class="meta-cell">
        <div class="meta-label">Date Issued</div>
        <div class="meta-value">{{ date }}</div>
        <div class="meta-label" style="margin-top:10px;">Revised Total</div>
        <div class="meta-value accent" style="font-size:20px;">${{ "%.2f"|format(revised_total) }}</div>
      </div>
    </div>
    <div class="section-heading">Reason for Change</div>
    <div class="reason-box">{{ reason }}</div>
    <div class="section-heading">Original Contract</div>
    <div class="original-box">
      <strong>Scope:</strong> {{ original_scope }}<br>
      <strong>Original Amount:</strong> ${{ "%.2f"|format(original_amount) }}
    </div>
    <div class="section-heading">Additional Work &amp; Materials</div>
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
        {% for item in line_items %}
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
        <div class="totals-row">
          <span>Change Order Subtotal</span>
          <span>${{ "%.2f"|format(co_subtotal) }}</span>
        </div>
        <div class="totals-row">
          <span>Original Contract</span>
          <span>${{ "%.2f"|format(original_amount) }}</span>
        </div>
        <div class="totals-row grand">
          <span>Revised Total</span>
          <span>${{ "%.2f"|format(revised_total) }}</span>
        </div>
      </div>
    </div>
    <div class="auth-section">
      <div class="auth-heading">Authorization</div>
      <div class="auth-text">
        By signing, the client authorizes the additional work described above and agrees to pay
        the revised total of <strong>${{ "%.2f"|format(revised_total) }}</strong>
        under the original payment terms ({{ payment_terms }}).
      </div>
      <div class="sig-grid">
        <div>
          <div class="sig-line"></div>
          <div class="sig-label">Contractor — {{ business_name }}, {{ date }}</div>
        </div>
        <div>
          <div class="sig-line"></div>
          <div class="sig-label">Client — {{ client_name }}, Date: ___________</div>
        </div>
      </div>
    </div>
  </div>
  <div class="footer">
    <div>{{ business_name }} &bull; {{ contractor_phone }}{% if contractor_email %} &bull; {{ contractor_email }}{% endif %}</div>
    <div>{{ co_number }}</div>
  </div>
</div>
</body>
</html>"""


def generate_change_order(
    job,
    contractor,
    client,
    reason: str,
    line_items: list[dict],
    original_scope: str = None,
    original_estimate_number: str = None,
) -> str:
    """
    Generate a change order PDF.
    Returns file path.
    line_items: [{description, qty, unit_price, amount}]
    """
    today = datetime.now()
    co_number = f"CO-{today.strftime('%Y%m%d')}-{job.id[:6].upper()}"
    co_subtotal = sum(i["amount"] for i in line_items)
    original_amount = job.quoted_amount or 0
    revised_total = original_amount + co_subtotal

    template = Template(CHANGE_ORDER_HTML)
    html = template.render(
        business_name=contractor.business_name or contractor.name,
        trade=contractor.trade or "Contractor",
        contractor_phone=contractor.phone,
        contractor_email=contractor.email or contractor.work_email or "",
        license_no=contractor.license_no,
        client_name=client.name if client else "Client",
        client_address=client.address if client else job.address or "",
        client_phone=client.phone if client else "",
        client_email=client.email if client else "",
        date=today.strftime("%B %d, %Y"),
        co_number=co_number,
        job_title=job.title,
        job_address=job.address or (client.address if client else ""),
        original_estimate_number=original_estimate_number or f"EST-{job.id[:6].upper()}",
        reason=reason,
        original_scope=original_scope or job.description or job.title,
        original_amount=original_amount,
        line_items=line_items,
        co_subtotal=co_subtotal,
        revised_total=revised_total,
        payment_terms=contractor.invoice_terms or "Net 15",
    )

    filename = f"change_order_{job.id[:8]}_{today.strftime('%Y%m%d')}.pdf"
    path = OUTPUT_DIR / filename
    HTML(string=html).write_pdf(str(path))
    return str(path), co_number, revised_total
