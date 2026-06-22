"""Service discovery and route ownership."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class RouteOwner:
    prefix: str
    service: str
    base_url: str
    description: str


def build_route_map() -> list[RouteOwner]:
    return [
        RouteOwner(
            prefix="/api/user/auth",
            service="userservice",
            base_url=settings.user_service_url,
            description="Authentication (register, login, tokens)",
        ),
        RouteOwner(
            prefix="/api/user/me/avatar",
            service="userservice",
            base_url=settings.user_service_url,
            description="Avatar R2 presigned upload flow",
        ),
        RouteOwner(
            prefix="/api/user/me/addresses",
            service="userservice",
            base_url=settings.user_service_url,
            description="User addresses CRUD",
        ),
        RouteOwner(
            prefix="/api/user/me/preferences",
            service="userservice",
            base_url=settings.user_service_url,
            description="Notification and locale preferences",
        ),
        RouteOwner(
            prefix="/api/user/me/security",
            service="userservice",
            base_url=settings.user_service_url,
            description="Devices, login history, logout-all",
        ),
        RouteOwner(
            prefix="/api/user/me",
            service="userservice",
            base_url=settings.user_service_url,
            description="Profile and onboarding",
        ),
        RouteOwner(
            prefix="/api/user",
            service="userservice",
            base_url=settings.user_service_url,
            description="UserService catch-all",
        ),
        RouteOwner(
            prefix="/admin",
            service="admin",
            base_url=settings.admin_url,
            description="Admin panel API",
        ),
        RouteOwner(
            prefix="/api",
            service="backend",
            base_url=settings.backend_url,
            description="Commerce backend API",
        ),
    ]


def resolve_upstream(path: str) -> RouteOwner:
    for route in build_route_map():
        if path.startswith(route.prefix):
            return route
    return RouteOwner(
        prefix="/",
        service="backend",
        base_url=settings.backend_url,
        description="Default backend fallback",
    )
