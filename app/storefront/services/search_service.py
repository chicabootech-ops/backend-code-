from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.lib.media import product_image_url
from app.storefront.schemas.product import StorefrontProductOut


class SearchSuggestion(BaseModel):
    name: str
    slug: str


class SearchResponse(BaseModel):
    query: str
    items: list[StorefrontProductOut] = Field(default_factory=list)
    suggestions: list[SearchSuggestion] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 24


class SearchService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        q: str,
        *,
        page: int = 1,
        page_size: int = 24,
        min_price_paise: int | None = None,
        max_price_paise: int | None = None,
        sort: str = "relevance",
    ) -> SearchResponse:
        query = (q or "").strip()
        if not query:
            return SearchResponse(query=query, page=page, page_size=page_size)

        offset = (page - 1) * page_size
        price_join = """
            LEFT JOIN LATERAL (
              SELECT v.price_paise, v.compare_at_price_paise
              FROM commerce.product_variants v
              WHERE v.product_id = p.id AND v.deleted_at IS NULL AND v.status = 'active'
              ORDER BY v.created_at ASC
              LIMIT 1
            ) pv ON true
        """
        filters = [
            "p.deleted_at IS NULL",
            "p.status = 'active'",
            """(
              COALESCE(p.search_vector, ''::tsvector) @@ plainto_tsquery('english', :q)
              OR p.name ILIKE :like
              OR similarity(p.name, :q) > 0.15
            )""",
        ]
        params: dict = {
            "q": query,
            "like": f"%{query}%",
            "lim": page_size,
            "off": offset,
        }
        if min_price_paise is not None:
            filters.append("COALESCE(pv.price_paise, 0) >= :min_p")
            params["min_p"] = min_price_paise
        if max_price_paise is not None:
            filters.append("COALESCE(pv.price_paise, 0) <= :max_p")
            params["max_p"] = max_price_paise

        order = {
            "price_asc": "COALESCE(pv.price_paise, 0) ASC, p.name ASC",
            "price_desc": "COALESCE(pv.price_paise, 0) DESC, p.name ASC",
            "newest": "p.created_at DESC",
            "name": "p.name ASC",
        }.get(sort, "rank DESC NULLS LAST, sim DESC, p.name ASC")

        where_sql = " AND ".join(filters)
        count_row = (
            await self._session.execute(
                text(
                    f"""
                    SELECT count(*) FROM commerce.products p
                    {price_join}
                    WHERE {where_sql}
                    """
                ),
                params,
            )
        ).scalar_one()

        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT p.id, p.name, p.slug, p.short_description, p.description,
                           p.metadata, p.primary_category_id,
                           c.slug AS category_slug, c.name AS category_name,
                           COALESCE(pv.price_paise, 0) AS price_paise,
                           pv.compare_at_price_paise,
                           ts_rank(
                             COALESCE(p.search_vector, ''::tsvector),
                             plainto_tsquery('english', :q)
                           ) AS rank,
                           similarity(p.name, :q) AS sim
                    FROM commerce.products p
                    LEFT JOIN commerce.categories c ON c.id = p.primary_category_id
                    {price_join}
                    WHERE {where_sql}
                    ORDER BY {order}
                    OFFSET :off LIMIT :lim
                    """
                ),
                params,
            )
        ).mappings().all()

        items = [
            StorefrontProductOut(
                id=row["id"],
                name=row["name"],
                slug=row["slug"],
                short_description=row["short_description"],
                description=row["description"],
                image_url=product_image_url(row["metadata"] or {}),
                price_paise=int(row["price_paise"] or 0),
                compare_at_price_paise=row["compare_at_price_paise"],
                primary_category_id=row["primary_category_id"],
                category_slug=row["category_slug"],
                category_name=row["category_name"],
            )
            for row in rows
        ]

        suggestions: list[SearchSuggestion] = []
        if not items:
            sug_rows = (
                await self._session.execute(
                    text(
                        """
                        SELECT name, slug FROM commerce.products
                        WHERE deleted_at IS NULL AND status = 'active'
                        ORDER BY similarity(name, :q) DESC
                        LIMIT 6
                        """
                    ),
                    {"q": query},
                )
            ).mappings().all()
            suggestions = [SearchSuggestion(name=r["name"], slug=r["slug"]) for r in sug_rows]

        return SearchResponse(
            query=query,
            items=items,
            suggestions=suggestions,
            total=int(count_row),
            page=page,
            page_size=page_size,
        )
