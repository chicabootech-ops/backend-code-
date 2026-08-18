"""Admin APIs for the messaging platform — `/api/v1/admin/whatsapp/*`.

Templates, message logs, search, resend, analytics and consent. Reads are open to
any signed-in admin (the panel is useless if support cannot look at a delivery
log); anything that sends a message or changes template config requires
`Permission.MARKETING_SEND`.

One deliberate omission: there is no endpoint that returns an OTP code, and no
log view that could reveal one. `ops.notifications.variables` holds the code for
OTP notifications, so `_redact` strips it from every response in this file. An
admin able to read live OTP codes could take over any customer account, and "we
trust our admins" is not an access control.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.admin_api.core.security.permissions import Permission, require
from app.admin_api.dependencies import CurrentAdmin
from app.config import settings
from app.notifications.analytics_service import AnalyticsService
from app.notifications.service import NotificationService
from app.storefront.dependencies import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/whatsapp", tags=["admin-whatsapp"])

RequireMarketing = Depends(require(Permission.MARKETING_SEND))

#: Variable names never returned by any endpoint here.
_SECRET_VARIABLES = frozenset({"otp", "code", "password", "token"})


def _redact(variables: Any) -> dict[str, Any]:
    """Strip secrets from a notification's template variables.

    Applied to every log response. An OTP lives in `variables` for the lifetime
    of the notification row, and a delivery log that echoed it back would hand
    account takeover to anyone with admin read access.
    """
    if isinstance(variables, str):
        import json

        try:
            variables = json.loads(variables or "{}")
        except ValueError:
            return {}
    if not isinstance(variables, dict):
        return {}
    return {
        key: ("***" if key.lower() in _SECRET_VARIABLES else value)
        for key, value in variables.items()
    }


def get_notifications(request: Request, db: DbSession) -> NotificationService:
    build = getattr(request.app.state, "build_notifications", None)
    if build is None:  # pragma: no cover - misconfiguration
        raise HTTPException(status_code=503, detail="Messaging is not configured")
    return build(db)


NotificationsDep = Annotated[NotificationService, Depends(get_notifications)]


# ====================================================================== #
# Templates
# ====================================================================== #
class TemplateUpdate(BaseModel):
    provider_template_name: str | None = None
    language: str | None = None
    category: str | None = Field(default=None, pattern="^(authentication|utility|marketing)$")
    variable_order: list[str] | None = None
    is_active: bool | None = None


@router.get("/templates")
async def list_templates(admin: CurrentAdmin, db: DbSession) -> dict:
    """Every template mapping, including inactive ones."""
    rows = (
        await db.execute(
            text(
                """
                SELECT id, notification_type, channel, provider,
                       provider_template_name, provider_template_id, language,
                       category, variable_order, is_active, updated_at
                FROM ops.notification_templates
                ORDER BY channel, notification_type, language
                """
            )
        )
    ).mappings().all()
    return {"templates": [dict(r) for r in rows]}


@router.patch("/templates/{template_id}", dependencies=[RequireMarketing])
async def update_template(
    template_id: uuid.UUID,
    payload: TemplateUpdate,
    admin: CurrentAdmin,
    db: DbSession,
) -> dict:
    """Update a template mapping.

    Template names are data rather than code precisely so a Meta-approved
    template can be swapped without a deploy. Deactivating a row is the
    supported way to stop sending one type while leaving the rest alone.
    """
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    import json

    updated = (
        await db.execute(
            text(
                """
                UPDATE ops.notification_templates
                SET provider_template_name =
                        COALESCE(:name, provider_template_name),
                    language = COALESCE(:language, language),
                    category = COALESCE(:category, category),
                    variable_order = COALESCE(
                        CAST(:variable_order AS JSONB), variable_order),
                    is_active = COALESCE(:is_active, is_active)
                WHERE id = :id
                RETURNING id, notification_type, provider_template_name, is_active
                """
            ),
            {
                "id": str(template_id),
                "name": fields.get("provider_template_name"),
                "language": fields.get("language"),
                "category": fields.get("category"),
                "variable_order": (
                    json.dumps(fields["variable_order"])
                    if "variable_order" in fields
                    else None
                ),
                "is_active": fields.get("is_active"),
            },
        )
    ).mappings().first()
    await db.commit()

    if updated is None:
        raise HTTPException(status_code=404, detail="Template not found")

    logger.info("template_updated id=%s admin=%s", template_id, admin.id)
    return dict(updated)


# ====================================================================== #
# Message log
# ====================================================================== #
@router.get("/messages")
async def list_messages(
    admin: CurrentAdmin,
    db: DbSession,
    status: str | None = Query(default=None),
    notification_type: str | None = Query(default=None),
    recipient: str | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Searchable message log.

    Every filter is a bind parameter wrapped in CAST — a param compared only
    against NULL has no inferable type and makes asyncpg raise
    `AmbiguousParameterError`.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT n.id, n.user_id, n.notification_type, n.category,
                       n.recipient, n.status, n.channel_preference,
                       n.attempt_count, n.last_error, n.variables,
                       n.campaign_id, n.reference_type, n.reference_id,
                       n.created_at, n.delivered_at, n.failed_at
                FROM ops.notifications n
                WHERE (CAST(:status AS TEXT) IS NULL OR n.status = CAST(:status AS TEXT))
                  AND (CAST(:ntype AS TEXT) IS NULL
                       OR n.notification_type = CAST(:ntype AS TEXT))
                  AND (CAST(:recipient AS TEXT) IS NULL
                       OR n.recipient ILIKE '%' || CAST(:recipient AS TEXT) || '%')
                  AND (CAST(:uid AS UUID) IS NULL OR n.user_id = CAST(:uid AS UUID))
                ORDER BY n.created_at DESC
                LIMIT :lim OFFSET :off
                """
            ),
            {
                "status": status,
                "ntype": notification_type,
                "recipient": recipient,
                "uid": str(user_id) if user_id else None,
                "lim": limit,
                "off": offset,
            },
        )
    ).mappings().all()

    messages = []
    for row in rows:
        record = dict(row)
        record["variables"] = _redact(record.get("variables"))
        messages.append(record)

    return {"messages": messages, "limit": limit, "offset": offset}


