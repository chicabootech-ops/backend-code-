"""Admin coupon CRUD."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class CouponCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    description: str | None = None
    discount_type: str = Field(pattern="^(percentage|fixed_amount|free_shipping)$")
    discount_percent: int | None = Field(default=None, ge=1, le=100)
    discount_value_paise: int | None = Field(default=None, gt=0)
    max_discount_paise: int | None = Field(default=None, gt=0)
    min_order_amount_paise: int | None = Field(default=None, ge=0)
    usage_limit_total: int | None = Field(default=None, gt=0)
    usage_limit_per_user: int = Field(default=1, gt=0)
    status: str = Field(default="active", pattern="^(active|inactive)$")
    expires_at: datetime | None = None

    @field_validator("code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class CouponUpdate(BaseModel):
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(active|inactive|expired)$")
    usage_limit_total: int | None = Field(default=None, gt=0)
    expires_at: datetime | None = None


class CouponOut(BaseModel):
    id: uuid.UUID
    code: str
    description: str | None
    discount_type: str
    discount_percent: int | None
    discount_value_paise: int | None
    min_order_amount_paise: int | None
    usage_limit_total: int | None
    usage_limit_per_user: int
    status: str
    starts_at: datetime
    expires_at: datetime | None
    usage_count: int = 0


class CouponListOut(BaseModel):
    items: list[CouponOut]
    total: int


class CouponAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_coupons(self, *, page: int = 1, page_size: int = 50) -> CouponListOut:
        total = (
            await self._session.execute(
                text("SELECT count(*) FROM commerce.coupons WHERE deleted_at IS NULL")
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT c.*,
                           (SELECT count(*) FROM commerce.coupon_usages u WHERE u.coupon_id = c.id) AS usage_count
                    FROM commerce.coupons c
                    WHERE c.deleted_at IS NULL
                    ORDER BY c.created_at DESC
                    OFFSET :off LIMIT :lim
                    """
                ),
                {"off": (page - 1) * page_size, "lim": page_size},
            )
        ).mappings().all()
        return CouponListOut(
            items=[self._map(r) for r in rows],
            total=int(total),
        )

    async def create(self, payload: CouponCreate) -> CouponOut:
        row = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO commerce.coupons (
                      code, code_normalized, description, discount_type,
                      discount_percent, discount_value_paise, max_discount_paise,
                      min_order_amount_paise, usage_limit_total, usage_limit_per_user,
                      status, expires_at
                    ) VALUES (
                      :code, :norm, :desc, :dtype,
                      :dpercent, :dvalue, :maxd,
                      :min_order, :limit_total, :limit_user,
                      :status, :expires
                    )
                    RETURNING *
                    """
                ),
                {
                    "code": payload.code,
                    "norm": payload.code,
                    "desc": payload.description,
                    "dtype": payload.discount_type,
                    "dpercent": payload.discount_percent,
                    "dvalue": payload.discount_value_paise,
                    "maxd": payload.max_discount_paise,
                    "min_order": payload.min_order_amount_paise,
                    "limit_total": payload.usage_limit_total,
                    "limit_user": payload.usage_limit_per_user,
                    "status": payload.status,
                    "expires": payload.expires_at,
                },
            )
        ).mappings().one()
        await self._session.flush()
        data = dict(row)
        data["usage_count"] = 0
        return self._map(data)

    async def update(self, coupon_id: uuid.UUID, payload: CouponUpdate) -> CouponOut:
        fields: dict[str, Any] = {}
        if payload.description is not None:
            fields["description"] = payload.description
        if payload.status is not None:
            fields["status"] = payload.status
        if payload.usage_limit_total is not None:
            fields["usage_limit_total"] = payload.usage_limit_total
        if payload.expires_at is not None:
            fields["expires_at"] = payload.expires_at
        if not fields:
            return await self.get(coupon_id)
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        fields["id"] = str(coupon_id)
        row = (
            await self._session.execute(
                text(
                    f"""
                    UPDATE commerce.coupons SET {sets}, updated_at = now()
                    WHERE id = :id AND deleted_at IS NULL
                    RETURNING *
                    """
                ),
                fields,
            )
        ).mappings().first()
        if not row:
            raise ValueError("Coupon not found")
        await self._session.flush()
        return await self.get(coupon_id)

    async def get(self, coupon_id: uuid.UUID) -> CouponOut:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT c.*,
                           (SELECT count(*) FROM commerce.coupon_usages u WHERE u.coupon_id = c.id) AS usage_count
                    FROM commerce.coupons c
                    WHERE c.id = :id AND c.deleted_at IS NULL
                    """
                ),
                {"id": str(coupon_id)},
            )
        ).mappings().first()
        if not row:
            raise ValueError("Coupon not found")
        return self._map(row)

    def _map(self, row: Any) -> CouponOut:
        return CouponOut(
            id=row["id"],
            code=row["code"],
            description=row.get("description"),
            discount_type=row["discount_type"],
            discount_percent=row.get("discount_percent"),
            discount_value_paise=row.get("discount_value_paise"),
            min_order_amount_paise=row.get("min_order_amount_paise"),
            usage_limit_total=row.get("usage_limit_total"),
            usage_limit_per_user=row["usage_limit_per_user"],
            status=row["status"],
            starts_at=row["starts_at"],
            expires_at=row.get("expires_at"),
            usage_count=int(row.get("usage_count") or 0),
        )
