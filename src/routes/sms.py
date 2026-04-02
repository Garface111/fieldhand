"""
Twilio SMS webhook — all contractor text messages come through here.

Flow:
  1. Twilio POSTs inbound SMS to /webhook/sms
  2. We look up the contractor by phone number
  3. If unknown: start onboarding
  4. If known: route to agent
  5. Reply with TwiML
"""
import os
from fastapi import APIRouter, Request, Form
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from src.database import SessionLocal
from src.models import Contractor
from src.agent import ContractorAgent
from src.handlers.receipt import process_receipt_image
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

# Simple in-memory onboarding state (survives restarts poorly but fine for MVP)
# Keyed by phone number, value is stage name
_onboarding_state: dict[str, dict] = {}


def twiml_reply(message: str) -> PlainTextResponse:
    """Wrap a text reply in TwiML."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{message}</Message>
</Response>"""
    return PlainTextResponse(xml, media_type="text/xml")


@router.post("/webhook/sms")
async def sms_webhook(
    request: Request,
    From: str = Form(...),
    Body: str = Form(""),
    NumMedia: str = Form("0"),
    MediaUrl0: str = Form(None),
    MediaContentType0: str = Form(None),
):
    phone = From.strip()
    body = Body.strip()

    db: Session = SessionLocal()
    try:
        contractor = db.query(Contractor).filter(Contractor.phone == phone).first()

        # ---- ONBOARDING: unknown number ---- #
        if not contractor:
            return await _handle_onboarding(phone, body, db)

        # ---- IMAGE / RECEIPT ---- #
        num_media = int(NumMedia or 0)
        if num_media > 0 and MediaUrl0:
            reply = await _handle_media(MediaUrl0, MediaContentType0, body, contractor, db)
            return twiml_reply(reply)

        # ---- NORMAL CHAT ---- #
        agent = ContractorAgent(db=db, contractor_id=contractor.id)
        reply = agent.chat(body, channel="sms")
        return twiml_reply(reply)

    finally:
        db.close()


async def _handle_onboarding(phone: str, body: str, db: Session) -> PlainTextResponse:
    """Multi-step onboarding for a new contractor."""
    state = _onboarding_state.get(phone, {"stage": "start"})

    if state["stage"] == "start":
        _onboarding_state[phone] = {"stage": "ask_name"}
        return twiml_reply(
            "Hey! I'm FIELDHAND, your business assistant. "
            "I handle invoices, expenses, client follow-ups, and more. "
            "What's your name?"
        )

    elif state["stage"] == "ask_name":
        state["name"] = body
        state["stage"] = "ask_trade"
        _onboarding_state[phone] = state
        return twiml_reply(f"Nice to meet you, {body}! What trade are you in? (e.g. electrician, plumber, HVAC, general contractor)")

    elif state["stage"] == "ask_trade":
        state["trade"] = body
        state["stage"] = "ask_business"
        _onboarding_state[phone] = state
        return twiml_reply(f"Got it. What's your business name? (or just your name if you don't have one)")

    elif state["stage"] == "ask_business":
        state["business_name"] = body
        # Create the contractor record
        contractor = Contractor(
            name=state["name"],
            phone=phone,
            trade=state["trade"],
            business_name=state["business_name"],
            onboarding_complete=True,
        )
        db.add(contractor)
        db.commit()
        db.refresh(contractor)
        del _onboarding_state[phone]

        return twiml_reply(
            f"You're all set, {contractor.name}! "
            f"I'm your business assistant now. "
            f"Text me anything — add a job, log an expense, send an invoice, or just ask how much you're owed. "
            f"I've got your back."
        )

    return twiml_reply("Something went wrong. Text 'hi' to start over.")


async def _handle_media(
    media_url: str,
    content_type: str,
    caption: str,
    contractor: Contractor,
    db: Session,
) -> str:
    """Route image messages — likely receipts or job site photos."""
    # For now, treat all images as potential receipts
    result = await process_receipt_image(
        image_url=media_url,
        contractor_id=contractor.id,
        job_hint=caption or None,
        db=db,
    )
    return result
