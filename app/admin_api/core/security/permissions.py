"""Role-based authorisation for the admin API.

The admin JWT has always carried a role, but every endpoint accepted any
authenticated admin — so a support agent could refund orders or delete the
catalog. This maps each role to the areas it may write to, and exposes a
dependency factory routers use to declare what they need.

Reads stay open to every signed-in admin: the panel is useless if support can't
look at an order. Only mutations are gated.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from fastapi import Depends

from app.admin_api.core.exceptions import ForbiddenError
from app.admin_api.core.security.jwt import AdminTokenPayload
from app.admin_api.dependencies import CurrentAdmin


class Permission(StrEnum):
    CATALOG_WRITE = "catalog:write"
    ORDER_WRITE = "order:write"
    #: Moves real money — deliberately the narrowest grant.
    REFUND_WRITE = "refund:write"
    USER_WRITE = "user:write"
    COUPON_WRITE = "coupon:write"
    INVENTORY_WRITE = "inventory:write"
    MAINTENANCE_RUN = "maintenance:run"


SUPER_ADMIN = "super_admin"

#: Role -> what it may change. Unlisted roles get read-only access.
ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    SUPER_ADMIN: frozenset(Permission),
    "catalog_manager": frozenset(
        {Permission.CATALOG_WRITE, Permission.INVENTORY_WRITE, Permission.COUPON_WRITE}
    ),
    "order_manager": frozenset(
        {Permission.ORDER_WRITE, Permission.INVENTORY_WRITE, Permission.REFUND_WRITE}
    ),
    "support_agent": frozenset({Permission.ORDER_WRITE}),
}


def permissions_for(role: str) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def require(*required: Permission):
    """Dependency factory: `_: Annotated[..., Depends(require(Permission.X))]`.

    An unknown role resolves to no permissions, so adding a role to the database
    without adding it here fails closed rather than granting everything.
    """

    async def _check(admin: CurrentAdmin) -> AdminTokenPayload:
        granted = permissions_for(admin.role)
        missing = [p for p in required if p not in granted]
        if missing:
            raise ForbiddenError(
                f"Your role ({admin.role}) cannot perform this action.",
                code="insufficient_permissions",
            )
        return admin

    return _check


#: Pre-built dependencies for the common cases, so routers read cleanly.
CatalogWriter = Annotated[AdminTokenPayload, Depends(require(Permission.CATALOG_WRITE))]
OrderWriter = Annotated[AdminTokenPayload, Depends(require(Permission.ORDER_WRITE))]
RefundWriter = Annotated[AdminTokenPayload, Depends(require(Permission.REFUND_WRITE))]
UserWriter = Annotated[AdminTokenPayload, Depends(require(Permission.USER_WRITE))]
CouponWriter = Annotated[AdminTokenPayload, Depends(require(Permission.COUPON_WRITE))]
InventoryWriter = Annotated[AdminTokenPayload, Depends(require(Permission.INVENTORY_WRITE))]
MaintenanceRunner = Annotated[AdminTokenPayload, Depends(require(Permission.MAINTENANCE_RUN))]
