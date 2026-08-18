"""Audience segmentation.

A segment is a JSON document stored on the campaign, not a resolved list of
users. That is the important design choice here: a campaign scheduled for Diwali
should reach whoever qualifies *on the day it sends*, not whoever qualified when
an admin drafted it three weeks earlier.

    {"rules": ["purchased_before", "spent_over_5000"], "match": "all"}

Rules compose as intersecting EXISTS/NOT EXISTS subqueries rather than joins, so
adding a rule narrows the audience without multiplying rows — a customer with
four orders must appear once, not four times.

**Every segment is implicitly filtered to consenting, reachable users.** That is
not a rule an admin can turn off: `base_conditions` applies a verified phone, a
non-deleted account and the marketing opt-in to every query this module builds.
A segment that returns someone who opted out is not a bug to be caught later in
the send path — it should be unrepresentable here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SegmentRule:
    """One audience filter.

    `sql` is a fragment evaluated against the aliased `u` (identity.users). It is
    selected from this module's own table by key — an admin supplies the key, never
    the SQL — so nothing user-supplied is ever interpolated into a query.
    """

    key: str
    label: str
    sql: str
    #: Named bind parameters the fragment needs, with their default values.
    params: dict[str, Any] | None = None


#: The rule catalogue. Adding an audience filter means adding a row here.
#:
#: `commerce.orders.status` values that count as a real purchase are listed
#: explicitly rather than "not cancelled": a pending order is not a purchase, and
#: treating it as one would market a discount to someone mid-checkout.
_PAID_STATES = "('confirmed','processing','shipped','delivered','completed')"

RULES: dict[str, SegmentRule] = {
    "purchased_before": SegmentRule(
        key="purchased_before",
        label="Has purchased before",
        sql=f"""
            EXISTS (SELECT 1 FROM commerce.orders o
                     WHERE o.user_id = u.id AND o.status IN {_PAID_STATES})
        """,
    ),
    "never_purchased": SegmentRule(
        key="never_purchased",
        label="Has never purchased",
        sql=f"""
            NOT EXISTS (SELECT 1 FROM commerce.orders o
                         WHERE o.user_id = u.id AND o.status IN {_PAID_STATES})
        """,
    ),
    "cart_abandoned": SegmentRule(
        key="cart_abandoned",
        label="Has an abandoned cart",
        sql="""
            EXISTS (SELECT 1 FROM commerce.carts c
                     WHERE c.user_id = u.id
                       AND c.status IN ('active', 'abandoned')
                       AND c.deleted_at IS NULL
                       AND c.converted_order_id IS NULL
                       AND c.updated_at < now() - interval '1 hour'
                       -- deleted_at matters: emptying a cart soft-deletes its
                       -- items but leaves the cart row 'active', so without this
                       -- an empty cart looks abandoned and earns a reminder
                       -- about nothing.
                       AND EXISTS (SELECT 1 FROM commerce.cart_items ci
                                    WHERE ci.cart_id = c.id
                                      AND ci.deleted_at IS NULL))
        """,
    ),
    "purchased_last_30_days": SegmentRule(
        key="purchased_last_30_days",
        label="Purchased in the last 30 days",
        sql=f"""
            EXISTS (SELECT 1 FROM commerce.orders o
                     WHERE o.user_id = u.id AND o.status IN {_PAID_STATES}
                       AND o.created_at > now() - interval '30 days')
        """,
    ),
    "inactive_90_days": SegmentRule(
        key="inactive_90_days",
        label="Inactive for 90 days",
        # "Inactive" means no order in 90 days, not "never ordered" — a customer
        # who has never bought anything is a different audience with a different
        # message, and `never_purchased` is the rule for them.
        sql=f"""
            EXISTS (SELECT 1 FROM commerce.orders o
                     WHERE o.user_id = u.id AND o.status IN {_PAID_STATES})
            AND NOT EXISTS (SELECT 1 FROM commerce.orders o2
                             WHERE o2.user_id = u.id AND o2.status IN {_PAID_STATES}
                               AND o2.created_at > now() - interval '90 days')
        """,
    ),
    "spent_over_5000": SegmentRule(
        key="spent_over_5000",
        label="Spent more than ₹5,000",
        # Paise, so ₹5,000 is 500000. Comparing against 5000 here would target
        # everyone who ever spent fifty rupees.
        sql=f"""
            (SELECT COALESCE(SUM(o.grand_total_paise), 0) FROM commerce.orders o
              WHERE o.user_id = u.id AND o.status IN {_PAID_STATES}) > 500000  -- ₹5,000
        """,
    ),
    "spent_over_10000": SegmentRule(
        key="spent_over_10000",
        label="Spent more than ₹10,000",
        sql=f"""
            (SELECT COALESCE(SUM(o.grand_total_paise), 0) FROM commerce.orders o
              WHERE o.user_id = u.id AND o.status IN {_PAID_STATES}) > 1000000
        """,
    ),
    "first_time_customer": SegmentRule(
        key="first_time_customer",
        label="First-time customer (exactly one order)",
        sql=f"""
            (SELECT COUNT(*) FROM commerce.orders o
              WHERE o.user_id = u.id AND o.status IN {_PAID_STATES}) = 1
        """,
    ),
    "returning_customer": SegmentRule(
        key="returning_customer",
        label="Returning customer (two or more orders)",
        sql=f"""
            (SELECT COUNT(*) FROM commerce.orders o
              WHERE o.user_id = u.id AND o.status IN {_PAID_STATES}) >= 2
        """,
    ),
    "category_buyer": SegmentRule(
        key="category_buyer",
        label="Bought from a specific category",
        # The only rule taking a parameter. It is a bind param, not interpolation.
        sql=f"""
            EXISTS (
                SELECT 1
                  FROM commerce.orders o
                  JOIN commerce.order_items oi ON oi.order_id = o.id
                  JOIN commerce.products p     ON p.id = oi.product_id
                  -- A product has a primary category AND may be cross-listed in
                  -- commerce.product_categories. Matching only the primary one
                  -- would miss buyers of a product filed under the category as a
                  -- secondary, so both are checked.
                  JOIN commerce.categories cat
                    ON cat.id = p.primary_category_id
                    OR cat.id IN (SELECT pc.category_id
                                    FROM commerce.product_categories pc
                                   WHERE pc.product_id = p.id)
                 WHERE o.user_id = u.id AND o.status IN {_PAID_STATES}
                   AND cat.slug = :category_slug
            )
        """,
        params={"category_slug": ""},
    ),
}


def available_rules() -> list[dict[str, str]]:
    """The catalogue, for the admin UI's segment builder."""
    return [{"key": r.key, "label": r.label} for r in RULES.values()]


