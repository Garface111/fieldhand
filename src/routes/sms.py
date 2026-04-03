"""
Twilio SMS webhook — all contractor text messages come through here.

Flow:
  1. Twilio POSTs inbound SMS to /webhook/sms
  2. We look up the contractor by phone number
  3. If unknown: start onboarding
  4. If known + onboarding incomplete: continue onboarding
  5. Route to agent
  6. Reply with TwiML
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


def twiml_reply(message: str) -> PlainTextResponse:
    safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{safe}</Message>
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
    upper = body.upper().strip()

    # STOP / HELP compliance keywords
    if upper in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"):
        return twiml_reply(
            "You have been unsubscribed from FIELDHAND. "
            "No further messages will be sent. "
            "Text START to re-subscribe anytime."
        )
    if upper == "HELP":
        return twiml_reply(
            "FIELDHAND Help: Text me anything about your jobs, expenses, or invoices. "
            "Reply STOP to unsubscribe. Support: support@fieldhand.app"
        )
    if upper in ("START", "UNSTOP"):
        return twiml_reply(
            "Welcome back to FIELDHAND! You're re-subscribed. "
            "Text me anything to pick up where you left off."
        )

    db: Session = SessionLocal()
    try:
        contractor = db.query(Contractor).filter(Contractor.phone == phone).first()

        # Unknown number — start onboarding
        if not contractor:
            return await _handle_onboarding_new(phone, body, db)

        # Known but onboarding not complete — continue onboarding
        if not contractor.onboarding_complete:
            return await _continue_onboarding(contractor, body, db)

        # Email approval — contractor replying YES/SEND to a pending draft
        if upper in ("YES", "SEND", "SEND IT", "CONFIRM", "LOOKS GOOD", "OK SEND", "YEP", "YUP", "DO IT"):
            if contractor.pending_email:
                result = _fire_pending_email(contractor, db)
                return twiml_reply(result)
            # No pending email — fall through to normal agent

        # Email connect shortcut — contractor texting "connect email" or similar
        if any(kw in upper for kw in ("CONNECT EMAIL", "LINK EMAIL", "EMAIL SETUP", "SETUP EMAIL", "CONNECT GMAIL")):
            base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
            email_link = f"{base_url}/email/connect/{contractor.id}"
            if contractor.gmail_refresh_token:
                return twiml_reply(
                    f"Your email is already connected ({contractor.work_email or 'Gmail'}). "
                    f"To reconnect with a different account:\n{email_link}"
                )
            return twiml_reply(
                f"Tap the link to connect your email — takes 30 seconds:\n\n{email_link}\n\n"
                f"After that I'll send quotes, invoices, and follow-ups straight from your inbox."
            )

        # Image / receipt
        if int(NumMedia or 0) > 0 and MediaUrl0:
            reply = await _handle_media(MediaUrl0, MediaContentType0, body, contractor, db)
            return twiml_reply(reply)

        # Normal agent chat
        agent = ContractorAgent(db=db, contractor_id=contractor.id)
        reply, _cost = agent.chat(body, channel="sms")
        return twiml_reply(reply)

    finally:
        db.close()


# ── Onboarding state machine ──────────────────────────────────────────────────
# Keyed by phone number. In prod: move to Redis or DB.
# Stages: start → ask_name_trade → ask_business_name → ask_rate → done
# Optional: ask_trade_only (when no comma in name+trade message)
_onboarding_state: dict[str, dict] = {}


async def _handle_onboarding_new(phone: str, body: str, db: Session) -> PlainTextResponse:
    _onboarding_state[phone] = {"stage": "ask_name_trade"}
    return twiml_reply(
        "Hey, I'm FIELDHAND — your AI business assistant. "
        "Handles your invoices, quotes, permits, and client follow-ups.\n\n"
        "What's your name and trade? (e.g. 'Jake Morales, electrician')"
    )


async def _continue_onboarding(contractor: Contractor, body: str, db: Session) -> PlainTextResponse:
    phone = contractor.phone
    state = _onboarding_state.get(phone)

    # If server restarted and state is lost, reconstruct from contractor fields
    if not state:
        state = _reconstruct_state(contractor)
        _onboarding_state[phone] = state

    stage = state.get("stage", "ask_name_trade")
    text = body.strip()

    # ── 3-question boot ───────────────────────────────────────────────────────

    if stage == "ask_name_trade":
        if "," in text:
            parts = [p.strip() for p in text.split(",", 1)]
            contractor.name = parts[0].title()
            contractor.trade = parts[1].lower()
            db.commit()
            state["stage"] = "ask_business_name"
            first = contractor.name.split()[0]
            return twiml_reply(
                f"Got it, {first}. What's your business name?\n"
                "(Or just your name if you're solo)"
            )
        else:
            # No comma — save name and ask trade separately
            contractor.name = text.title()
            db.commit()
            state["stage"] = "ask_trade_only"
            return twiml_reply("And your trade? (e.g. electrician, plumber, HVAC)")

    elif stage == "ask_trade_only":
        contractor.trade = text.lower()
        db.commit()
        state["stage"] = "ask_business_name"
        return twiml_reply(
            "What's your business name?\n"
            "(Or just your name if you're solo)"
        )

    elif stage == "ask_business_name":
        contractor.business_name = text
        db.commit()
        state["stage"] = "ask_rate"
        return twiml_reply(
            "What's your hourly labor rate? (just the number, e.g. 110)"
        )

    elif stage == "ask_rate":
        try:
            rate = float(text.replace("$", "").replace("/hr", "").replace("/hour", "").strip())
            contractor.labor_rate = rate
            contractor.onboarding_complete = True
            db.commit()
        except ValueError:
            return twiml_reply("Just send the number — e.g. 95 or 110")

        if phone in _onboarding_state:
            del _onboarding_state[phone]

        first = (contractor.name or "").split()[0]
        base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
        email_link = f"{base_url}/email/connect/{contractor.id}"

        # Send the welcome message first, then the email connect prompt as a follow-up SMS
        _send_follow_up_sms(phone, (
            f"One more thing — connect your email and I'll send quotes, "
            f"invoices, and follow-ups straight from your inbox:\n\n"
            f"{email_link}\n\n"
            f"Takes 30 seconds. Skip it for now if you want — you can always do it later."
        ))

        return twiml_reply(
            f"You're in, {first}! Here's what you can text me:\n\n"
            f"• \"New job, Smith, 412 Maple St, panel upgrade\"\n"
            f"• \"Quote Smith — 200A panel $210, 8 hours labor, 25% markup\"\n"
            f"• \"Smith job is done. Send the invoice.\"\n\n"
            f"I'll ask for anything else as we go. What's your first job?"
        )

    # ── Catch-all ────────────────────────────────────────────────────────────
    return twiml_reply("Something went wrong. Text 'hi' to start over.")


def _reconstruct_state(contractor: Contractor) -> dict:
    """Reconstruct onboarding state from what's already saved (simplified for 3-question flow)."""
    if not contractor.name:
        return {"stage": "ask_name_trade"}
    if not contractor.trade:
        return {"stage": "ask_trade_only"}
    if not contractor.business_name:
        return {"stage": "ask_business_name"}
    if not contractor.labor_rate or contractor.labor_rate == 85.0:
        return {"stage": "ask_rate"}
    # All 3 questions answered — shouldn't reach here mid-onboarding
    return {"stage": "ask_name_trade"}


