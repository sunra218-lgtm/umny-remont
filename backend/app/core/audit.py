import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_plain(v) for v in value]
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def audit(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID | None,
    action: str,
    actor_id: uuid.UUID | None,
    ip: str | None = None,
    old: dict[str, Any] | None = None,
    new: dict[str, Any] | None = None,
    is_system: bool = False,
) -> None:
    """Добавляет запись в журнал в той же транзакции, что и само изменение."""
    session.add(
        AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_id=actor_id,
            ip=ip,
            old_val=_plain(old) if old is not None else None,
            new_val=_plain(new) if new is not None else None,
            is_system=is_system,
        )
    )
