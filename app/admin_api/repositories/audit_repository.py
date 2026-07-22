from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        *,
        admin_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        action: str,
        old_data: dict[str, Any] | None = None,
        new_data: dict[str, Any] | None = None,
        domain: str = "catalog",
        target_user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
    ) -> None:
        await self._session.execute(
            text(
                """
                INSERT INTO admin.audit_logs (
                  admin_id, entity_type, entity_id, action,
                  old_data, new_data, domain, target_user_id, ip_address
                ) VALUES (
                  :admin_id, :entity_type, :entity_id, :action,
                  CAST(:old_data AS jsonb), CAST(:new_data AS jsonb),
                  :domain, :target_user_id, :ip_address
                )
                """
            ),
            {
                "admin_id": admin_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action": action,
                "old_data": json.dumps(old_data) if old_data is not None else None,
                "new_data": json.dumps(new_data) if new_data is not None else None,
                "domain": domain,
                "target_user_id": target_user_id,
                "ip_address": ip_address,
            },
        )
