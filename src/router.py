"""
Message Router — classifies every inbound contractor message before the main agent.
Uses claude-haiku-4-5 (cheap, fast) to determine:
  - Tier (1=execute, 2=reason, 3=investigate)
  - Which tool categories are needed
  - Whether extended thinking is needed
  - Thinking token budget
  - Whether web search is needed

Pricing reference:
  Haiku:  $0.80/M input, $4.00/M output
  Sonnet: $3.00/M input, $15.00/M output
"""
import json, os
from anthropic import Anthropic
from dotenv import load_dotenv
load_dotenv()

client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

# Tool categories — agent only loads what's needed
TOOL_CATEGORIES = {
    'client':    ['create_client', 'update_client', 'add_client_note'],
    'job':       ['create_job', 'update_job_status', 'list_jobs'],
    'expense':   ['log_expense', 'lookup_price'],
    'invoice':   ['queue_invoice', 'send_invoice_to_client', 'send_quote_to_client'],
    'document':  ['create_change_order', 'generate_permit_prep', 'generate_picklist'],
    'financial': ['get_financial_summary', 'query_business'],
    'email':     ['check_email', 'send_email', 'draft_email'],
    'profile':   ['update_contractor_profile'],
    'confirm':   ['confirm_pending', 'reject_pending'],
    'utility':   ['export_tax_csv', 'calculate'],
    'search':    ['web_search'],
}

CLASSIFIER_PROMPT = """You are a routing classifier for a contractor AI assistant.
Analyze the contractor's message and return a JSON routing decision.

Tier definitions:
1 = EXECUTE: Simple task execution. Log expense, update status, list jobs, add client info.
    No reasoning needed. Fast, cheap path.
2 = REASON: Requires judgment. Building quotes, sending documents, financial questions,
    explaining something, handling multiple steps.
3 = INVESTIGATE: Complex analysis. Business questions ('why am I losing money'), 
    comparisons ('which client is most profitable'), strategic ('should I take this job'),
    permit/legal research, supplier lookup.

Tool categories: client, job, expense, invoice, document, financial, email, profile, confirm, utility, search

Return ONLY valid JSON, no other text:
{
  "tier": 1|2|3,
  "tools_needed": ["category1", "category2"],
  "needs_thinking": true|false,
  "thinking_budget": 0|3000|8000,
  "needs_web_search": true|false,
  "reason": "one sentence why"
}

Examples:
- "log $340 romex Ferguson" -> tier 1, [expense], no thinking
- "mark Smith job active" -> tier 1, [job], no thinking
- "build a quote for the panel upgrade" -> tier 2, [expense, invoice, document], thinking 3000
- "send the invoice to Smith" -> tier 2, [invoice, confirm, email], thinking 3000
- "why am I losing money on commercial jobs?" -> tier 3, [financial, job, expense], thinking 8000
- "what are permit requirements in Manchester NH for 200A panel?" -> tier 3, [document, search], thinking 8000, web search
- "Y" or "yes" -> tier 1, [confirm], no thinking
"""

def classify(message: str, context_hint: str = '') -> dict:
    """
    Classify a contractor message. Returns routing dict.
    context_hint: brief summary of recent conversation (e.g. 'working on Smith panel job')
    """
    user_content = f"Message: {message}"
    if context_hint:
        user_content += f"\nContext: {context_hint}"
    
    try:
        response = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=200,
            system=CLASSIFIER_PROMPT,
            messages=[{'role': 'user', 'content': user_content}]
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        result = json.loads(text)
        result['classifier_tokens'] = {
            'input': response.usage.input_tokens,
            'output': response.usage.output_tokens,
        }
        return result
    except Exception as e:
        # Fail safe: treat as tier 2, all tools
        return {
            'tier': 2,
            'tools_needed': list(TOOL_CATEGORIES.keys()),
            'needs_thinking': False,
            'thinking_budget': 0,
            'needs_web_search': False,
            'reason': f'classification failed: {e}',
            'classifier_tokens': {'input': 0, 'output': 0},
        }

def get_tools_for_categories(categories: list, all_tools: list) -> list:
    """
    Filter the full tool list to only those in the requested categories.
    Always include 'confirm' (for pending Y/N) unless it's clearly unneeded.
    """
    needed_names = set()
    for cat in categories:
        needed_names.update(TOOL_CATEGORIES.get(cat, []))
    # Always include confirm — contractor might say Y to a pending action
    needed_names.update(TOOL_CATEGORIES['confirm'])
    return [t for t in all_tools if t['name'] in needed_names]
