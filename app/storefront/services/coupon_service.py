"""Coupon validation + discount at checkout.

Covers the coupon edge cases: invalid/expired/inactive, not-yet-started,
below minimum order, total usage limit, per-user usage limit. The discount is
snapshotted onto the order; redemption is recorded atomically at payment capture.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class CouponError(Exception):
    def __init__(self, message: str, *, code: str = "coupon_invalid") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class CouponResult:
    coupon_id: uuid.UUID
    code: str
    discount_paise: int
    free_shipping: bool


class CouponService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def validate(
        self, code: str, *, user_id: uuid.UUID | None, subtotal_paise: int, shipping_paise: int
    ) -> CouponResult:
        # DB trigger stores code_normalized = lower(trim(code)); match that.
        normalized = (code or "").strip().lower()
        if not normalized:
            raise CouponError("Enter a coupon code.")

        row = (
            await self._session.execute(
                text(
                    """
                    SELECT id, code, discount_type, discount_percent, discount_value_paise,
                           max_discount_paise, min_order_amount_paise, usage_limit_total,
                           usage_limit_per_user, status,
                           (starts_at > now()) AS not_started,
                           (expires_at IS NOT NULL AND expires_at < now()) AS expired
                    FROM commerce.coupons
                    WHERE code_normalized = :c AND deleted_at IS NULL
                    """
                ),
                {"c": normalized},
            )
        ).mappings().first()

        if row is None:
            raise CouponError("That coupon code isn’t valid.", code="coupon_not_found")
        if row["status"] != "active" or row["not_started"]:
            raise CouponError("This coupon isn’t active.", code="coupon_inactive")
        if row["expired"]:
            raise CouponError("This coupon has expired.", code="coupon_expired")

        min_amt = row["min_order_amount_paise"] or 0
        if subtotal_paise < min_amt:
            need = (min_amt - subtotal_paise) / 100
            raise CouponError(
                f"Add ₹{need:,.0f} more to use this coupon.", code="coupon_min_order"
            )

        # Total usage limit
        if row["usage_limit_total"] is not None:
            used = (
                await self._session.execute(
                    text("SELECT count(*) FROM commerce.coupon_usages WHERE coupon_id = :id"),
                    {"id": str(row["id"])},
                )
            ).scalar_one()
            if int(used) >= int(row["usage_limit_total"]):
                raise CouponError("This coupon has reached its usage limit.", code="coupon_used_up")

        # Per-user usage limit
        if user_id is not None and row["usage_limit_per_user"]:
            used_by_user = (
                await self._session.execute(
                    text(
                        "SELECT count(*) FROM commerce.coupon_usages WHERE coupon_id = :id AND user_id = :u"
                    ),
                    {"id": str(row["id"]), "u": str(user_id)},
                )
            ).scalar_one()
            if int(used_by_user) >= int(row["usage_limit_per_user"]):
                raise CouponError("You’ve already used this coupon.", code="coupon_already_used")

        discount = 0
        free_shipping = False
        dtype = row["discount_type"]
        if dtype == "percentage":
            pct = int(row["discount_percent"] or 0)
            discount = subtotal_paise * pct // 100
            if row["max_discount_paise"]:
                discount = min(discount, int(row["max_discount_paise"]))
        elif dtype == "fixed_amount":
            discount = int(row["discount_value_paise"] or 0)
        elif dtype == "free_shipping":
            free_shipping = True
            discount = int(shipping_paise)

        discount = max(0, min(discount, subtotal_paise + shipping_paise))
        return CouponResult(
            coupon_id=row["id"], code=row["code"], discount_paise=discount, free_shipping=free_shipping
        )

    async def record_usage(
        self,
        *,
        coupon_id: uuid.UUID,
        user_id: uuid.UUID | None,
        order_id: uuid.UUID,
        discount_paise: int,
    ) -> None:
        """Record a redemption, respecting the total limit atomically (best-effort)."""
        await self._session.execute(
            text(
                """
                INSERT INTO commerce.coupon_usages
                    (coupon_id, user_id, order_id, discount_applied_paise)
                SELECT :cid, :uid, :oid, :disc
                WHERE (
                    SELECT count(*) FROM commerce.coupon_usages WHERE coupon_id = :cid
                ) < COALESCE(
                    (SELECT usage_limit_total FROM commerce.coupons WHERE id = :cid), 2147483647
                )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "cid": str(coupon_id),
                "uid": str(user_id) if user_id else None,
                "oid": str(order_id),
                "disc": int(discount_paise),
            },
        )
