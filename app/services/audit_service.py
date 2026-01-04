from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def log_audit(
    db: Session,
    user_id: int,
    action: str,
    entity: str,
    entity_id: Optional[int] = None,
    detail: Optional[str] = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            detail=detail,
        )
    )
