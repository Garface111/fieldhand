"""Stripe webhook — handles payment events."""
import os
import stripe
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session
from src.database import SessionLocal
from src.models import Contractor
from src.documents.invoice import handle_payment_webhook
from src.tasks.monitoring import send_sms
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        if WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
        else:
            import json
            event = json.loads(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db: Session = SessionLocal()
    try:
        contractor_id = handle_payment_webhook(event, db)
        if contractor_id:
            contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
            if contractor:
                amount = event["data"]["object"].get("amount_paid", 0) / 100
                job_id = event["data"]["object"].get("metadata", {}).get("job_id", "")
                send_sms(
                    contractor.phone,
                    f"💰 Payment received! ${amount:,.2f} just hit. Invoice paid."
                )
    finally:
        db.close()

    return {"status": "ok"}
