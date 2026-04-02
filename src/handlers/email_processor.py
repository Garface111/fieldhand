"""
Email processor — reads inbound email, classifies it,
drafts a response via the agent, and queues it for contractor approval.
"""
import os
import json
from anthropic import Anthropic
from sqlalchemy.orm import Session
from src.models import Contractor
from src.email_client import GmailClient, EmailMessage
from src.memory import Memory
from src.agent import ContractorAgent
from dotenv import load_dotenv

load_dotenv()

anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

EMAIL_INTENTS = [
    "new_inquiry",       # someone wants work done
    "client_update",     # client asking for status
    "supplier_invoice",  # vendor sending a bill
    "payment",           # client paying / confirming payment
    "permit",            # permit approval or notice
    "spam",              # junk
    "other",
]


def classify_email(email: EmailMessage) -> str:
    """Quick LLM classification of email intent."""
    resp = anthropic.messages.create(
        model="claude-haiku-4-5",
        max_tokens=20,
        messages=[{
            "role": "user",
            "content": (
                f"Classify this email into ONE of: {', '.join(EMAIL_INTENTS)}\n\n"
                f"From: {email.sender}\n"
                f"Subject: {email.subject}\n"
                f"Body: {email.body[:500]}\n\n"
                "Reply with only the category name."
            ),
        }],
    )
    result = resp.content[0].text.strip().lower()
    return result if result in EMAIL_INTENTS else "other"


def draft_reply(email: EmailMessage, contractor: Contractor, context: dict) -> str:
    """Draft an email reply on behalf of the contractor."""
    resp = anthropic.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": (
                f"You are drafting an email reply for {contractor.name}, "
                f"a {contractor.trade or 'contractor'} who runs {contractor.business_name or 'their business'}.\n\n"
                f"Email received:\n"
                f"From: {email.sender_name}\n"
                f"Subject: {email.subject}\n"
                f"Body: {email.body[:1000]}\n\n"
                f"Business context:\n{json.dumps(context, default=str, indent=2)}\n\n"
                "Write a professional but plain-spoken reply. "
                "Be brief. Sign off as the contractor. "
                "If you need info you don't have, say so naturally. "
                "Do NOT use corporate-speak or filler phrases."
            ),
        }],
    )
    return resp.content[0].text.strip()


async def process_contractor_emails(contractor: Contractor, db: Session) -> list[dict]:
    """
    Read unread emails, classify them, draft replies.
    Returns a list of pending actions for SMS approval.
    """
    if not contractor.gmail_refresh_token:
        return []

    gmail = GmailClient(contractor.gmail_refresh_token)
    memory = Memory(db, contractor.id)
    context = memory.get_context_snapshot()

    try:
        emails = await gmail.get_unread()
    except Exception as e:
        return [{"error": f"Could not read email: {e}"}]

    pending_actions = []

    for email in emails:
        intent = classify_email(email)

        if intent == "spam":
            await gmail.mark_read(email.message_id)
            continue

        draft = draft_reply(email, contractor, context)

        pending_actions.append({
            "type": "email_draft",
            "intent": intent,
            "email_id": email.message_id,
            "thread_id": email.thread_id,
            "from": email.sender,
            "from_name": email.sender_name,
            "subject": email.subject,
            "draft": draft,
        })

        await gmail.mark_read(email.message_id)

    return pending_actions


def format_approval_sms(action: dict) -> str:
    """Format an email draft as an SMS approval request."""
    return (
        f"📧 Email from {action['from_name']} re: \"{action['subject']}\"\n\n"
        f"My draft reply:\n\"{action['draft'][:200]}{'...' if len(action['draft']) > 200 else ''}\"\n\n"
        f"Reply YES to send, NO to skip, or edit it by texting back new text."
    )
