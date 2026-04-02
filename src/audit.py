"""Helper to write audit log entries from anywhere in the app."""
from sqlalchemy.orm import Session
from src.models.audit_log import AuditLog


def log(
    db: Session,
    contractor_id: str,
    action: str,
    subject: str = None,
    detail: str = None,
    channel: str = "system",
    initiated_by: str = "agent",
):
    entry = AuditLog(
        contractor_id=contractor_id,
        action=action,
        subject=subject,
        detail=detail,
        channel=channel,
        initiated_by=initiated_by,
    )
    db.add(entry)
    db.commit()
    return entry