@router.get("/messages/{notification_id}")
async def message_detail(
    notification_id: uuid.UUID,
    admin: CurrentAdmin,
    db: DbSession,
) -> dict:
    """One notification with its full attempt timeline."""
    notification = (
        await db.execute(
            text("SELECT * FROM ops.notifications WHERE id = :id"),
            {"id": str(notification_id)},
        )
    ).mappings().first()
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    attempts = (
        await db.execute(
            text(
                """
                SELECT id, attempt_number, provider, channel, status,
                       provider_message_id, template_name, failure_code,
                       failure_reason, error_class,
                       requested_at, accepted_at, delivered_at, read_at, failed_at
                FROM ops.notification_attempts
                WHERE notification_id = :id
                ORDER BY attempt_number, requested_at
                """
            ),
            {"id": str(notification_id)},
        )
    ).mappings().all()

    record = dict(notification)
    record["variables"] = _redact(record.get("variables"))
    return {"notification": record, "attempts": [dict(a) for a in attempts]}


@router.post("/messages/{notification_id}/resend", dependencies=[RequireMarketing])
async def resend_message(
    notification_id: uuid.UUID,
    admin: CurrentAdmin,
    db: DbSession,
    notifications: NotificationsDep,
) -> dict:
    """Manually re-send a failed notification.

    Only failed ones. Re-sending a delivered message would send a customer a
    second copy, and re-sending an UNKNOWN is exactly the duplicate the whole
    delivery design exists to prevent.

    OTP notifications are refused outright: the underlying challenge may have
    expired or been consumed, so this would deliver a code that no longer works
    — and a resend endpoint that emits OTPs is an account-takeover primitive.
    The customer should request a fresh code instead.
    """
    row = (
        await db.execute(
            text(
                "SELECT status, category, attempt_count FROM ops.notifications WHERE id = :id"
            ),
            {"id": str(notification_id)},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    if row["category"] == "otp":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "otp_resend_forbidden",
                "message": "OTP notifications cannot be resent. Ask the customer "
                "to request a new code.",
            },
        )

    if row["status"] != "failed":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "resend_not_failed",
                "message": f"Only failed notifications can be resent (this one is "
                f"'{row['status']}').",
            },
        )

    # Reset to pending so the delivery worker claims it. attempt_count is left
    # alone, so the retry budget still applies and a manual resend of a
    # permanently-broken message cannot be used to hammer Meta.
    await db.execute(
        text(
            """
            UPDATE ops.notifications
            SET status = 'pending', next_retry_at = NULL,
                failed_at = NULL, completed_at = NULL
            WHERE id = :id
            """
        ),
        {"id": str(notification_id)},
    )
    await db.commit()

    status = await notifications.deliver(notification_id)
    logger.info(
        "notification_resent id=%s admin=%s status=%s", notification_id, admin.id, status
    )
    return {"notification_id": str(notification_id), "status": str(status)}