@dataclass(slots=True)
class Segment:
    """A parsed, validated audience definition."""

    rules: list[str]
    #: "all" intersects the rules (AND), "any" unions them (OR).
    match: str = "all"
    params: dict[str, Any] | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> "Segment":
        """Validate an admin-supplied filter.

        Unknown rule keys are rejected rather than ignored. Silently dropping one
        would widen the audience — "spent over ₹10,000" quietly becoming
        "everybody" is exactly the failure that sends a VIP discount to the whole
        list.
        """
        raw = raw or {}
        rules = raw.get("rules") or []
        if not isinstance(rules, list):
            raise ValueError("segment 'rules' must be a list")

        unknown = [r for r in rules if r not in RULES]
        if unknown:
            raise ValueError(f"Unknown segment rules: {', '.join(map(str, unknown))}")

        match = str(raw.get("match") or "all").lower()
        if match not in ("all", "any"):
            raise ValueError("segment 'match' must be 'all' or 'any'")

        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("segment 'params' must be an object")

        # A parameterised rule with no value would match nothing and look like a
        # broken campaign rather than a misconfigured one.
        if "category_buyer" in rules and not params.get("category_slug"):
            raise ValueError("category_buyer requires params.category_slug")

        return cls(rules=list(rules), match=match, params=params)


