"""Read-through cache wrapper around CatalogService.

Exposes the same methods the routers call, serving cached JSON on hit and
populating the cache on miss. TTLs are short (lists 60s, details 120s) so even
without an admin bump the catalog is at most ~1-2 minutes stale.
"""

from __future__ import annotations

from app.storefront.lib.catalog_cache import CatalogCache
from app.storefront.services.catalog_service import CatalogService

_LIST_TTL = 60
_DETAIL_TTL = 120


class CachedCatalogService:
    def __init__(self, inner: CatalogService, cache: CatalogCache) -> None:
        self._inner = inner
        self._cache = cache

    async def list_sections(self, *, preview_limit: int = 10):
        return await self._cache.get_or_set(
            f"sections:{preview_limit}", _LIST_TTL,
            lambda: self._inner.list_sections(preview_limit=preview_limit),
        )

    async def get_section(self, slug: str):
        return await self._cache.get_or_set(
            f"section:{slug}", _DETAIL_TTL, lambda: self._inner.get_section(slug)
        )

    async def get_category_with_products(self, slug: str, *, page: int = 1, page_size: int = 24):
        return await self._cache.get_or_set(
            f"category:{slug}:{page}:{page_size}", _DETAIL_TTL,
            lambda: self._inner.get_category_with_products(slug, page=page, page_size=page_size),
        )

    async def get_product(self, slug: str):
        return await self._cache.get_or_set(
            f"product:{slug}", _DETAIL_TTL, lambda: self._inner.get_product(slug)
        )

    async def list_products(self, *, category_slug: str | None = None, page: int = 1, page_size: int = 24):
        return await self._cache.get_or_set(
            f"products:{category_slug}:{page}:{page_size}", _LIST_TTL,
            lambda: self._inner.list_products(
                category_slug=category_slug, page=page, page_size=page_size
            ),
        )
