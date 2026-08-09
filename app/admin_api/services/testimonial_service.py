"""Admin CRUD for curated storefront testimonials."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import NotFoundError, ValidationError
from app.admin_api.repositories.audit_repository import AuditRepository
from app.admin_api.schemas.testimonial import (
    TestimonialCreate,
    TestimonialListResponse,
    TestimonialOut,
    TestimonialUpdate,
)
from app.storefront.lib.media import resolve_storage_url

_SELECT = """
    SELECT t.id, t.author_name, t.author_role, t.avatar_r2_key, t.quote, t.rating,
           t.product_id, p.name AS product_name, t.is_featured, t.status,
           t.sort_order, t.created_at, t.updated_at
    FROM commerce.testimonials t
    LEFT JOIN commerce.products p ON p.id = t.product_id AND p.deleted_at IS NULL
"""

# Columns a PATCH is allowed to touch, in the order the UI presents them.
_UPDATABLE = (
    "author_name",
    "author_role",
    "avatar_r2_key",
    "quote",
    "rating",
    "product_id",
    "is_featured",
    "status",
    "sort_order",
)


def _to_out(row) -> TestimonialOut:
    return TestimonialOut(
        id=row["id"],
        author_name=row["author_name"],
        author_role=row["author_role"],
        avatar_r2_key=row["avatar_r2_key"],
        avatar_url=resolve_storage_url(row["avatar_r2_key"]),
        quote=row["quote"],
        rating=row["rating"],
        product_id=row["product_id"],
        product_name=row["product_name"],
        is_featured=row["is_featured"],
        status=row["status"],
        sort_order=row["sort_order"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class TestimonialAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditRepository(session)

    async def list(self, *, include_hidden: bool = True) -> TestimonialListResponse:
        result = await self._session.execute(
            text(
                _SELECT
                + """
                WHERE t.deleted_at IS NULL
                  AND (:include_hidden = TRUE OR t.status = 'published')
                ORDER BY t.is_featured DESC, t.sort_order, t.created_at DESC
                """
            ),
            {"include_hidden": include_hidden},
        )
        items = [_to_out(row) for row in result.mappings().all()]
        return TestimonialListResponse(items=items, total=len(items))

    async def get(self, testimonial_id: uuid.UUID) -> TestimonialOut:
        result = await self._session.execute(
            text(_SELECT + " WHERE t.id = :id AND t.deleted_at IS NULL"),
            {"id": testimonial_id},
        )
        row = result.mappings().first()
        if not row:
            raise NotFoundError("Testimonial not found")
        return _to_out(row)

    async def create(
        self,
        payload: TestimonialCreate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> TestimonialOut:
        await self._assert_product_exists(payload.product_id)
        result = await self._session.execute(
            text(
                """
                INSERT INTO commerce.testimonials (
                  author_name, author_role, avatar_r2_key, quote, rating,
                  product_id, is_featured, status, sort_order
                ) VALUES (
                  :author_name, :author_role, :avatar_r2_key, :quote, :rating,
                  :product_id, :is_featured, :status, :sort_order
                )
                RETURNING id
                """
            ),
            {
                "author_name": payload.author_name.strip(),
                "author_role": (payload.author_role or "").strip() or None,
                "avatar_r2_key": (payload.avatar_r2_key or "").strip() or None,
                "quote": payload.quote.strip(),
                "rating": payload.rating,
                "product_id": payload.product_id,
                "is_featured": payload.is_featured,
                "status": payload.status,
                "sort_order": payload.sort_order,
            },
        )
        testimonial_id = result.scalar_one()
        await self._audit.log(
            admin_id=admin_id,
            entity_type="testimonial",
            entity_id=testimonial_id,
            action="create",
            new_data={"author_name": payload.author_name},
            ip_address=ip_address,
        )
        return await self.get(testimonial_id)

    async def update(
        self,
        testimonial_id: uuid.UUID,
        payload: TestimonialUpdate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> TestimonialOut:
        await self.get(testimonial_id)  # 404s before we build the UPDATE

        data = payload.model_dump(exclude_unset=True)
        if "product_id" in data:
            await self._assert_product_exists(data["product_id"])
        for field in ("author_name", "quote", "author_role", "avatar_r2_key"):
            if isinstance(data.get(field), str):
                data[field] = data[field].strip() or None
        if data.get("author_name") is None and "author_name" in data:
            raise ValidationError("Author name cannot be blank.")
        if data.get("quote") is None and "quote" in data:
            raise ValidationError("Quote cannot be blank.")

        assignments = [f"{col} = :{col}" for col in _UPDATABLE if col in data]
        if assignments:
            params = {col: data[col] for col in _UPDATABLE if col in data}
            params["id"] = testimonial_id
            await self._session.execute(
                text(
                    f"UPDATE commerce.testimonials SET {', '.join(assignments)} "
                    "WHERE id = :id AND deleted_at IS NULL"
                ),
                params,
            )
            await self._audit.log(
                admin_id=admin_id,
                entity_type="testimonial",
                entity_id=testimonial_id,
                action="update",
                new_data={k: str(v) for k, v in params.items() if k != "id"},
                ip_address=ip_address,
            )
        return await self.get(testimonial_id)

    async def delete(
        self,
        testimonial_id: uuid.UUID,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> None:
        await self.get(testimonial_id)
        await self._session.execute(
            text(
                "UPDATE commerce.testimonials SET deleted_at = NOW() "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": testimonial_id},
        )
        await self._audit.log(
            admin_id=admin_id,
            entity_type="testimonial",
            entity_id=testimonial_id,
            action="delete",
            ip_address=ip_address,
        )

    async def _assert_product_exists(self, product_id: uuid.UUID | None) -> None:
        if product_id is None:
            return
        result = await self._session.execute(
            text(
                "SELECT 1 FROM commerce.products WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": product_id},
        )
        if result.first() is None:
            raise ValidationError("Linked product not found.", code="product_not_found")
