"""Admin CRUD for the flower types, colours and wraps the builder offers."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.admin_api.core.slug import slugify
from app.admin_api.repositories.audit_repository import AuditRepository
from app.admin_api.schemas.bouquet import (
    BouquetOptionCreate,
    BouquetOptionListResponse,
    BouquetOptionOut,
    BouquetOptionUpdate,
)
from app.storefront.lib.media import resolve_storage_url

_SELECT = """
    SELECT id, kind, name, slug, description, hex_code, image_r2_key,
           price_delta_paise, status, sort_order, created_at, updated_at
    FROM commerce.bouquet_options
"""

_UPDATABLE = (
    "name",
    "description",
    "hex_code",
    "image_r2_key",
    "price_delta_paise",
    "status",
    "sort_order",
)


def _to_out(row) -> BouquetOptionOut:
    return BouquetOptionOut(
        id=row["id"],
        kind=row["kind"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        hex_code=row["hex_code"],
        image_r2_key=row["image_r2_key"],
        image_url=resolve_storage_url(row["image_r2_key"]),
        price_delta_paise=row["price_delta_paise"],
        status=row["status"],
        sort_order=row["sort_order"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class BouquetAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditRepository(session)

    async def list(self, *, kind: str | None = None) -> BouquetOptionListResponse:
        result = await self._session.execute(
            text(
                _SELECT
                + """
                WHERE deleted_at IS NULL
                  -- Cast required: compared only against NULL, Postgres can't
                  -- infer the parameter's type on its own.
                  AND (CAST(:kind AS TEXT) IS NULL OR kind = CAST(:kind AS TEXT))
                ORDER BY kind, sort_order, name
                """
            ),
            {"kind": kind},
        )
        items = [_to_out(row) for row in result.mappings().all()]
        return BouquetOptionListResponse(items=items, total=len(items))

    async def get(self, option_id: uuid.UUID) -> BouquetOptionOut:
        result = await self._session.execute(
            text(_SELECT + " WHERE id = :id AND deleted_at IS NULL"), {"id": option_id}
        )
        row = result.mappings().first()
        if not row:
            raise NotFoundError("Bouquet option not found")
        return _to_out(row)

    async def create(
        self,
        payload: BouquetOptionCreate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> BouquetOptionOut:
        if payload.kind == "color" and not payload.hex_code:
            raise ValidationError(
                "Colours need a swatch — pick a hex code.", code="color_needs_hex"
            )

        slug = slugify(payload.slug or payload.name)
        exists = await self._session.execute(
            text(
                """
                SELECT 1 FROM commerce.bouquet_options
                WHERE kind = :kind AND slug = :slug AND deleted_at IS NULL
                """
            ),
            {"kind": payload.kind, "slug": slug},
        )
        if exists.first():
            raise ConflictError(f"A {payload.kind} called '{payload.name}' already exists.")

        result = await self._session.execute(
            text(
                """
                INSERT INTO commerce.bouquet_options (
                  kind, name, slug, description, hex_code, image_r2_key,
                  price_delta_paise, status, sort_order
                ) VALUES (
                  :kind, :name, :slug, :description, :hex_code, :image_r2_key,
                  :price_delta_paise, :status, :sort_order
                )
                RETURNING id
                """
            ),
            {
                "kind": payload.kind,
                "name": payload.name.strip(),
                "slug": slug,
                "description": (payload.description or "").strip() or None,
                "hex_code": payload.hex_code,
                "image_r2_key": (payload.image_r2_key or "").strip() or None,
                "price_delta_paise": payload.price_delta_paise,
                "status": payload.status,
                "sort_order": payload.sort_order,
            },
        )
        option_id = result.scalar_one()
        await self._audit.log(
            admin_id=admin_id,
            entity_type="bouquet_option",
            entity_id=option_id,
            action="create",
            new_data={"kind": payload.kind, "name": payload.name},
            ip_address=ip_address,
        )
        return await self.get(option_id)

    async def update(
        self,
        option_id: uuid.UUID,
        payload: BouquetOptionUpdate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> BouquetOptionOut:
        current = await self.get(option_id)

        data = payload.model_dump(exclude_unset=True)
        for field in ("name", "description", "image_r2_key"):
            if isinstance(data.get(field), str):
                data[field] = data[field].strip() or None
        if data.get("name") is None and "name" in data:
            raise ValidationError("Name cannot be blank.")
        # Don't let a colour lose the swatch the picker renders.
        if current.kind == "color" and "hex_code" in data and not data["hex_code"]:
            raise ValidationError("Colours need a swatch.", code="color_needs_hex")

        assignments = [f"{col} = :{col}" for col in _UPDATABLE if col in data]
        if assignments:
            params = {col: data[col] for col in _UPDATABLE if col in data}
            params["id"] = option_id
            await self._session.execute(
                text(
                    f"UPDATE commerce.bouquet_options SET {', '.join(assignments)} "
                    "WHERE id = :id AND deleted_at IS NULL"
                ),
                params,
            )
            await self._audit.log(
                admin_id=admin_id,
                entity_type="bouquet_option",
                entity_id=option_id,
                action="update",
                new_data={k: str(v) for k, v in params.items() if k != "id"},
                ip_address=ip_address,
            )
        return await self.get(option_id)

    async def delete(
        self,
        option_id: uuid.UUID,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> None:
        await self.get(option_id)
        # Soft delete: past orders reference these names in their snapshots.
        await self._session.execute(
            text(
                "UPDATE commerce.bouquet_options SET deleted_at = NOW() "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": option_id},
        )
        await self._audit.log(
            admin_id=admin_id,
            entity_type="bouquet_option",
            entity_id=option_id,
            action="delete",
            ip_address=ip_address,
        )
