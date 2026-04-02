"""
Gmail OAuth 2.0 flow for linking a contractor's Google Workspace / Gmail account.

Flow:
  GET  /email/connect/{contractor_id}  -> redirects to Google consent screen
  GET  /email/callback                 -> Google redirects back here with code
  GET  /email/status/{contractor_id}   -> check if email is connected
  POST /email/disconnect/{contractor_id} -> revoke and remove token

After linking, the agent can read and draft emails on behalf of the contractor.
"""
import os
import json
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode
import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from src.database import SessionLocal
from src.models.contractor import Contractor
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/email")

GOOGLE_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
]

# Temp state store: state_token -> contractor_id
# In prod use Redis or DB
_oauth_states: dict[str, str] = {}


def get_redirect_uri(request: Request) -> str:
    base = os.getenv("APP_BASE_URL", str(request.base_url).rstrip("/"))
    return f"{base}/email/callback"


@router.get("/connect/{contractor_id}", response_class=HTMLResponse)
async def connect_email(contractor_id: str, request: Request):
    """Start the OAuth flow — redirect contractor to Google consent screen."""
    if not GOOGLE_CLIENT_ID:
        return HTMLResponse(_no_credentials_page(), status_code=503)

    db = SessionLocal()
    try:
        contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
        if not contractor:
            raise HTTPException(status_code=404, detail="Contractor not found")
    finally:
        db.close()

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = contractor_id

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": get_redirect_uri(request),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "login_hint": contractor.email or "",
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(auth_url)


@router.get("/callback")
async def oauth_callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Google redirects here after the user approves access."""
    if error:
        return HTMLResponse(_error_page(f"Google returned an error: {error}"))

    if not code or not state:
        return HTMLResponse(_error_page("Missing code or state parameter."))

    contractor_id = _oauth_states.pop(state, None)
    if not contractor_id:
        return HTMLResponse(_error_page("Invalid or expired session. Please try connecting again."))

    # Exchange code for tokens
    redirect_uri = get_redirect_uri(request)
    async with httpx.AsyncClient() as http:
        resp = await http.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        if resp.status_code != 200:
            return HTMLResponse(_error_page(f"Token exchange failed: {resp.text}"))
        tokens = resp.json()

        # Get their email address
        userinfo_resp = await http.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        userinfo = userinfo_resp.json() if userinfo_resp.status_code == 200 else {}

    # Save refresh token + email to contractor record
    db = SessionLocal()
    try:
        contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
        if not contractor:
            return HTMLResponse(_error_page("Contractor not found."))

        contractor.gmail_refresh_token = tokens.get("refresh_token")
        contractor.work_email = userinfo.get("email") or contractor.email
        if not contractor.email:
            contractor.email = userinfo.get("email")
        db.commit()

        email_addr = contractor.work_email or "your email"
        name = contractor.name.split()[0]

        # Send SMS confirmation
        _notify_contractor_sms(contractor, f"✅ Email connected: {email_addr}. I'll start reading your inbox and drafting replies. You approve before anything sends.")

    finally:
        db.close()

    return HTMLResponse(_success_page(name, email_addr))


@router.get("/status/{contractor_id}")
async def email_status(contractor_id: str):
    db = SessionLocal()
    try:
        contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
        if not contractor:
            raise HTTPException(status_code=404, detail="Not found")
        return {
            "connected": bool(contractor.gmail_refresh_token),
            "email": contractor.work_email,
        }
    finally:
        db.close()


@router.post("/disconnect/{contractor_id}")
async def disconnect_email(contractor_id: str):
    db = SessionLocal()
    try:
        contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
        if not contractor:
            raise HTTPException(status_code=404, detail="Not found")

        if contractor.gmail_refresh_token:
            # Revoke the token with Google
            try:
                async with httpx.AsyncClient() as http:
                    await http.post(GOOGLE_REVOKE_URL, params={"token": contractor.gmail_refresh_token})
            except Exception:
                pass
            contractor.gmail_refresh_token = None

        db.commit()
        return {"ok": True, "message": "Email disconnected."}
    finally:
        db.close()


def _notify_contractor_sms(contractor: Contractor, message: str):
    try:
        from twilio.rest import Client
        sid = os.getenv("TWILIO_ACCOUNT_SID")
        token = os.getenv("TWILIO_AUTH_TOKEN")
        from_num = os.getenv("TWILIO_PHONE_NUMBER")
        if all([sid, token, from_num]):
            Client(sid, token).messages.create(
                from_=from_num, to=contractor.phone, body=message
            )
    except Exception:
        pass


def _success_page(name: str, email: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FIELDHAND — Email Connected</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex; align-items: center;
         justify-content: center; min-height: 100vh; background: #f4f6f9; padding: 20px; }}
  .card {{ background: white; border-radius: 12px; padding: 48px 40px;
           text-align: center; max-width: 440px; box-shadow: 0 4px 24px rgba(0,0,0,0.1); }}
  .icon {{ font-size: 56px; margin-bottom: 16px; }}
  h2 {{ color: #1e3a5f; font-size: 24px; margin-bottom: 10px; }}
  p {{ color: #555; font-size: 15px; line-height: 1.6; }}
  .email {{ font-weight: 600; color: #1e3a5f; }}
</style>
</head>
<body>
<div class="card">
  <div class="icon">✅</div>
  <h2>Email connected, {name}!</h2>
  <p>FIELDHAND is now linked to <span class="email">{email}</span>.</p>
  <p>I'll start reading your inbox, flagging important messages, and drafting replies for your approval.</p>
  <p>You can close this tab. Check your phone for a confirmation text.</p>
</div>
</body>
</html>"""


def _error_page(msg: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>FIELDHAND — Error</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;
min-height:100vh;background:#f4f6f9;padding:20px;}}
.card{{background:white;border-radius:12px;padding:48px 40px;text-align:center;max-width:440px;}}
h2{{color:#c0392b;}}p{{color:#555;}}</style></head>
<body><div class="card"><h2>Something went wrong</h2><p>{msg}</p>
<p><a href="/">Try again</a></p></div></body></html>"""


def _no_credentials_page() -> str:
    return """<!DOCTYPE html>
<html><head><title>FIELDHAND — Setup Required</title></head>
<body style="font-family:sans-serif;padding:40px;max-width:600px">
<h2>Gmail OAuth Not Configured</h2>
<p>Add GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET to your .env file.</p>
<p>See setup instructions below.</p>
</body></html>"""