class SegmentationService:
    """Resolves a segment into recipients."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Query construction
    # ------------------------------------------------------------------ #
    def _base_conditions(self, *, consent_column: str) -> str:
        """Conditions applied to every segment, without exception.

        `consent_column` is chosen by the caller from a fixed set — never from a
        request body. See `resolve`.
        """
        return f"""
            u.deleted_at IS NULL
            -- identity.users has no is_active flag; reachability is the `status`
            -- column, and suspended/blocked/pending accounts are not marketable.
            AND u.status = 'active'
            AND u.phone IS NOT NULL
            AND btrim(u.phone) <> ''
            -- Unverified numbers are excluded: sending marketing to a number
            -- nobody proved they own is both a deliverability problem and a
            -- consent problem.
            AND u.phone_verified
            AND EXISTS (
                SELECT 1 FROM public.user_preferences pref
                 WHERE pref.user_id = u.id AND pref.{consent_column}
            )
        """

    def _build(self, segment: Segment, *, consent_column: str) -> tuple[str, dict]:
        """Assemble the WHERE clause and its bind parameters."""
        params: dict[str, Any] = {}
        fragments: list[str] = []

        for key in segment.rules:
            rule = RULES[key]
            fragments.append(f"({rule.sql.strip()})")
            for name, default in (rule.params or {}).items():
                params[name] = (segment.params or {}).get(name, default)

        where = self._base_conditions(consent_column=consent_column)
        if fragments:
            joiner = " AND " if segment.match == "all" else " OR "
            where += f" AND ({joiner.join(fragments)})"
        return where, params

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def count(
        self, segment: Segment, *, consent_column: str = "whatsapp_marketing"
    ) -> int:
        """How many people this segment currently reaches.

        Used by the admin UI before launching a campaign, so "this will message
        4,812 customers" is shown while it can still be cancelled.
        """
        where, params = self._build(segment, consent_column=consent_column)
        sql = f"SELECT COUNT(*) FROM identity.users u WHERE {where}"  # noqa: S608
        return int((await self._session.execute(text(sql), params)).scalar_one())

    async def resolve(
        self,
        segment: Segment,
        *,
        consent_column: str = "whatsapp_marketing",
        limit: int | None = None,
    ) -> list[dict]:
        """Materialise the audience.

        `consent_column` is validated against a fixed allowlist here rather than
        trusted, because it is interpolated into SQL. Everything else in the
        query is either a bind parameter or a fragment from `RULES`.
        """
        if consent_column not in ("whatsapp_marketing", "whatsapp_abandoned_cart"):
            raise ValueError(f"Unsupported consent column: {consent_column}")

        where, params = self._build(segment, consent_column=consent_column)
        # first_name lives on public.user_profiles, not identity.users, and the
        # profile row is optional — LEFT JOIN, with a greeting that still reads
        # naturally when it is missing ("Hi there,").
        sql = f"""
            SELECT u.id AS user_id,
                   u.phone AS recipient,
                   COALESCE(NULLIF(btrim(prof.first_name), ''), 'there') AS customer_name
              FROM identity.users u
              LEFT JOIN public.user_profiles prof ON prof.user_id = u.id
             WHERE {where}
             ORDER BY u.created_at
        """  # noqa: S608
        if limit is not None:
            sql += " LIMIT :segment_limit"
            params["segment_limit"] = int(limit)

        rows = (await self._session.execute(text(sql), params)).mappings().all()
        logger.info(
            "segment_resolved rules=%s match=%s size=%s",
            ",".join(segment.rules) or "(everyone)",
            segment.match,
            len(rows),
        )
        return [dict(r) for r in rows]
