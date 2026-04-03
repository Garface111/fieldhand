"""
Permit application prep sheet generator.
Generates a pre-filled, professional PDF the contractor reviews before submitting.
"""
from datetime import datetime
from pathlib import Path
from jinja2 import Template
from weasyprint import HTML

OUTPUT_DIR = Path(__file__).parent.parent.parent / "generated_docs"
OUTPUT_DIR.mkdir(exist_ok=True)

PERMIT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 12.5px; line-height: 1.55; color: #1c1c1e; background: #fff; }
  .page { max-width: 780px; margin: 0 auto; }

  .header { padding: 32px 48px 24px; border-bottom: 3px solid #1a4731; display: flex; justify-content: space-between; align-items: flex-start; }
  .brand-name { font-size: 21px; font-weight: 700; color: #1a4731; letter-spacing: -0.3px; margin-bottom: 3px; }
  .brand-trade { font-size: 11px; color: #8e8e93; text-transform: uppercase; letter-spacing: 1.2px; }
  .header-contact { text-align: right; font-size: 12px; color: #48484a; line-height: 1.7; }

  .doc-strip { background: #1a4731; padding: 10px 48px; display: flex; justify-content: space-between; align-items: center; }
  .doc-type { font-size: 11px; font-weight: 700; color: #fff; letter-spacing: 2px; text-transform: uppercase; }
  .doc-sub { font-size: 11px; color: rgba(255,255,255,0.55); }

  .warning-bar { background: #fffbeb; border-bottom: 1px solid #fcd34d; padding: 9px 48px; display: flex; align-items: center; gap: 10px; font-size: 11px; color: #92400e; font-weight: 600; }
  .warning-icon { font-size: 14px; }

  .body { padding: 28px 48px; }

  /* Section card */
  .section-card { border: 1px solid #e5e5ea; border-radius: 10px; overflow: hidden; margin-bottom: 20px; }
  .section-card-header { background: #1a4731; padding: 8px 18px; font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; color: #fff; }
  .field-grid { display: grid; grid-template-columns: 1fr 1fr; }
  .field-grid.three { grid-template-columns: 1fr 1fr 1fr; }
  .field-grid.one { grid-template-columns: 1fr; }
  .field-cell { padding: 11px 18px; border-right: 1px solid #e5e5ea; border-bottom: 1px solid #e5e5ea; }
  .field-cell:nth-child(2n) { border-right: none; }
  .field-grid.three .field-cell:nth-child(2n) { border-right: 1px solid #e5e5ea; }
  .field-grid.three .field-cell:nth-child(3n) { border-right: none; }
  .field-grid.one .field-cell { border-right: none; }
  .field-label { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #8e8e93; margin-bottom: 4px; }
  .field-value { font-size: 13px; color: #1c1c1e; font-weight: 500; }
  .field-value.missing { color: #d97706; font-style: italic; font-weight: 400; }
  .field-value.large { font-size: 15px; font-weight: 700; color: #1a4731; }

  /* Scope box */
  .scope-box { padding: 14px 18px; font-size: 13px; color: #3a3a3c; line-height: 1.65; background: #f9f9fb; }

  /* Checklist */
  .checklist { padding: 4px 0; }
  .check-item { display: flex; align-items: flex-start; gap: 12px; padding: 9px 18px; border-bottom: 1px solid #f2f2f7; font-size: 12.5px; }
  .check-item:last-child { border-bottom: none; }
  .check-box { width: 16px; height: 16px; border: 1.5px solid #1a4731; border-radius: 3px; flex-shrink: 0; margin-top: 1px; display: flex; align-items: center; justify-content: center; font-size: 11px; color: #1a4731; font-weight: 700; }
  .check-box.done { background: #1a4731; color: #fff; }
  .check-text { color: #3a3a3c; }
  .check-text.done { color: #1a4731; font-weight: 600; }

  /* Instructions */
  .instructions-box { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px; padding: 16px 20px; margin-top: 20px; }
  .instructions-heading { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px; color: #166534; margin-bottom: 10px; }
  .instructions-body { font-size: 12px; color: #15803d; line-height: 1.75; }
  .instructions-body strong { color: #14532d; }

  .footer { margin-top: 36px; padding: 12px 48px; background: #f9f9fb; border-top: 1px solid #e5e5ea; display: flex; justify-content: space-between; font-size: 10px; color: #8e8e93; }
</style>
</head>
<body>
<div class="page">

  <div class="header">
    <div>
      <div class="brand-name">{{ business_name }}</div>
      <div class="brand-trade">{{ trade }} &mdash; Permit Preparation</div>
    </div>
    <div class="header-contact">
      {{ contractor_phone }}<br>
      {% if contractor_email %}{{ contractor_email }}<br>{% endif %}
      {% if license_no %}<span style="font-size:10px;color:#8e8e93;text-transform:uppercase;letter-spacing:.5px;">License #{{ license_no }}</span>{% endif %}
    </div>
  </div>

  <div class="doc-strip">
    <span class="doc-type">Permit Application Prep Sheet</span>
    <span class="doc-sub">{{ date }}</span>
  </div>

  <div class="warning-bar">
    <span class="warning-icon">&#9888;</span>
    This is a preparation document only — not a permit application. Review all fields, then transfer to your municipality's official form.
  </div>

  <div class="body">

    <!-- Property & Project -->
    <div class="section-card">
      <div class="section-card-header">Property &amp; Project</div>
      <div class="field-grid">
        <div class="field-cell">
          <div class="field-label">Job Site Address</div>
          <div class="field-value {% if not job_address %}missing{% endif %}">{{ job_address or 'Not provided' }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Project Type</div>
          <div class="field-value">{{ project_type }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Property Owner Name</div>
          <div class="field-value {% if not client_name %}missing{% endif %}">{{ client_name or 'Not provided' }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Owner Phone</div>
          <div class="field-value {% if not client_phone %}missing{% endif %}">{{ client_phone or 'Not provided' }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Owner Email</div>
          <div class="field-value {% if not client_email %}missing{% endif %}">{{ client_email or 'Not provided' }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Estimated Start Date</div>
          <div class="field-value {% if not start_date %}missing{% endif %}">{{ start_date or 'Not specified' }}</div>
        </div>
      </div>
    </div>

    <!-- Scope -->
    <div class="section-card">
      <div class="section-card-header">Scope of Work</div>
      <div class="scope-box">{{ scope_of_work }}</div>
    </div>

    <!-- Contractor -->
    <div class="section-card">
      <div class="section-card-header">Contractor Information</div>
      <div class="field-grid three">
        <div class="field-cell">
          <div class="field-label">Contractor Name</div>
          <div class="field-value">{{ contractor_name }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Business Name</div>
          <div class="field-value">{{ business_name }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">License Number</div>
          <div class="field-value {% if not license_no %}missing large{% else %}large{% endif %}">{{ license_no or 'ADD LICENSE NUMBER' }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Business Phone</div>
          <div class="field-value">{{ contractor_phone }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Business Email</div>
          <div class="field-value {% if not contractor_email %}missing{% endif %}">{{ contractor_email or 'Not provided' }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Business Address</div>
          <div class="field-value {% if not business_address %}missing{% endif %}">{{ business_address or 'Not provided' }}</div>
        </div>
      </div>
    </div>

    <!-- Insurance -->
    <div class="section-card">
      <div class="section-card-header">Insurance &amp; Compliance</div>
      <div class="field-grid">
        <div class="field-cell">
          <div class="field-label">GL Insurance Carrier</div>
          <div class="field-value {% if not gl_carrier %}missing{% endif %}">{{ gl_carrier or 'Not provided' }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">GL Policy Number</div>
          <div class="field-value {% if not gl_policy %}missing{% endif %}">{{ gl_policy or 'Not provided' }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">GL Expiration</div>
          <div class="field-value {% if not gl_expiry %}missing{% endif %}">{{ gl_expiry or 'Not provided' }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Workers Compensation</div>
          <div class="field-value">{{ wc_status }}</div>
        </div>
      </div>
    </div>

    <!-- Financials -->
    <div class="section-card">
      <div class="section-card-header">Project Value</div>
      <div class="field-grid">
        <div class="field-cell">
          <div class="field-label">Estimated Project Value</div>
          <div class="field-value large">${{ "%.2f"|format(estimated_value) }}</div>
        </div>
        <div class="field-cell">
          <div class="field-label">Estimated Permit Fee</div>
          <div class="field-value">~1–2% of project value (verify with building dept)</div>
        </div>
      </div>
    </div>

    <!-- Pre-submission checklist -->
    <div class="section-card">
      <div class="section-card-header">Pre-Submission Checklist</div>
      <div class="checklist">
        <div class="check-item">
          <div class="check-box {% if license_no %}done{% endif %}">{% if license_no %}&#10003;{% endif %}</div>
          <div class="check-text {% if license_no %}done{% endif %}">Contractor license number filled in</div>
        </div>
        <div class="check-item">
          <div class="check-box {% if gl_carrier %}done{% endif %}">{% if gl_carrier %}&#10003;{% endif %}</div>
          <div class="check-text {% if gl_carrier %}done{% endif %}">GL insurance carrier &amp; policy number on file</div>
        </div>
        <div class="check-item">
          <div class="check-box"><!-- manual --></div>
          <div class="check-text">Certificate of insurance (COI) attached or on file with building dept</div>
        </div>
        <div class="check-item">
          <div class="check-box {% if wc_status %}done{% endif %}">{% if wc_status %}&#10003;{% endif %}</div>
          <div class="check-text {% if wc_status %}done{% endif %}">Workers comp policy or exemption certificate ready</div>
        </div>
        <div class="check-item">
          <div class="check-box"><!-- manual --></div>
          <div class="check-text">Property owner signature obtained (if required by jurisdiction)</div>
        </div>
        <div class="check-item">
          <div class="check-box"><!-- manual --></div>
          <div class="check-text">Permit fee payment method ready (check, credit card, or online)</div>
        </div>
        <div class="check-item">
          <div class="check-box"><!-- manual --></div>
          <div class="check-text">Site plan or wiring diagram prepared (if required for this scope)</div>
        </div>
      </div>
    </div>

    <!-- Submission instructions -->
    <div class="instructions-box">
      <div class="instructions-heading">How to Submit</div>
      <div class="instructions-body">
        <strong>Online:</strong> Search "[city name] building permit online" or visit your municipality's building department portal.<br>
        <strong>In Person:</strong> Bring this sheet + license + COI to the building department counter. Most issue same-day for standard residential work.<br>
        <strong>By Email:</strong> Many departments accept PDF submissions for jobs under $25,000. Contact them first to confirm.<br><br>
        <strong>Typical turnaround:</strong> Same day to 5 business days for residential. Commercial may take 2–4 weeks.
      </div>
    </div>

  </div><!-- /body -->

  <div class="footer">
    <div>Prepared by FIELDHAND for {{ business_name }} &bull; {{ contractor_phone }}</div>
    <div>{{ date }} &bull; Prep document only — not a permit application</div>
  </div>

</div>
</body>
</html>"""


def generate_permit_prep(job, contractor, client, start_date: str = None) -> str:
    today = datetime.now()

    trade = (contractor.trade or "").lower()
    title = (job.title or "").lower()
    desc = (job.description or "").lower()
    combined = title + " " + desc

    if any(w in combined for w in ["panel", "service", "breaker", "wiring", "electrical", "circuit", "afci", "gfci"]):
        project_type = "Electrical — Service / Wiring"
    elif any(w in combined for w in ["plumbing", "pipe", "water heater", "drain", "sewer", "toilet", "fixture"]):
        project_type = "Plumbing"
    elif any(w in combined for w in ["hvac", "furnace", "ac", "heat", "duct", "ventilation", "mechanical"]):
        project_type = "Mechanical / HVAC"
    elif any(w in combined for w in ["roof", "roofing", "shingle"]):
        project_type = "Roofing"
    elif any(w in combined for w in ["addition", "remodel", "framing", "structural"]):
        project_type = "General Construction"
    else:
        project_type = (contractor.trade or "Trade Work").title()

    if contractor.wc_exempt:
        wc_status = "Exempt (Solo Contractor — no employees)"
    elif contractor.wc_carrier:
        wc_status = f"{contractor.wc_carrier} / {contractor.wc_policy or 'Policy on file'}"
        if contractor.wc_expiration:
            wc_status += f" — exp. {contractor.wc_expiration}"
    else:
        wc_status = ""

    template = Template(PERMIT_HTML)
    html = template.render(
        business_name=contractor.business_name or contractor.name,
        contractor_name=contractor.name,
        trade=contractor.trade or "Contractor",
        contractor_phone=contractor.phone,
        contractor_email=contractor.email or getattr(contractor, 'work_email', None) or "",
        license_no=contractor.license_no,
        business_address=getattr(contractor, 'business_address', None),
        gl_carrier=getattr(contractor, 'gl_carrier', None),
        gl_policy=getattr(contractor, 'gl_policy_number', None),
        gl_expiry=getattr(contractor, 'gl_expiration', None),
        wc_status=wc_status,
        client_name=client.name if client else None,
        client_address=client.address if client else job.address or "",
        client_phone=client.phone if client else "",
        client_email=client.email if client else "",
        job_address=job.address or (client.address if client else ""),
        project_type=project_type,
        scope_of_work=job.description or job.title,
        estimated_value=job.quoted_amount or 0,
        start_date=start_date,
        date=today.strftime("%B %d, %Y"),
    )

    filename = f"permit_prep_{job.id[:8]}_{today.strftime('%Y%m%d')}.pdf"
    path = OUTPUT_DIR / filename
    HTML(string=html).write_pdf(str(path))
    return str(path)