# ====================================================================== #
# Analytics
# ====================================================================== #
@router.get("/analytics/overview")
async def analytics_overview(
    admin: CurrentAdmin,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Headline delivery numbers."""
    return await AnalyticsService(db).overview(days=days)


@router.get("/analytics/otp")
async def analytics_otp(
    admin: CurrentAdmin,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """OTP success rate — can customers actually log in."""
    return await AnalyticsService(db).otp_success_rate(days=days)


@router.get("/analytics/by-type")
async def analytics_by_type(
    admin: CurrentAdmin,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Per-notification-type delivery breakdown."""
    return {"types": await AnalyticsService(db).by_type(days=days)}


@router.get("/analytics/failures")
async def analytics_failures(
    admin: CurrentAdmin,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Meta error codes behind recent failures, most frequent first."""
    return {"failures": await AnalyticsService(db).failure_reasons(days=days)}


@router.get("/analytics/daily")
async def analytics_daily(
    admin: CurrentAdmin,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
    notification_type: str | None = Query(default=None),
) -> dict:
    """Daily time series from the rollup table."""
    return {
        "series": await AnalyticsService(db).daily_series(
            days=days, notification_type=notification_type
        )
    }


# ====================================================================== #
# Consent
# ====================================================================== #
class PreferencesUpdate(BaseModel):
    whatsapp_marketing: bool | None = None
    whatsapp_transactional: bool | None = None
    whatsapp_abandoned_cart: bool | None = None


@router.get("/preferences/{user_id}")
async def get_preferences(
    user_id: uuid.UUID,
    admin: CurrentAdmin,
    db: DbSession,
) -> dict:
    """A customer's messaging consent."""
    row = (
        await db.execute(
            text(
                """
                SELECT user_id, whatsapp_marketing, whatsapp_transactional,
                       whatsapp_abandoned_cart, updated_at
                FROM public.user_preferences
                WHERE user_id = :uid
                """
            ),
            {"uid": str(user_id)},
        )
    ).mappings().first()

    if row is None:
        # No row means no recorded opt-in. Reported as all-false rather than 404,
        # because "this customer has not consented" is the honest answer and a
        # 404 invites the caller to treat it as unknown.
        return {
            "user_id": str(user_id),
            "whatsapp_marketing": False,
            "whatsapp_transactional": True,
            "whatsapp_abandoned_cart": False,
            "note": "No preferences row — defaults shown.",
        }
    return dict(row)


@router.patch("/preferences/{user_id}", dependencies=[RequireMarketing])
async def update_preferences(
    user_id: uuid.UUID,
    payload: PreferencesUpdate,
    admin: CurrentAdmin,
    db: DbSession,
) -> dict:
    """Update consent on a customer's behalf.

    Exists so support can honour an opt-out a customer made by replying STOP or
    over the phone. Every change is audit-logged with the acting admin, because
    a consent record that can be changed without a trail is not a consent record.
    """
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    row = (
        await db.execute(
            text(
                """
                INSERT INTO public.user_preferences
                    (user_id, whatsapp_marketing, whatsapp_transactional,
                     whatsapp_abandoned_cart)
                VALUES (:uid,
                        COALESCE(:marketing, FALSE),
                        COALESCE(:transactional, TRUE),
                        COALESCE(:cart, FALSE))
                ON CONFLICT (user_id) DO UPDATE
                SET whatsapp_marketing =
                        COALESCE(:marketing, public.user_preferences.whatsapp_marketing),
                    whatsapp_transactional =
                        COALESCE(:transactional, public.user_preferences.whatsapp_transactional),
                    whatsapp_abandoned_cart =
                        COALESCE(:cart, public.user_preferences.whatsapp_abandoned_cart)
                RETURNING user_id, whatsapp_marketing, whatsapp_transactional,
                          whatsapp_abandoned_cart
                """
            ),
            {
                "uid": str(user_id),
                "marketing": fields.get("whatsapp_marketing"),
                "transactional": fields.get("whatsapp_transactional"),
                "cart": fields.get("whatsapp_abandoned_cart"),
            },
        )
    ).mappings().first()
    await db.commit()

    logger.info(
        "consent_updated user=%s admin=%s changes=%s",
        user_id,
        admin.id,
        ",".join(fields),
    )
    return dict(row) if row else {}


# ====================================================================== #
# Health
# ====================================================================== #
@router.get("/health")
async def whatsapp_health(admin: CurrentAdmin, db: DbSession) -> dict:
    """Is the channel actually able to send right now.

    Surfaces the configuration state plus the recent failure mix, so "customers
    say they aren't getting codes" can be answered without reading logs.
    """
    queue = (
        await db.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status = 'pending')  AS pending,
                  COUNT(*) FILTER (WHERE status = 'sending')  AS sending,
                  COUNT(*) FILTER (WHERE status = 'unknown')  AS unknown,
                  COUNT(*) FILTER (WHERE status = 'pending'
                                   AND next_retry_at IS NOT NULL) AS awaiting_retry
                FROM ops.notifications
                WHERE created_at > now() - interval '24 hours'
                """
            )
        )
    ).mappings().one()

    active_templates = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM ops.notification_templates
                WHERE channel = 'whatsapp' AND is_active
                """
            )
        )
    ).scalar_one()

    return {
        "whatsapp_configured": settings.whatsapp_configured,
        "whatsapp_enabled": settings.whatsapp_enabled,
        "phone_number_id_set": bool(settings.whatsapp_phone_number_id),
        "webhook_secret_set": bool(settings.whatsapp_signing_secret),
        "active_templates": int(active_templates),
        "queue": dict(queue),
    }
