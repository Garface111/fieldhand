"""Generate preview PDFs from current templates to see what they look like."""
import sys
sys.path.insert(0, '.')
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from pathlib import Path
from datetime import datetime, timedelta

TEMPLATE_DIR = Path('templates')
OUT = Path('generated_docs')
OUT.mkdir(exist_ok=True)
env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

line_items = [
    {'description': 'Square D QO 200A Main Breaker Panel (QO140L200PG)', 'qty': 1, 'unit_price': 210.00, 'amount': 210.00},
    {'description': '20A Single Pole Breakers (QO120)', 'qty': 15, 'unit_price': 9.25, 'amount': 138.75},
    {'description': 'AFCI Breakers (QO120CAFIC)', 'qty': 6, 'unit_price': 42.00, 'amount': 252.00},
    {'description': 'Whole-Home Surge Protector (QOVSB2)', 'qty': 1, 'unit_price': 195.00, 'amount': 195.00},
    {'description': '12/2 Romex NM-B Wire (300ft)', 'qty': 300, 'unit_price': 1.15, 'amount': 345.00},
    {'description': 'Aluminum Pigtailing — labor & materials', 'qty': 1, 'unit_price': 480.00, 'amount': 480.00},
    {'description': 'Labor — Panel installation (8 hrs @ $110/hr)', 'qty': 8, 'unit_price': 110.00, 'amount': 880.00},
    {'description': 'Permit — City of Manchester Electrical', 'qty': 1, 'unit_price': 125.00, 'amount': 125.00},
    {'description': 'Materials markup (25%)', 'qty': 1, 'unit_price': 390.19, 'amount': 390.19},
]
subtotal = sum(i['amount'] for i in line_items)
today = datetime.now()
valid_until = today + timedelta(days=30)

t = env.get_template('quote.html')
html = t.render(
    business_name='Dyson Swarm Technologies',
    trade='Technology Contractor',
    contractor_phone='+1 (603) 667-3203',
    contractor_email='ford.genereaux@dysonswarmtechnologies.com',
    license_no='EL-2024-00847',
    client_name='David Smith',
    client_address='412 Maple St, Manchester, NH 03101',
    client_phone='603-555-0177',
    client_email='dave.smith@email.com',
    date=today.strftime('%B %d, %Y'),
    valid_until=valid_until.strftime('%B %d, %Y'),
    estimate_number='EST-20260403-PREV01',
    description='Remove and replace existing 100A Federal Pacific panel with new 200A Square D QO panel.',
    line_items=line_items,
    subtotal=subtotal,
    tax_rate=None, tax_amount=0, total=subtotal,
    payment_terms='Net 15',
    deposit_required=False, deposit_pct=0, deposit_amount=0,
    notes='All work to NEC 2020 code. 1-year labor warranty. Permit pulled and included.',
)
HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(OUT / 'preview_quote.pdf'))
print('Quote:', OUT / 'preview_quote.pdf')
