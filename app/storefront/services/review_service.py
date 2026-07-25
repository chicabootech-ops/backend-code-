"""Product review queries and submission."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ReviewError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code


class ReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_approved(self, slug: str) -> list[dict]:
        result = await self._session.execute(
            text(
                """
                SELECT r.id, r.rating, r.title, r.body, r.is_verified_purchase,
                       r.helpful_count, r.created_at,
                       COALESCE(NULLIF(TRIM(CONCAT(up.first_name, ' ', up.last_name)), ''), 'Customer')
                           AS author_name
                FROM commerce.reviews r
                JOIN commerce.products p ON p.id = r.product_id
                LEFT JOIN public.user_profiles up ON up.user_id = r.user_id
                WHERE p.slug = :slug
                  AND r.status = 'approved'
                  AND r.deleted_at IS NULL
                  AND p.deleted_at IS NULL
                ORDER BY r.created_at DESC
                """
            ),
            {"slug": slug},
        )
        return [dict(row) for row in result.mappings().all()]

    async def submit(
        self,
        slug: str,
        user_id: uuid.UUID,
        *,
        rating: int,
        title: str | None,
        body: str | None,
    ) -> dict:
        product = (
            await self._session.execute(
                text(
                    """
                    SELECT id FROM commerce.products
                    WHERE slug = :slug AND status = 'active' AND deleted_at IS NULL
                    """
                ),
                {"slug": slug},
            )
        ).mappings().one_or_none()
        if not product:
            raise ReviewError("Product not found", status_code=404)

        existing = (
            await self._session.execute(
                text(
                    """
                    SELECT id FROM commerce.reviews
                    WHERE user_id = :user_id AND product_id = :product_id
                      AND order_id IS NULL AND deleted_at IS NULL
                    """
                ),
                {"user_id": str(user_id), "product_id": str(product["id"])},
            )
        ).first()
        if existing:
            raise ReviewError("You have already reviewed this product", status_code=409)

        result = await self._session.execute(
            text(
                """
                INSERT INTO commerce.reviews (product_id, user_id, rating, title, body)
                VALUES (:product_id, :user_id, :rating, :title, :body)
                RETURNING id, rating, title, body, status, created_at
                """
            ),
            {
                "product_id": str(product["id"]),
                "user_id": str(user_id),
                "rating": rating,
                "title": title,
                "body": body,
            },
        )
        return dict(result.mappings().one())
