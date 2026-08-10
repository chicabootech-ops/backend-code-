"""Custom bouquet builder — option catalogue and server-side pricing.

Pricing lives here and nowhere else. The browser sends *what* the customer
chose (option ids and counts); it never sends money. Both the live quote shown
in the builder and the price charged at checkout come from this one method, so
they cannot drift apart or be tampered with from the client.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.lib.media import resolve_storage_url
from app.storefront.schemas.bouquet import (
    MAX_STEM_GROUPS,
    MAX_STEMS,
    MIN_STEMS,
    BouquetConfigIn,
    BouquetOptionOut,
    BouquetOptionsResponse,
    BouquetQuoteOut,
    BouquetStemOut,
)

#: A product flagged `metadata.custom_bouquet = true` supplies the base price
#: and the variant every custom order line hangs off.
BASE_PRODUCT_SQL = text(
    """
    SELECT p.id, p.name, p.slug, p.metadata,
           v.id AS variant_id, v.sku, v.price_paise
    FROM commerce.products p
    JOIN commerce.product_variants v
      ON v.product_id = p.id AND v.deleted_at IS NULL AND v.status = 'active'
    WHERE p.deleted_at IS NULL
      AND p.status = 'active'
      AND COALESCE((p.metadata ->> 'custom_bouquet')::boolean, FALSE) IS TRUE
    ORDER BY v.created_at
    LIMIT 1
    """
)

OPTIONS_SQL = text(
    """
    SELECT id, kind, name, slug, description, hex_code, image_r2_key,
           price_delta_paise
    FROM commerce.bouquet_options
    WHERE status = 'active' AND deleted_at IS NULL
    ORDER BY kind, sort_order, name
    """
)


class BouquetError(Exception):
    def __init__(self, message: str, *, code: str = "bouquet_error", status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def _to_out(row) -> BouquetOptionOut:
    return BouquetOptionOut(
        id=row["id"],
        kind=row["kind"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        hex_code=row["hex_code"],
        image_url=resolve_storage_url(row["image_r2_key"]),
        price_delta_paise=row["price_delta_paise"],
    )


class BouquetService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_options(self) -> BouquetOptionsResponse:
        rows = (await self._session.execute(OPTIONS_SQL)).mappings().all()
        base = (await self._session.execute(BASE_PRODUCT_SQL)).mappings().first()

        by_kind: dict[str, list[BouquetOptionOut]] = {"flower": [], "color": [], "wrap": []}
        for row in rows:
            by_kind.setdefault(row["kind"], []).append(_to_out(row))

        meta = (base["metadata"] if base else None) or {}
        available = bool(base) and bool(by_kind["flower"]) and bool(by_kind["color"])

        return BouquetOptionsResponse(
            flowers=by_kind["flower"],
            colors=by_kind["color"],
            wraps=by_kind["wrap"],
            base_price_paise=int(base["price_paise"]) if base else 0,
            price_per_stem_paise=int(meta.get("price_per_stem_paise") or 0),
            min_stems=MIN_STEMS,
            max_stems=MAX_STEMS,
            max_stem_groups=MAX_STEM_GROUPS,
            available=available,
        )

    async def quote(self, config: BouquetConfigIn) -> BouquetQuoteOut:
        """Price a configuration. Raises BouquetError on anything invalid."""
        base = (await self._session.execute(BASE_PRODUCT_SQL)).mappings().first()
        if base is None:
            raise BouquetError(
                "The bouquet builder isn't set up yet.",
                code="builder_unavailable",
                status_code=503,
            )

        options = await self._load_options(config)
        meta = base["metadata"] or {}
        per_stem = int(meta.get("price_per_stem_paise") or 0)

        stems: list[BouquetStemOut] = []
        total_stems = 0
        stems_price = 0

        for stem in config.stems:
            flower = options[stem.flower_id]
            color = options[stem.color_id]
            unit = per_stem + flower["price_delta_paise"] + color["price_delta_paise"]
            line_total = unit * stem.quantity

            stems.append(
                BouquetStemOut(
                    flower_name=flower["name"],
                    color_name=color["name"],
                    color_hex=color["hex_code"],
                    quantity=stem.quantity,
                    unit_price_paise=unit,
                    line_total_paise=line_total,
                )
            )
            total_stems += stem.quantity
            stems_price += line_total

        if total_stems < MIN_STEMS:
            raise BouquetError(
                f"A bouquet needs at least {MIN_STEMS} stems.", code="too_few_stems"
            )
        if total_stems > MAX_STEMS:
            raise BouquetError(
                f"A bouquet can hold at most {MAX_STEMS} stems.", code="too_many_stems"
            )

        wrap = options.get(config.wrap_id) if config.wrap_id else None
        wrap_price = wrap["price_delta_paise"] if wrap else 0
        base_price = int(base["price_paise"])

        return BouquetQuoteOut(
            stems=stems,
            total_stems=total_stems,
            base_price_paise=base_price,
            stems_price_paise=stems_price,
            wrap_name=wrap["name"] if wrap else None,
            wrap_price_paise=wrap_price,
            total_paise=base_price + stems_price + wrap_price,
            summary=_summarise(stems, wrap["name"] if wrap else None),
        )

    async def _load_options(self, config: BouquetConfigIn) -> dict[uuid.UUID, dict]:
        """Fetch every referenced option and check each is real, live and the right kind."""
        if len(config.stems) > MAX_STEM_GROUPS:
            raise BouquetError("Too many different flowers in one bouquet.", code="too_many_groups")

        # A set of (id, expected_kind) pairs, NOT a dict keyed by id: the same id
        # can legitimately appear in two slots, and keying by id would let the
        # later slot's expectation overwrite the earlier one — which is exactly
        # how a colour id passed as `flower_id` would sneak past the kind check.
        wanted: set[tuple[uuid.UUID, str]] = set()
        for stem in config.stems:
            wanted.add((stem.flower_id, "flower"))
            wanted.add((stem.color_id, "color"))
        if config.wrap_id:
            wanted.add((config.wrap_id, "wrap"))

        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT id, kind, name, hex_code, price_delta_paise
                    FROM commerce.bouquet_options
                    WHERE id = ANY(:ids) AND status = 'active' AND deleted_at IS NULL
                    """
                ),
                {"ids": [option_id for option_id, _ in wanted]},
            )
        ).mappings().all()

        found = {row["id"]: dict(row) for row in rows}
        for option_id, expected_kind in wanted:
            row = found.get(option_id)
            if row is None:
                raise BouquetError(
                    "One of the choices is no longer available.", code="option_unavailable"
                )
            if row["kind"] != expected_kind:
                raise BouquetError("That choice doesn't belong there.", code="option_kind_mismatch")
        return found


def _summarise(stems: list[BouquetStemOut], wrap_name: str | None) -> str:
    """Human-readable line for the order, invoice and admin panel."""
    parts = [f"{s.quantity} × {s.color_name} {s.flower_name}" for s in stems]
    summary = ", ".join(parts)
    return f"{summary} · {wrap_name}" if wrap_name else summary
