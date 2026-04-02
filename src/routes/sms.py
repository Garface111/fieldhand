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

    # STOP / HELP compliance keywords — handle before anything else
    upper = body.upper().strip()
    if upper in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"):
        # Twilio auto-handles STOP at carrier level, but log it
        return twiml_reply(
            "You have been unsubscribed from FIELDHAND. "
            "No further messages will be sent. "
            "Text START to re-subscribe anytime."
        )
    if upper == "HELP":
        return twiml_reply(
            "FIELDHAND Help: Text me anything about your jobs, expenses, or invoices. "
            "Reply STOP to unsubscribe. "
            "Support: support@fieldhand.app"
        )
    if upper in ("START", "UNSTOP"):
        return twiml_reply(
            "Welcome back to FIELDHAND! You're re-subscribed. "
            "Text me anything to pick up where you left off."
        )

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
    """
    Multi-step onboarding wizard.
    Stages: start -> name -> trade -> business -> rate -> terms -> auto_followup -> done
    """
    state = _onboarding_state.get(phone, {"stage": "start"})

    if state["stage"] == "start":
        _onboarding_state[phone] = {"stage": "ask_name"}
        return twiml_reply(
            "Hey! I'm FIELDHAND — your AI business assistant. "
            "I handle your invoices, expenses, jobs, and client follow-ups. "
            "Takes 60 seconds to set up.\n\n"
            "What's your first and last name?"
        )

    elif state["stage"] == "ask_name":
        state["name"] = body.strip().title()
        state["stage"] = "ask_trade"
        _onboarding_state[phone] = state
        first = state["name"].split()[0]
        return twiml_reply(
            f"Nice to meet you, {first}! "
            f"What trade are you in?\n"
            f"(e.g. electrician, plumber, HVAC, carpenter, general contractor)"
        )

    elif state["stage"] == "ask_trade":
        state["trade"] = body.strip().lower()
        state["stage"] = "ask_business"
        _onboarding_state[phone] = state
        return twiml_reply(
            "What's your business name?\n"
            "(or just your name if you're solo)"
        )

    elif state["stage"] == "ask_business":
        state["business_name"] = body.strip()
        state["stage"] = "ask_rate"
        _onboarding_state[phone] = state
        return twiml_reply(
            "What's your labor rate per hour?\n"
            "(just the number — e.g. 95)"
        )

    elif state["stage"] == "ask_rate":
        try:
            rate = float(body.strip().replace("$", "").replace("/hr", "").strip())
        except ValueError:
            return twiml_reply("Just send the number — e.g. 95 or 125")
        state["labor_rate"] = rate
        state["stage"] = "ask_terms"
        _onboarding_state[phone] = state
        return twiml_reply(
            "Payment terms? This goes on your invoices.\n"
            "Reply:\n"
            "1 — Net 15 (pay within 15 days)\n"
            "2 — Net 30 (pay within 30 days)\n"
            "3 — Due on receipt\n"
            "4 — 50% deposit required"
        )

    elif state["stage"] == "ask_terms":
        terms_map = {
            "1": "Net 15", "net 15": "Net 15",
            "2": "Net 30", "net 30": "Net 30",
            "3": "Due on Receipt", "due on receipt": "Due on Receipt",
            "4": "50% Deposit Required", "50%": "50% Deposit Required",
        }
        terms = terms_map.get(body.strip().lower(), "Net 15")
        state["invoice_terms"] = terms
        state["stage"] = "ask_auto_followup"
        _onboarding_state[phone] = state
        return twiml_reply(
            f"Got it — {terms}.\n\n"
            "Last question: should I automatically follow up "
            "on unpaid invoices for you?\n"
            "Reply YES or NO"
        )

    elif state["stage"] == "ask_auto_followup":
        auto = body.strip().upper().startswith("Y")
        state["auto_followup"] = auto

        # Create the contractor
        contractor = Contractor(
            name=state["name"],
            phone=phone,
            trade=state["trade"],
            business_name=state["business_name"],
            labor_rate=state.get("labor_rate", 85.0),
            markup_pct=20.0,
            invoice_terms=state.get("invoice_terms", "Net 15"),
            onboarding_complete=True,
        )
        db.add(contractor)
        db.commit()
        db.refresh(contractor)
        del _onboarding_state[phone]

        first = contractor.name.split()[0]
        auto_msg = "I'll handle invoice follow-ups automatically." if auto else "I'll flag overdue invoices for you to review."

        # Build the dashboard + email connect URL
        base_url = os.getenv("APP_BASE_URL", "https://fieldhand-ai.loca.lt")
        email_url = f"{base_url}/email/connect/{contractor.id}"

        return twiml_reply(
            f"You're all set, {first}! 🎉\n\n"
            f"Here's what you can text me:\n"
            f"• \"New job, [client], [address], [description]\"\n"
            f"• \"Log $340 materials, Mitchell job\"\n"
            f"• \"How much do I have outstanding?\"\n"
            f"• \"List my jobs\"\n\n"
            f"{auto_msg}\n\n"
            f"Want me to handle your email too? Connect it here:\n"
            f"{email_url}"
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
