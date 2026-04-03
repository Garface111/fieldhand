"""
Receipt OCR handler — uses GPT-4o vision to extract expense data
from photos of receipts, delivery tickets, or invoices.
"""
import os
import json
import httpx
from openai import AsyncOpenAI
from sqlalchemy.orm import Session
from src.memory import Memory
from dotenv import load_dotenv

load_dotenv()

# Lazy-initialized so missing OPENAI_API_KEY doesn't crash startup
_openai_client = None

def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client


async def process_receipt_image(
    image_url: str,
    contractor_id: str,
    job_hint: str | None,
    db: Session,
) -> str:
    """
    Extract expense data from an image and log it.
    Returns a plain text confirmation for SMS.
    """
    memory = Memory(db, contractor_id)

    # Build the vision prompt
    job_context = ""
    if job_hint:
        job = memory.find_job(job_hint)
        if job:
            job_context = f" Log it to job: '{job.title}'."

    prompt = f"""Look at this receipt or invoice image. Extract:
- vendor (store or supplier name)
- total amount paid
- date (if visible)
- main items purchased (brief summary)
- category: one of [materials, fuel, tools, permits, subcontractor, other]

Return ONLY a JSON object with keys: vendor, amount, date, items_summary, category.
If you can't read the amount, set amount to null.{job_context}"""

    try:
        response = await _get_openai_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
                    ],
                }
            ],
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)

    except Exception as e:
        return f"Got your receipt photo but had trouble reading it ({e}). Can you text me the vendor and amount?"

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

    if job_id:
        memory.log_expense(
            job_id=job_id,
            description=items,
            amount=float(amount),
            category=category,
            vendor=vendor,
        )
        return f"Logged ${float(amount):.2f} at {vendor} ({category}) to '{job_name}'."
    else:
        # Store as unassigned — ask which job
        return (
            f"Got it — ${float(amount):.2f} at {vendor} ({items}). "
            f"Which job do I log this to? Text me the job name."
        )
