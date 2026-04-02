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
