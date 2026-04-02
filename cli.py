"""
Local CLI for testing FIELDHAND without Twilio.
Simulates an SMS conversation in your terminal.

Usage: python cli.py
       python cli.py --phone +15551234567  (use existing contractor)
       python cli.py --new                  (create fresh test contractor)
"""
import sys
import os
import argparse
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from src.database import SessionLocal, engine, Base
import src.models  # noqa
from src.models import Contractor
from src.agent import ContractorAgent

Base.metadata.create_all(bind=engine)


def get_or_create_test_contractor(db, phone: str = "+15550000001") -> Contractor:
    contractor = db.query(Contractor).filter(Contractor.phone == phone).first()
    if not contractor:
        contractor = Contractor(
            name="Mike Russo",
            phone=phone,
            trade="electrician",
            business_name="Russo Electric",
            labor_rate=95.0,
            markup_pct=20.0,
            invoice_terms="Net 15",
            onboarding_complete=True,
        )
        db.add(contractor)
        db.commit()
        db.refresh(contractor)
        print(f"\n[Created test contractor: {contractor.name} — {contractor.business_name}]")
    else:
        print(f"\n[Loaded contractor: {contractor.name} — {contractor.business_name}]")
    return contractor


def main():
    parser = argparse.ArgumentParser(description="FIELDHAND CLI test client")
    parser.add_argument("--phone", default="+15550000001", help="Contractor phone number")
    parser.add_argument("--new", action="store_true", help="Delete and recreate test contractor")
    args = parser.parse_args()

    db = SessionLocal()

    if args.new:
        existing = db.query(Contractor).filter(Contractor.phone == args.phone).first()
        if existing:
            db.delete(existing)
            db.commit()
            print("[Cleared existing contractor]")

    contractor = get_or_create_test_contractor(db, args.phone)
    agent = ContractorAgent(db=db, contractor_id=contractor.id)

    print("\n" + "="*60)
    print("  FIELDHAND — Contractor AI Assistant")
    print("  Type your messages. Ctrl+C or 'quit' to exit.")
    print("="*60 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "bye"):
            print("FIELDHAND: Talk later.")
            break

        try:
            response = agent.chat(user_input, channel="cli")
            print(f"\nFIELDHAND: {response}\n")
        except Exception as e:
            print(f"\n[Error: {e}]\n")
            import traceback
            traceback.print_exc()

    db.close()


if __name__ == "__main__":
    main()
