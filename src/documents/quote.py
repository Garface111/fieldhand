"""
Quote / estimate PDF generator.
Uses Jinja2 to fill the HTML template, then WeasyPrint to render PDF.
"""
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from sqlalchemy.orm import Session
from src.models import Job, Client, Contractor, Document
from dotenv import load_dotenv

load_dotenv()

TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "generated_docs"
OUTPUT_DIR.mkdir(exist_ok=True)

env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


def generate_quote(
    job: Job,
    line_items: list[dict],  # [{description, qty, unit_price, amount}]
    db: Session,
    tax_rate: float = 0.0,
    deposit_pct: float = 0.0,
    notes: str = None,
) -> str:
    """
    Generate a quote PDF. Returns the file path.
    line_items: list of dicts with keys: description, qty, unit_price, amount
    """
    contractor: Contractor = job.contractor
    client: Client = job.client

    subtotal = sum(item["amount"] for item in line_items)
    tax_amount = subtotal * (tax_rate / 100) if tax_rate else 0
    total = subtotal + tax_amount
    deposit_amount = total * (deposit_pct / 100) if deposit_pct else 0

    today = datetime.now()
    valid_until = today + timedelta(days=30)
    estimate_number = f"EST-{today.strftime('%Y%m%d')}-{job.id[:6].upper()}"

    template = env.get_template("quote.html")
    html_content = template.render(
        business_name=contractor.business_name or contractor.name,
        trade=contractor.trade or "Contractor",
        contractor_phone=contractor.phone,
        contractor_email=contractor.email,
        license_no=contractor.license_no,
        client_name=client.name if client else "Valued Customer",
        client_address=client.address if client else job.address,
        client_phone=client.phone if client else None,
        client_email=client.email if client else None,
        date=today.strftime("%B %d, %Y"),
        valid_until=valid_until.strftime("%B %d, %Y"),
        estimate_number=estimate_number,
        description=job.description or job.title,
        line_items=line_items,
        subtotal=subtotal,
        tax_rate=tax_rate if tax_rate else None,
        tax_amount=tax_amount,
        total=total,
        payment_terms=contractor.invoice_terms or "Net 15",
        deposit_required=deposit_pct > 0,
        deposit_pct=deposit_pct,
        deposit_amount=deposit_amount,
        notes=notes,
    )

    filename = f"quote_{job.id[:8]}_{today.strftime('%Y%m%d')}.pdf"
    output_path = OUTPUT_DIR / filename

    HTML(string=html_content, base_url=str(TEMPLATE_DIR)).write_pdf(str(output_path))

    # Update job quoted amount
    if job.quoted_amount is None:
        job.quoted_amount = total
        db.commit()

    # Record document
    doc = Document(
        job_id=job.id,
        contractor_id=job.contractor_id,
        doc_type="quote",
        url=str(output_path),
    )
    db.add(doc)
    db.commit()

    return str(output_path)