async def _handle_media(media_url, content_type, caption, contractor, db):
    result = await process_receipt_image(
        image_url=media_url,
        contractor_id=contractor.id,
        job_hint=caption or None,
        db=db,
    )
    return result


def _fire_pending_email(contractor, db) -> str:
    """
    Actually send the staged email that contractor just approved with YES.
    Clears pending_email after sending.
    """
    import json
    try:
        data = json.loads(contractor.pending_email)
    except Exception:
        contractor.pending_email = None
        db.commit()
        return "Couldn't read the pending email. Try drafting it again."

    to = data.get("to", "")
    subject = data.get("subject", "")
    body = data.get("body", "")
    pdf_path = data.get("pdf_path")

    if not contractor.gmail_refresh_token:
        return "Email not connected — can't send. Link your Gmail first."

    try:
        import asyncio
        from src.email_client import GmailClient
        gmail = GmailClient(contractor.gmail_refresh_token)

        if pdf_path:
            import base64
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            import os
            filename = os.path.basename(pdf_path)
            try:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(gmail.send(to=to, subject=subject, body=body,
                                                    attachments=[(filename, pdf_bytes)]))
            except RuntimeError:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(gmail.send(to=to, subject=subject, body=body,
                                                    attachments=[(filename, pdf_bytes)]))
                loop.close()
        else:
            try:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(gmail.send(to=to, subject=subject, body=body))
            except RuntimeError:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(gmail.send(to=to, subject=subject, body=body))
                loop.close()

        # Clear pending and log
        contractor.pending_email = None
        db.commit()
        from src.audit import log as audit_log
        audit_log(db, contractor.id, "email_sent",
                  subject=f"To: {to} — {subject}",
                  channel="email", initiated_by="contractor")
        return f"Sent to {to}."

    except Exception as e:
        return f"Failed to send: {e}. Try again or check your Gmail connection."


def _send_follow_up_sms(to: str, message: str):
    """
    Send a second SMS out-of-band via Twilio (not via TwiML reply).
    Used so onboarding can send two messages — the welcome + the email connect link.
    Fire-and-forget; failures are swallowed so they never block the main reply.
    """
    try:
        from twilio.rest import Client
        sid = os.getenv("TWILIO_ACCOUNT_SID")
        token = os.getenv("TWILIO_AUTH_TOKEN")
        from_num = os.getenv("TWILIO_PHONE_NUMBER")
        if all([sid, token, from_num]):
            Client(sid, token).messages.create(
                from_=from_num,
                to=to,
                body=message,
            )
    except Exception:
        pass
