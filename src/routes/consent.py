"""
Consent / opt-in routes.

GET  /          — opt-in landing page
POST /optin     — submit consent, returns fieldhand number
GET  /privacy   — privacy policy (required for Twilio verification)
GET  /admin/consents — proof-of-consent log for Twilio verification
"""
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from src.database import get_db
from src.models.consent import Consent
from pathlib import Path

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent.parent / "templates"))


class OptInRequest(BaseModel):
    name: str
    phone: str
    email: str = ""
    agreed_to_terms: bool = False


@router.get("/", response_class=HTMLResponse)
async def optin_page(request: Request):
    return templates.TemplateResponse(request=request, name="optin.html")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse(request=request, name="privacy.html")


@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request=request, name="terms.html")


@router.post("/optin")
async def submit_optin(
    data: OptInRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not data.agreed_to_terms:
        return JSONResponse(
            status_code=400,
            content={"detail": "You must agree to the terms to continue."}
        )

    if not data.phone or len(data.phone) < 10:
        return JSONResponse(
            status_code=400,
            content={"detail": "Please enter a valid phone number."}
        )

    # Record consent with full audit trail
    consent = Consent(
        phone=data.phone,
        name=data.name,
        email=data.email or None,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        agreed_to_terms=True,
        opt_in_method="web_form",
        source_url=str(request.url),
    )
    db.add(consent)
    db.commit()

    # Send welcome SMS
    fieldhand_number = os.getenv("TWILIO_PHONE_NUMBER", "")
    _send_welcome_sms(data.phone, data.name, fieldhand_number)

    return {
        "ok": True,
        "fieldhand_number": fieldhand_number or "+1 (833) XXX-XXXX",
        "message": f"Welcome, {data.name}! Check your phone."
    }


@router.get("/admin/consents", response_class=HTMLResponse)
async def consent_log(request: Request, db: Session = Depends(get_db)):
    """
    Proof-of-consent log — share this URL with Twilio during verification.
    Shows all opt-in records with timestamps, IP, and user agent.
    """
    consents = (
        db.query(Consent)
        .order_by(Consent.created_at.desc())
        .limit(200)
        .all()
    )

    rows = ""
    for c in consents:
        rows += f"""
        <tr>
          <td>{c.created_at.strftime('%Y-%m-%d %H:%M:%S UTC') if c.created_at else ''}</td>
          <td>{c.name or ''}</td>
          <td>{_mask_phone(c.phone)}</td>
          <td>{c.opt_in_method}</td>
          <td style="color:{'green' if c.agreed_to_terms else 'red'}">
            {'✓ Agreed' if c.agreed_to_terms else '✗ Not agreed'}
          </td>
          <td style="font-size:11px;color:#888">{c.ip_address or ''}</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>FIELDHAND — Consent Records</title>
<style>
  body {{ font-family: sans-serif; padding: 32px; color: #333; }}
  h1 {{ color: #1e3a5f; }}
  .meta {{ color: #888; font-size: 13px; margin-bottom: 24px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #1e3a5f; color: white; padding: 10px 12px; text-align: left; }}
  td {{ padding: 9px 12px; border-bottom: 1px solid #eee; }}
  tr:hover {{ background: #f9f9f9; }}
  .count {{ font-weight: bold; color: #1e3a5f; }}
</style>
</head>
<body>
<h1>FIELDHAND — SMS Opt-In Consent Records</h1>
<p class="meta">
  This page documents proof of user consent for SMS messaging from FIELDHAND.<br>
  All users explicitly checked an opt-in checkbox and agreed to SMS terms before
  their phone number was enrolled.<br><br>
  Total records: <span class="count">{len(consents)}</span> &nbsp;|&nbsp;
  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
</p>
<table>
  <thead>
    <tr>
      <th>Timestamp (UTC)</th>
      <th>Name</th>
      <th>Phone (masked)</th>
      <th>Opt-In Method</th>
      <th>Consent Given</th>
      <th>IP Address</th>
    </tr>
  </thead>
  <tbody>
    {rows if rows else '<tr><td colspan="6" style="text-align:center;color:#999;padding:32px">No consent records yet.</td></tr>'}
  </tbody>
</table>
<br>
<p style="font-size:12px;color:#aaa">
  FIELDHAND SMS Opt-In System &bull; Consent records retained permanently for compliance.
</p>
</body>
</html>"""

    return HTMLResponse(html)


def _mask_phone(phone: str) -> str:
    if not phone or len(phone) < 6:
        return phone
    return phone[:-4].replace(phone[2:-4], '*' * (len(phone) - 6)) + phone[-4:]


def _send_welcome_sms(to: str, name: str, from_number: str):
    """Send a welcome SMS after opt-in. Fire and forget."""
    try:
        import os
        from twilio.rest import Client
        sid = os.getenv("TWILIO_ACCOUNT_SID")
        token = os.getenv("TWILIO_AUTH_TOKEN")
        if not all([sid, token, from_number]):
            return
        c = Client(sid, token)
        first = name.split()[0] if name else "there"
        c.messages.create(
            from_=from_number,
            to=to,
            body=(
                f"Hey {first}, I'm FIELDHAND — your AI business assistant. "
                f"Text me anything to get started. "
                f"Try: 'New job, [client name], [address], [description]'"
            )
        )
    except Exception:
        pass  # Don't block sign-up if SMS fails
