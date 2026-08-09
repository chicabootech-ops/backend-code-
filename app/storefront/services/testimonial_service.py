"""Published customer testimonials for the storefront."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.lib.media import resolve_storage_url
from app.storefront.schemas.testimonial import (
    StorefrontTestimonialListResponse,
    StorefrontTestimonialOut,
)

_LIST_SQL = text(
    """
    SELECT t.id,
           t.author_name,
           t.author_role,
           t.avatar_r2_key,
           t.quote,
           t.rating,
           t.is_featured,
           p.slug AS product_slug,
           p.name AS product_name
    FROM commerce.testimonials t
    LEFT JOIN commerce.products p
           ON p.id = t.product_id AND p.deleted_at IS NULL
    WHERE t.status = 'published'
      AND t.deleted_at IS NULL
      AND (:featured_only = FALSE OR t.is_featured = TRUE)
    ORDER BY t.is_featured DESC, t.sort_order, t.created_at DESC
    LIMIT :limit
    """
)


class TestimonialService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_published(
        self, *, limit: int = 24, featured_only: bool = False
    ) -> StorefrontTestimonialListResponse:
        result = await self._session.execute(
            _LIST_SQL, {"limit": limit, "featured_only": featured_only}
        )
        rows = result.mappings().all()

        items = [
            StorefrontTestimonialOut(
                id=row["id"],
                author_name=row["author_name"],
                author_role=row["author_role"],
                avatar_url=resolve_storage_url(row["avatar_r2_key"]),
                quote=row["quote"],
                rating=row["rating"],
                is_featured=row["is_featured"],
                product_slug=row["product_slug"],
                product_name=row["product_name"],
            )
            for row in rows
        ]
        ratings = [item.rating for item in items if item.rating]
        return StorefrontTestimonialListResponse(
            items=items,
            total=len(items),
            average_rating=round(sum(ratings) / len(ratings), 2) if ratings else None,
        )
