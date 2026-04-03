"""
Outbound Review Layer — reasoning pass before anything leaves the system.

Every outbound action (email to client, SMS to client, quote, invoice,
change order, picklist) runs through review() before executing.

The reviewer checks:
  1. Correctness — numbers add up, names match, dates are reasonable
  2. Tone — professional, clear, not confusing or offensive
  3. Completeness — no obvious missing info (amount, job name, contact)
  4. Risk — anything that could damage the contractor's relationship or rep

Returns ReviewResult(approved=True) to proceed, or approved=False with a
blocking_reason explaining what's wrong. The caller logs the result either way.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

REVIEW_SYSTEM = """You are a meticulous quality-control reviewer for FIELDHAND, an AI assistant that helps independent trade contractors run their business.

Your job is to review outbound content BEFORE it is sent to a client or outside party. You are the last line of defense before something goes out.

You review for:
1. CORRECTNESS — do dollar amounts look right? Do names/job references match? Are dates reasonable?
2. TONE — is it professional, clear, and respectful? Nothing that could upset or confuse a client.
3. COMPLETENESS — is anything obviously missing? (e.g. no amount on an invoice, no job address on a permit)
4. RISK — would sending this embarrass the contractor, create a legal issue, or damage a client relationship?

Be concise. Your ONLY output is a JSON object with exactly these fields:
{
  "approved": true or false,
  "confidence": "high" | "medium" | "low",
  "issues": ["list of specific issues found, empty if none"],
  "blocking_reason": "short sentence explaining why blocked, empty string if approved",
  "suggestions": ["optional improvements even if approved"]
}

Approve anything reasonable. Only block if there is a clear, specific problem that would cause real harm if sent. Do not invent problems."""


@dataclass
class ReviewResult:
    approved: bool
    confidence: str = "high"
    issues: list[str] = field(default_factory=list)
    blocking_reason: str = ""
    suggestions: list[str] = field(default_factory=list)
    raw: str = ""


def review(
    action_type: str,
    recipient: str,
    content: dict,
    contractor_name: str = "",
    client_name: str = "",
) -> ReviewResult:
    """
    Run a reasoning pass on outbound content before sending.

    action_type: "quote" | "invoice" | "change_order" | "email" | "picklist"
    recipient:   email address or "SMS" 
    content:     dict describing what's being sent — varies by action_type
    """
    prompt = _build_prompt(action_type, recipient, content, contractor_name, client_name)

    try:
        resp = _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            system=REVIEW_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()

        import json, re
        # Extract JSON even if wrapped in markdown code block
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            data = json.loads(raw)

        return ReviewResult(
            approved=bool(data.get("approved", True)),
            confidence=data.get("confidence", "medium"),
            issues=data.get("issues", []),
            blocking_reason=data.get("blocking_reason", ""),
            suggestions=data.get("suggestions", []),
            raw=raw,
        )
    except Exception as e:
        # On any failure, default to approved so we don't silently break
        # outbound actions — but note the failure
        return ReviewResult(
            approved=True,
            confidence="low",
            issues=[f"Review check failed: {e}"],
            blocking_reason="",
            raw=str(e),
        )


def _build_prompt(
    action_type: str,
    recipient: str,
    content: dict,
    contractor_name: str,
    client_name: str,
) -> str:
    lines = [
        f"ACTION TYPE: {action_type.upper()}",
        f"CONTRACTOR: {contractor_name or 'Unknown'}",
        f"RECIPIENT: {recipient}",
        f"CLIENT: {client_name or 'Unknown'}",
        "",
    ]

    if action_type == "quote":
        lines += [
            f"Job: {content.get('job_title', 'N/A')}",
            f"Total: ${content.get('total', 0):,.2f}",
            "Line items:",
        ]
        for item in content.get("line_items", []):
            lines.append(f"  - {item.get('description')} | qty {item.get('qty')} | ${item.get('amount', 0):,.2f}")
        if content.get("notes"):
            lines.append(f"Notes: {content['notes']}")

    elif action_type == "invoice":
        lines += [
            f"Job: {content.get('job_title', 'N/A')}",
            f"Amount: ${content.get('amount', 0):,.2f}",
            f"Terms: {content.get('terms', 'N/A')}",
            f"Email body preview: {content.get('body_preview', '')[:300]}",
        ]

    elif action_type == "change_order":
        lines += [
            f"Job: {content.get('job_title', 'N/A')}",
            f"Reason: {content.get('reason', 'N/A')}",
            f"Additional amount: ${content.get('co_total', 0):,.2f}",
            f"Revised total: ${content.get('revised_total', 0):,.2f}",
            "Line items:",
        ]
        for item in content.get("line_items", []):
            lines.append(f"  - {item.get('description')} | ${item.get('amount', 0):,.2f}")

    elif action_type == "email":
        lines += [
            f"Subject: {content.get('subject', 'N/A')}",
            f"Body:\n{content.get('body', '')[:500]}",
        ]

    elif action_type == "picklist":
        lines += [
            f"Job: {content.get('job_title', 'N/A')}",
            f"Pickup: {content.get('pickup_date', 'N/A')}",
            "Materials:",
        ]
        for m in content.get("materials", []):
            lines.append(f"  - {m.get('qty')} {m.get('unit', 'ea')} of {m.get('description')}")

    lines += [
        "",
        "Review the above and return your JSON assessment.",
    ]

    return "\n".join(lines)


def format_block_message(result: ReviewResult, action_type: str) -> str:
    """Format a blocked review result into a plain-language SMS reply."""
    issue_list = " | ".join(result.issues) if result.issues else result.blocking_reason
    return (
        f"Hold on — caught an issue before sending that {action_type}:\n"
        f"{result.blocking_reason or issue_list}\n"
        f"Fix that and I'll send it."
    )
