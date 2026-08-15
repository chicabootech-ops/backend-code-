"""Newsletter subscriber administration.

Read-mostly on purpose. An admin can see the list, search it, and remove an
address on request — but cannot add one. A subscriber has to opt in themselves,
because a list you can type addresses into is a list that stops being consented,
and consent is the only thing that makes a marketing send lawful.
"""

from __future__ import annotations

import csv
import io
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import NotFoundError
from app.admin_api.repositories.audit_repository import AuditRepository
from app.admin_api.schemas.subscriber import (
    SubscriberListResponse,
    SubscriberOut,
    SubscriberStats,
)

#: Only these can be sorted on, so the column name can be interpolated safely.
_SORTABLE = {"created_at", "confirmed_at", "email", "status"}


class SubscriberAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditRepository(session)

    async def stats(self) -> SubscriberStats:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      count(*)                                        AS total,
                      count(*) FILTER (WHERE status = 'confirmed')    AS confirmed,
                      count(*) FILTER (WHERE status = 'pending')      AS pending,
                      count(*) FILTER (WHERE status = 'unsubscribed') AS unsubscribed,
                      count(*) FILTER (
                        WHERE status = 'confirmed'
                          AND confirmed_at > now() - interval '30 days'
                      )                                               AS new_30d
                    FROM commerce.newsletter_subscribers
                    """
                )
            )
        ).mappings().one()
        return SubscriberStats(**dict(row))

    async def list_subscribers(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
        search: str | None = None,
        sort: str = "created_at",
        direction: str = "desc",
    ) -> SubscriberListResponse:
        where = ["TRUE"]
        params: dict[str, object] = {}
        if status:
            where.append("status = :status")
            params["status"] = status
        if search:
            where.append("email ILIKE :search")
            params["search"] = f"%{search.strip()}%"
        clause = " AND ".join(where)

        column = sort if sort in _SORTABLE else "created_at"
        order = "ASC" if direction.lower() == "asc" else "DESC"

        total = (
            await self._session.execute(
                text(
                    f"SELECT count(*) FROM commerce.newsletter_subscribers WHERE {clause}"
                ),
                params,
            )
        ).scalar_one()

        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT id, email, status, confirmed_at, unsubscribed_at, created_at
                    FROM commerce.newsletter_subscribers
                    WHERE {clause}
                    ORDER BY {column} {order} NULLS LAST
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        ).mappings().all()

        return SubscriberListResponse(
            items=[SubscriberOut(**dict(r)) for r in rows],
            meta={
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
        )

    async def export_csv(self, *, status: str | None = "confirmed") -> str:
        """Confirmed addresses only by default.

        Exporting pending or unsubscribed rows into a file that is going to be
        pasted into a mail tool is how people who never opted in — and people who
        explicitly opted out — end up receiving marketing.
        """
        clause = "status = :status" if status else "TRUE"
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT email, status, confirmed_at, created_at
                    FROM commerce.newsletter_subscribers
                    WHERE {clause}
                    ORDER BY created_at DESC
                    """
                ),
                {"status": status} if status else {},
            )
        ).mappings().all()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["email", "status", "confirmed_at", "created_at"])
        for row in rows:
            writer.writerow(
                [
                    row["email"],
                    row["status"],
                    row["confirmed_at"].isoformat() if row["confirmed_at"] else "",
                    row["created_at"].isoformat() if row["created_at"] else "",
                ]
            )
        return buffer.getvalue()

    async def unsubscribe(
        self, subscriber_id: uuid.UUID, *, admin_id: uuid.UUID, ip_address: str | None = None
    ) -> SubscriberOut:
        """Opt someone out on their behalf — for requests that arrive by email.

        This never deletes the row. A deleted address can resubscribe by
        accident and silently start receiving mail again; an `unsubscribed` row
        is a durable record that they asked not to be contacted.
        """
        row = (
            await self._session.execute(
                text(
                    """
                    UPDATE commerce.newsletter_subscribers
                       SET status = 'unsubscribed',
                           unsubscribed_at = COALESCE(unsubscribed_at, now()),
                           updated_at = now()
                     WHERE id = :id
                 RETURNING id, email, status, confirmed_at, unsubscribed_at, created_at
                    """
                ),
                {"id": str(subscriber_id)},
            )
        ).mappings().first()
        if not row:
            raise NotFoundError("Subscriber not found")

        await self._audit.log(
            admin_id=admin_id,
            entity_type="newsletter_subscriber",
            entity_id=subscriber_id,
            action="unsubscribe",
            old_data=None,
            new_data={"status": "unsubscribed"},
            domain="marketing",
            ip_address=ip_address,
        )
        return SubscriberOut(**dict(row))
