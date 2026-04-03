"""
query_business — natural language analytics over the contractor's DB.
convert_and_calculate — safe Python expression evaluator for job costing math.
"""
import ast, operator, os
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from src.models import Job, JobStatus, Client, Expense, Invoice, InvoiceStatus
from src.memory import _as_utc

# Safe math evaluator
ALLOWED_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos, ast.Mod: operator.mod,
}

def safe_eval(expr: str) -> float:
    """Safely evaluate a math expression string. No builtins, no imports."""
    def _eval(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError(f'Non-numeric constant: {node.value}')
        elif isinstance(node, ast.BinOp):
            op = ALLOWED_OPS.get(type(node.op))
            if not op: raise ValueError(f'Disallowed op: {node.op}')
            return op(_eval(node.left), _eval(node.right))
        elif isinstance(node, ast.UnaryOp):
            op = ALLOWED_OPS.get(type(node.op))
            if not op: raise ValueError(f'Disallowed unary: {node.op}')
            return op(_eval(node.operand))
        raise ValueError(f'Disallowed node type: {type(node).__name__}')
    tree = ast.parse(expr.strip(), mode='eval')
    return _eval(tree.body)


def query_business(question: str, contractor_id: str, db: Session) -> str:
    """
    Answer an open-ended business analytics question by computing against the DB.
    Returns a plain-text answer the agent can use in its response.
    """
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    # Pull all data for this contractor
    jobs = db.query(Job).filter(Job.contractor_id == contractor_id).all()
    expenses = db.query(Expense).filter(Expense.contractor_id == contractor_id).all()
    invoices = db.query(Invoice).filter(Invoice.contractor_id == contractor_id).all()
    clients = db.query(Client).filter(Client.contractor_id == contractor_id).all()

    q = question.lower()
    lines = []

    # ── Profitability by job ──────────────────────────────────────────────
    if any(w in q for w in ['profit', 'margin', 'losing money', 'making money', 'most profitable']):
        job_profits = []
        for j in jobs:
            if j.quoted_amount and j.quoted_amount > 0:
                margin = ((j.quoted_amount - j.actual_cost) / j.quoted_amount) * 100
                job_profits.append((j.title, j.quoted_amount, j.actual_cost, margin, j.client.name if j.client else 'Unknown'))
        if job_profits:
            job_profits.sort(key=lambda x: x[3], reverse=True)
            lines.append(f'PROFITABILITY ANALYSIS ({len(job_profits)} jobs with quotes):')
            for title, quoted, actual, margin, client in job_profits[:10]:
                bar = '█' * max(0, int(margin / 5)) if margin > 0 else '▓▓▓'
                lines.append(f'  {margin:+.0f}% | ${quoted:,.0f} quoted / ${actual:,.0f} cost | {title[:30]} ({client})')
            avg = sum(x[3] for x in job_profits) / len(job_profits)
            lines.append(f'Average margin: {avg:.1f}%')
            over_budget = [x for x in job_profits if x[3] < 0]
            if over_budget:
                lines.append(f'Over budget: {len(over_budget)} job(s)')
        else:
            lines.append('No completed jobs with quotes found.')

    # ── Client analysis ───────────────────────────────────────────────────
    if any(w in q for w in ['client', 'customer', 'best client', 'worst client', 'payment']):
        client_stats = {}
        for inv in invoices:
            job = next((j for j in jobs if j.id == inv.job_id), None)
            if not job or not job.client_id: continue
            cid = job.client_id
            if cid not in client_stats:
                client_stats[cid] = {'name': job.client.name if job.client else 'Unknown',
                                      'total_billed': 0, 'total_paid': 0, 'job_count': 0,
                                      'days_to_pay': [], 'overdue_count': 0}
            s = client_stats[cid]
            s['total_billed'] += inv.amount
            s['job_count'] += 1
            if inv.status == InvoiceStatus.PAID and inv.paid_at and inv.sent_at:
                days = (_as_utc(inv.paid_at) - _as_utc(inv.sent_at)).days
                s['days_to_pay'].append(days)
                s['total_paid'] += inv.amount
            elif inv.status in (InvoiceStatus.SENT, InvoiceStatus.OVERDUE):
                s['overdue_count'] += 1
        if client_stats:
            lines.append(f'CLIENT ANALYSIS ({len(client_stats)} clients with invoices):')
            for cid, s in sorted(client_stats.items(), key=lambda x: x[1]['total_billed'], reverse=True)[:8]:
                avg_days = sum(s['days_to_pay']) / len(s['days_to_pay']) if s['days_to_pay'] else None
                pay_str = f'avg {avg_days:.0f}d to pay' if avg_days else 'no payments recorded'
                overdue_str = f' ⚠ {s["overdue_count"]} overdue' if s['overdue_count'] else ''
                lines.append(f'  {s["name"]}: ${s["total_billed"]:,.0f} billed, {s["job_count"]} jobs, {pay_str}{overdue_str}')

    # ── Expense breakdown ─────────────────────────────────────────────────
    if any(w in q for w in ['expense', 'spend', 'spending', 'cost', 'material', 'where']):
        by_cat = {}
        for e in expenses:
            by_cat[e.category] = by_cat.get(e.category, 0) + e.amount
        if by_cat:
            total = sum(by_cat.values())
            lines.append(f'EXPENSE BREAKDOWN (${total:,.2f} total):')
            for cat, amt in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
                pct = (amt / total * 100) if total else 0
                lines.append(f'  {cat}: ${amt:,.2f} ({pct:.0f}%)')

    # ── Revenue trend ─────────────────────────────────────────────────────
    if any(w in q for w in ['revenue', 'income', 'earn', 'collect', 'month', 'trend', 'quarter']):
        monthly = {}
        for inv in invoices:
            if inv.status == InvoiceStatus.PAID and inv.paid_at:
                key = _as_utc(inv.paid_at).strftime('%Y-%m')
                monthly[key] = monthly.get(key, 0) + inv.amount
        if monthly:
            lines.append('MONTHLY REVENUE (paid invoices):')
            for month in sorted(monthly.keys())[-6:]:
                bar = '█' * int(monthly[month] / 500)
                lines.append(f'  {month}: ${monthly[month]:,.2f} {bar}')

    # ── Capacity & workload ───────────────────────────────────────────────
    if any(w in q for w in ['capacity', 'backlog', 'workload', 'busy', 'schedule', 'take']):
        active = [j for j in jobs if j.status == JobStatus.ACTIVE]
        quoted = [j for j in jobs if j.status == JobStatus.QUOTED]
        leads = [j for j in jobs if j.status == JobStatus.LEAD]
        pipeline_value = sum(j.quoted_amount or 0 for j in active + quoted)
        lines.append(f'WORKLOAD SNAPSHOT:')
        lines.append(f'  Active jobs: {len(active)}')
        lines.append(f'  Quoted (awaiting acceptance): {len(quoted)}')
        lines.append(f'  Leads in pipeline: {len(leads)}')
        lines.append(f'  Pipeline value: ${pipeline_value:,.2f}')

    # ── Outstanding / cash flow ───────────────────────────────────────────
    if any(w in q for w in ['outstanding', 'owe', 'cash', 'unpaid', 'receivable']):
        outstanding = [inv for inv in invoices if inv.status in (InvoiceStatus.SENT, InvoiceStatus.OVERDUE)]
        if outstanding:
            lines.append(f'OUTSTANDING RECEIVABLES ({len(outstanding)} invoices):')
            for inv in sorted(outstanding, key=lambda x: _as_utc(x.sent_at) if x.sent_at else now):
                days = (now - _as_utc(inv.sent_at)).days if inv.sent_at else 0
                job = next((j for j in jobs if j.id == inv.job_id), None)
                client_name = job.client.name if job and job.client else 'Unknown'
                flag = ' ⚠ OVERDUE' if days > 15 else ''
                lines.append(f'  {client_name}: ${inv.amount:,.2f} — {days}d old{flag}')
            lines.append(f'  Total: ${sum(i.amount for i in outstanding):,.2f}')

    if not lines:
        # Generic summary if nothing matched
        total_rev = sum(i.amount for i in invoices if i.status == InvoiceStatus.PAID)
        total_billed = sum(i.amount for i in invoices)
        active_count = sum(1 for j in jobs if j.status == JobStatus.ACTIVE)
        lines = [
            f'Business summary:',
            f'  Total revenue collected: ${total_rev:,.2f}',
            f'  Total billed: ${total_billed:,.2f}',
            f'  Total jobs: {len(jobs)}',
            f'  Active right now: {active_count}',
            f'  Total clients: {len(clients)}',
        ]

    return '\n'.join(lines)
