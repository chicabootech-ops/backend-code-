"""Event publishing helper for admin catalog writes.

Admin routers already bump the catalog cache directly; publishing alongside it
lets any other consumer (search reindex, cache warmers, audit tooling) react to
the same write without the routers knowing who is listening.
"""

from __future__ import annotations

import uuid

from app.events.bus import get_event_bus
from app.events.types import EventType


async def catalog_changed(
    entity: str,
    action: str,
    entity_id: uuid.UUID | str | None = None,
) -> None:
    await get_event_bus().publish(
        EventType.CATALOG_CHANGED,
        {
            "entity": entity,
            "action": action,
            "entity_id": str(entity_id) if entity_id else None,
            "reason": f"{entity}.{action}",
        },
    )
