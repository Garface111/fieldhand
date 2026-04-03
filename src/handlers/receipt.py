"""
Receipt OCR handler — extracts expense data from photos of receipts,
delivery tickets, or invoices.

Uses Anthropic Claude vision (primary) with OpenAI GPT-4o as fallback.
Works with only ANTHROPIC_API_KEY set — no OpenAI key required.
"""
import os
import json
import base64
import httpx
from sqlalchemy.orm import Session
from src.memory import Memory
from dotenv import load_dotenv

load_dotenv()

RECEIPT_PROMPT = """Look at this receipt or invoice image. Extract:
- vendor (store or supplier name)
- total amount paid
- date (if visible)
- main items purchased (brief summary)
- category: one of [materials, fuel, tools, permits, subcontractor, other]

Return ONLY a JSON object with keys: vendor, amount, date, items_summary, category.
If you can't read the amount, set amount to null."""


async def _fetch_image_as_base64(url: str) -> tuple[str, str]:
    """Download an image URL and return (base64_data, media_type)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=15)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        b64 = base64.standard_b64encode(resp.content).decode("utf-8")
        return b64, content_type


async def _ocr_with_anthropic(image_url: str, prompt: str) -> dict:
    """Use Claude claude-haiku-4-5 vision to read the receipt."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    b64, media_type = await _fetch_image_as_base64(image_url)

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


async def _ocr_with_openai(image_url: str, prompt: str) -> dict:
    """Fallback: use GPT-4o vision if OPENAI_API_KEY is available."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
            ],
        }],
        max_tokens=512,
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


async def process_receipt_image(
    image_url: str,
    contractor_id: str,
    job_hint: str | None,
    db: Session,
) -> str:
    """
    Extract expense data from an image and log it.
    Returns a plain text confirmation for SMS.
    Tries Anthropic first, falls back to OpenAI if available.
    """
    memory = Memory(db, contractor_id)

    prompt = RECEIPT_PROMPT
    if job_hint:
        job = memory.find_job(job_hint)
        if job:
            prompt += f" Log it to job: '{job.title}'."

    # Try Anthropic first (always available), fall back to OpenAI
    data = None
    try:
        data = await _ocr_with_anthropic(image_url, prompt)
    except Exception as anthropic_err:
        if os.getenv("OPENAI_API_KEY"):
            try:
                data = await _ocr_with_openai(image_url, prompt)
            except Exception as openai_err:
                return (
                    f"Got your receipt photo but had trouble reading it. "
                    f"Can you text me the vendor and amount?"
                )
        else:
            return (
                f"Got your receipt photo but had trouble reading it. "
                f"Can you text me the vendor and amount?"
            )

    if not data:
        return "Got your photo but couldn't extract the details. Text me the vendor and amount."

    amount = data.get("amount")
    vendor = data.get("vendor", "Unknown vendor")
    items = data.get("items_summary", "receipt")
    category = data.get("category", "materials")

    if not amount:
        return f"Got your photo from {vendor}. What was the total amount?"

    # Find job to log against
    job_id = None
    job_name = "unassigned"
    if job_hint:
        job = memory.find_job(job_hint)
        if job:
            job_id = job.id
            job_name = job.title

    # Also try to find most recent active job if no hint
    if not job_id:
        from src.models.job import Job, JobStatus
        recent_active = (
            db.query(Job)
            .filter(Job.contractor_id == contractor_id, Job.status == JobStatus.ACTIVE)
            .order_by(Job.created_at.desc())
            .first()
        )
        if recent_active:
            job_id = recent_active.id
            job_name = recent_active.title

    if job_id:
        memory.log_expense(
            job_id=job_id,
            description=items,
            amount=float(amount),
            category=category,
            vendor=vendor,
        )
        return f"Logged ${float(amount):.2f} at {vendor} ({category}) → '{job_name}'."
    else:
        return (
            f"Got it — ${float(amount):.2f} at {vendor} ({items}). "
            f"Which job do I log this to? Text me the job name."
        )
