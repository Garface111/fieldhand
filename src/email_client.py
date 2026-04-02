"""
Gmail API wrapper for reading and sending contractor work email.

OAuth tokens stored per contractor in DB.
For dev/testing: can use SMTP with app password as fallback.
"""
import os
import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from dataclasses import dataclass
from typing import Optional
import httpx
from dotenv import load_dotenv

load_dotenv()

GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


@dataclass
class EmailMessage:
    message_id: str
    thread_id: str
    sender: str
    sender_name: str
    subject: str
    body: str
    received_at: str
    labels: list[str]


class GmailClient:
    def __init__(self, refresh_token: str):
        self.refresh_token = refresh_token
        self._access_token: str | None = None

    async def _get_access_token(self) -> str:
        """Refresh the access token using stored refresh token."""
        async with httpx.AsyncClient() as http:
            resp = await http.post(GMAIL_TOKEN_URL, data={
                "client_id": os.getenv("GMAIL_CLIENT_ID"),
                "client_secret": os.getenv("GMAIL_CLIENT_SECRET"),
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            })
            resp.raise_for_status()
            self._access_token = resp.json()["access_token"]
        return self._access_token

    async def _headers(self) -> dict:
        token = await self._get_access_token()
        return {"Authorization": f"Bearer {token}"}

    async def get_unread(self, max_results: int = 20) -> list[EmailMessage]:
        """Fetch unread emails from inbox."""
        headers = await self._headers()
        async with httpx.AsyncClient() as http:
            # List unread message IDs
            resp = await http.get(
                f"{GMAIL_API_BASE}/users/me/messages",
                headers=headers,
                params={"q": "is:unread in:inbox", "maxResults": max_results},
            )
            resp.raise_for_status()
            items = resp.json().get("messages", [])

            emails = []
            for item in items:
                msg = await self._fetch_message(item["id"], headers, http)
                if msg:
                    emails.append(msg)
        return emails

    async def _fetch_message(self, msg_id: str, headers: dict, http: httpx.AsyncClient) -> EmailMessage | None:
        resp = await http.get(
            f"{GMAIL_API_BASE}/users/me/messages/{msg_id}",
            headers=headers,
            params={"format": "full"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()

        payload = data.get("payload", {})
        headers_list = payload.get("headers", [])
        h = {h["name"].lower(): h["value"] for h in headers_list}

        subject = h.get("subject", "(no subject)")
        sender = h.get("from", "")
        sender_name = sender.split("<")[0].strip().strip('"') if "<" in sender else sender
        date = h.get("date", "")
        labels = data.get("labelIds", [])

        body = _extract_body(payload)

        return EmailMessage(
            message_id=msg_id,
            thread_id=data.get("threadId", ""),
            sender=sender,
            sender_name=sender_name,
            subject=subject,
            body=body[:3000],  # cap at 3k chars for LLM context
            received_at=date,
            labels=labels,
        )

    async def mark_read(self, message_id: str):
        headers = await self._headers()
        async with httpx.AsyncClient() as http:
            await http.post(
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}/modify",
                headers=headers,
                json={"removeLabelIds": ["UNREAD"]},
            )

    async def send(self, to: str, subject: str, body: str,
                   reply_to_thread_id: str = None,
                   attachments: list[tuple[str, bytes]] = None):
        """Send an email. attachments: list of (filename, bytes)."""
        headers = await self._headers()

        if attachments:
            msg = MIMEMultipart()
            msg.attach(MIMEText(body, "plain"))
            for filename, data in attachments:
                part = MIMEApplication(data, Name=filename)
                part["Content-Disposition"] = f'attachment; filename="{filename}"'
                msg.attach(part)
        else:
            msg = MIMEText(body, "plain")

        msg["To"] = to
        msg["Subject"] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        payload = {"raw": raw}
        if reply_to_thread_id:
            payload["threadId"] = reply_to_thread_id

        async with httpx.AsyncClient() as http:
            await http.post(
                f"{GMAIL_API_BASE}/users/me/messages/send",
                headers=headers,
                json=payload,
            )


def _extract_body(payload: dict) -> str:
    """Recursively extract text/plain body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        text = _extract_body(part)
        if text:
            return text
    return ""
