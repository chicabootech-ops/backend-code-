"""Checkout + Razorpay payment orchestration.

The rule this module is built around: **the browser is a hint, the provider is
the authority.** Razorpay Checkout's callback tells us *where to look*; it never
decides that money moved. Every settlement is confirmed either by a signed
webhook or by our own fetch against the Razorpay REST API.

Flow:
  1. create_checkout  -> validate items, price with GST, create a pending order,
                          reserve stock, create a Razorpay order, return params.
  2. retry_payment    -> a fresh attempt on the *same* order (never a new order).
  3. verify_payment   -> browser callback: check the signature, then confirm the
                          real status with the provider before settling.
  4. handle_webhook   -> signed server-to-server truth; deduplicated in the DB.
  5. reconcile        -> see reconciliation_service; resolves anything left
                          unresolved when a callback or webhook never arrived.

Transaction discipline: no Razorpay call is ever made while holding a row lock.
Each settlement is (fetch from provider) -> (lock, apply, commit) -> (notify).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.events.bus import get_event_bus
from app.events.types import EventType
from app.storefront.lib.razorpay_client import (
    PaymentGatewayError,
    PaymentGatewayTimeout,
    RazorpayClient,
)
from app.storefront.models.commerce import Order, OrderItem, OrderTaxLine, Payment
from app.storefront.repositories.invoice_repository import InvoiceRepository
from app.storefront.repositories.order_repository import OrderRepository
from app.storefront.repositories.payment_repository import (
    NotificationLogRepository,
    PaymentRepository,
    WebhookEventRepository,
)
from app.storefront.repositories.product_repository import ProductRepository
from app.storefront.schemas.order import CheckoutItemIn, CheckoutRequest
from app.storefront.schemas.payment import (
    CheckoutResponse,
    PaymentStatusOut,
    RazorpayCheckoutOut,
    VerifyPaymentRequest,
)
from app.storefront.services.bouquet_service import (
    BASE_PRODUCT_SQL as BASE_BOUQUET_PRODUCT_SQL,
    BouquetError,
    BouquetService,
)
from app.storefront.services.coupon_service import CouponError, CouponService
from app.storefront.services.inventory_service import InventoryService, OutOfStockError
from app.storefront.services.invoice_service import InvoiceService
from app.storefront.services.payment_state import (
    RETRYABLE,
    SETTLED,
    UNRESOLVED,
    InvalidTransition,
    PaymentStatus,
    TransitionSource,
    can_transition,
    failure_copy,
    from_provider_status,
    from_webhook_event,
    project_order,
)
from app.storefront.services.pricing import price_order

logger = logging.getLogger(__name__)

#: How long after creation an unresolved attempt first gets reconciled.
FIRST_RECONCILE_DELAY = timedelta(minutes=2)


class CheckoutError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "checkout_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class PaymentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        razorpay: RazorpayClient,
        email_service: Any | None = None,
    ) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._payments = PaymentRepository(session)
        self._webhooks = WebhookEventRepository(session)
        self._notifications = NotificationLogRepository(session)
        self._invoices = InvoiceRepository(session)
        self._products = ProductRepository(session)
        self._invoice_service = InvoiceService(session)
        self._inventory = InventoryService(session)
        self._coupons = CouponService(session)
        self._razorpay = razorpay
        self._email = email_service

    # ------------------------------------------------------------------ #
    # 1. Checkout
    # ------------------------------------------------------------------ #
    async def create_checkout(
        self, *, user_id: uuid.UUID | None, request: CheckoutRequest
    ) -> CheckoutResponse:
        if not self._razorpay.configured:
            raise CheckoutError(
                "Online payments are not configured yet. Please try again shortly.",
                status_code=503,
                code="payment_not_configured",
            )

        # Idempotency: a repeat submit/retry with the same key reuses the pending
        # order + Razorpay order instead of creating duplicates (Case 21).
        if request.idempotency_key and user_id:
            existing = await self._orders.find_reusable_pending(
                user_id=user_id, idempotency_key=request.idempotency_key
            )
            if existing is not None:
                payment = await self._payments.get_by_order(existing.id)
                if payment and payment.provider_order_id:
                    ship = existing.shipping_address or {}
                    return CheckoutResponse(
                        order_id=existing.id,
                        order_number=existing.order_number,
                        grand_total_paise=existing.grand_total_paise,
                        payment_status=existing.payment_status,
                        razorpay=RazorpayCheckoutOut(
                            key_id=self._razorpay.key_id,
                            razorpay_order_id=payment.provider_order_id,
                            amount_paise=existing.grand_total_paise,
                            currency="INR",
                            description=f"Chic A Boo Order #{existing.order_number}",
                            prefill_name=ship.get("full_name"),
                            prefill_email=await self._user_email(user_id) or request.email,
                            prefill_contact=ship.get("phone"),
                        ),
                    )

        raw_items = await self._resolve_items(request)
        shipping, billing = await self._resolve_addresses(user_id=user_id, request=request)

        priced = price_order(
            raw_items,
            shipping_state_code=(shipping.get("state_code") or None),
        )
        if priced.grand_total_paise <= 0:
            raise CheckoutError("Cart total must be greater than zero.")

        discount_paise = 0
        coupon_id = None
        coupon_code = None
        if request.coupon_code:
            try:
                coupon = await self._coupons.validate(
                    request.coupon_code,
                    user_id=user_id,
                    subtotal_paise=priced.subtotal_paise,
                    shipping_paise=priced.shipping_paise,
                )
            except CouponError as exc:
                raise CheckoutError(exc.message, status_code=400, code=exc.code) from exc
            discount_paise = coupon.discount_paise
            coupon_id = coupon.coupon_id
            coupon_code = coupon.code

        final_total = max(0, priced.grand_total_paise - discount_paise)
        if final_total <= 0:
            raise CheckoutError("Order total after discount must be greater than zero.")

        user_email = await self._user_email(user_id) or request.email

        order = Order(
            user_id=user_id,
            guest_email=None if user_id else (request.email or None),
            status="pending",
            payment_status="pending",
            fulfillment_status="unfulfilled",
            currency="INR",
            subtotal_paise=priced.subtotal_paise,
            discount_paise=discount_paise,
            tax_paise=priced.tax_paise,
            shipping_paise=priced.shipping_paise,
            grand_total_paise=final_total,
            coupon_id=coupon_id,
            coupon_code=coupon_code,
            shipping_address=shipping,
            billing_address=billing or shipping,
            gstin=(request.gstin or None),
            customer_note=(request.customer_note or None),
            metadata_={
                "channel": "web",
                **({"idempotency_key": request.idempotency_key} if request.idempotency_key else {}),
            },
        )
        order = await self._orders.create_order(order)

        await self._orders.add_items(
            [
                OrderItem(
                    order_id=order.id,
                    product_variant_id=uuid.UUID(p.product_variant_id),
                    product_id=uuid.UUID(p.product_id),
                    sku=p.sku,
                    product_name=p.product_name,
                    variant_title=p.variant_title,
                    quantity=p.quantity,
                    unit_price_paise=p.unit_price_paise,
                    discount_paise=0,
                    tax_paise=p.tax_paise,
                    line_total_paise=p.line_gross_paise,
                    hsn_code=p.hsn_code,
                    tax_rate_bps=p.tax_rate_bps or None,
                    metadata_=p.metadata,
                )
                for p in priced.items
            ]
        )
        await self._orders.add_tax_lines(
            [
                OrderTaxLine(
                    order_id=order.id,
                    tax_type=t.tax_type,
                    tax_rate_bps=t.tax_rate_bps,
                    taxable_amount_paise=t.taxable_amount_paise,
                    tax_amount_paise=t.tax_amount_paise,
                )
                for t in priced.tax_lines
            ]
        )
        await self._orders.add_status_history(
            order.id, to_status="pending", from_status=None, reason="Order placed"
        )

        try:
            await self._inventory.reserve(
                order_id=order.id,
                items=[
                    {
                        "product_variant_id": p.product_variant_id,
                        "quantity": p.quantity,
                        "product_name": p.product_name,
                    }
                    for p in priced.items
                ],
            )
        except OutOfStockError as exc:
            raise CheckoutError(
                f"Sorry, these just sold out: {', '.join(exc.unavailable)}.",
                status_code=409,
                code="out_of_stock",
            ) from exc

        rp_order = await self._create_provider_order(order, final_total)
        await self._record_attempt(order, user_id=user_id, rp_order=rp_order, amount_paise=final_total)
        await self._session.commit()

        await get_event_bus().publish(
            EventType.ORDER_CREATED,
            {
                "order_id": str(order.id),
                "order_number": order.order_number,
                "total_paise": final_total,
                "user_id": str(user_id) if user_id else None,
                "item_count": len(raw_items),
            },
        )

        return CheckoutResponse(
            order_id=order.id,
            order_number=order.order_number,
            grand_total_paise=final_total,
            payment_status=order.payment_status,
            razorpay=RazorpayCheckoutOut(
                key_id=self._razorpay.key_id,
                razorpay_order_id=rp_order["id"],
                amount_paise=final_total,
                currency="INR",
                description=f"Chic A Boo Order #{order.order_number}",
                prefill_name=shipping.get("full_name"),
                prefill_email=user_email,
                prefill_contact=shipping.get("phone"),
            ),
        )

    # ------------------------------------------------------------------ #
    # 2. Retry — a new attempt on the SAME order (Case 19)
    # ------------------------------------------------------------------ #
    async def retry_payment(
        self, *, order_id: uuid.UUID, user_id: uuid.UUID | None
    ) -> CheckoutResponse:
        order = await self._orders.get_by_id(order_id, user_id=user_id)
        if order is None:
            raise CheckoutError("Order not found.", status_code=404, code="order_not_found")
        if order.status in ("cancelled", "refunded"):
            raise CheckoutError(
                "This order is closed. Please place a new order.",
                status_code=409,
                code="order_closed",
            )

        latest = await self._payments.get_by_order(order.id)
        if latest is not None:
            status = PaymentStatus(latest.status)
            if status in SETTLED:
                raise CheckoutError(
                    "This order is already paid.", status_code=409, code="already_paid"
                )
            # Refusing to open a second checkout while one is genuinely in flight
            # is the whole point of Case 18 — never invite a second charge while
            # the first is unresolved.
            if status in UNRESOLVED and status != PaymentStatus.CREATED:
                raise CheckoutError(
                    "Your previous payment is still being confirmed. Please wait a "
                    "moment before trying again.",
                    status_code=409,
                    code="payment_in_flight",
                )
            if status not in RETRYABLE and status != PaymentStatus.CREATED:
                raise CheckoutError(
                    "This payment cannot be retried.", status_code=409, code="not_retryable"
                )

        rp_order = await self._create_provider_order(order, order.grand_total_paise)
        await self._record_attempt(
            order,
            user_id=order.user_id,
            rp_order=rp_order,
            amount_paise=order.grand_total_paise,
        )
        # A retry re-opens the order for payment.
        order.payment_status = "pending"
        await self._session.commit()

        ship = order.shipping_address or {}
        return CheckoutResponse(
            order_id=order.id,
            order_number=order.order_number,
            grand_total_paise=order.grand_total_paise,
            payment_status=order.payment_status,
            razorpay=RazorpayCheckoutOut(
                key_id=self._razorpay.key_id,
                razorpay_order_id=rp_order["id"],
                amount_paise=order.grand_total_paise,
                currency="INR",
                description=f"Chic A Boo Order #{order.order_number}",
                prefill_name=ship.get("full_name"),
                prefill_email=order.guest_email or await self._user_email(order.user_id),
                prefill_contact=ship.get("phone"),
            ),
        )

    # ------------------------------------------------------------------ #
    # 3. Verify (browser callback — a hint, not proof)
    # ------------------------------------------------------------------ #
    async def verify_payment(
        self, *, user_id: uuid.UUID | None, payload: VerifyPaymentRequest
    ) -> PaymentStatusOut:
        order = await self._orders.get_by_id(payload.order_id, user_id=user_id)
        if order is None:
            raise CheckoutError("Order not found.", status_code=404, code="order_not_found")

        payment = await self._payments.get_by_provider_order_id(payload.razorpay_order_id)
        if payment is None or payment.order_id != order.id:
            raise CheckoutError("Payment record not found for this order.", status_code=404)

        # A webhook may already have settled this while the browser was talking
        # to us (Case 7). Nothing to do — report the settled truth.
        if PaymentStatus(payment.status) in SETTLED:
            return await self._status_out(order, payment)

        signature_ok = self._razorpay.verify_checkout_signature(
            order_id=payload.razorpay_order_id,
            payment_id=payload.razorpay_payment_id,
            signature=payload.razorpay_signature,
        )
        if not signature_ok:
            # Do NOT call this failed. A forged callback and a mangled one are
            # indistinguishable here, and the real payment may well have gone
            # through. Record it, hand it to reconciliation, and let the provider
            # decide (Case 5).
            logger.warning(
                "payment_verification_failed order=%s reason=signature_mismatch",
                order.order_number,
            )
            await self._transition(
                order,
                payment,
                target=PaymentStatus.VERIFICATION_REQUIRED,
                source=TransitionSource.CLIENT_CALLBACK,
                failure_reason="Client signature did not verify",
                failure_code="signature_mismatch",
                raw={"reason": "signature_mismatch"},
            )
            await self._session.commit()
            raise CheckoutError(
                "We could not confirm this payment yet. If money was debited we will "
                "reconcile it automatically — please do not pay again.",
                status_code=202,
                code="verification_required",
            )

        # Signature proves the id pair came from Razorpay. It does not prove the
        # payment was captured, nor for how much. Ask the provider directly.
        logger.info(
            "payment_verification_started order=%s payment=%s",
            order.order_number,
            payload.razorpay_payment_id,
        )
        try:
            entity = await self._razorpay.fetch_payment(payload.razorpay_payment_id)
        except PaymentGatewayTimeout:
            await self._transition(
                order,
                payment,
                target=PaymentStatus.VERIFICATION_REQUIRED,
                source=TransitionSource.CLIENT_CALLBACK,
                provider_payment_id=payload.razorpay_payment_id,
                failure_reason="Provider unreachable during verification",
                failure_code="gateway_timeout",
            )
            await self._session.commit()
            raise CheckoutError(
                "Your payment is being verified. This usually takes a few moments.",
                status_code=202,
                code="verification_required",
            ) from None
        except PaymentGatewayError as exc:
            await self._transition(
                order,
                payment,
                target=PaymentStatus.VERIFICATION_REQUIRED,
                source=TransitionSource.CLIENT_CALLBACK,
                provider_payment_id=payload.razorpay_payment_id,
                failure_reason=exc.message,
                failure_code=exc.code,
            )
            await self._session.commit()
            raise CheckoutError(
                "Your payment is being verified. This usually takes a few moments.",
                status_code=202,
                code="verification_required",
            ) from exc

        await self._apply_provider_entity(
            order, payment, entity, source=TransitionSource.PROVIDER_FETCH
        )
        await self._session.commit()

        refreshed = await self._payments.get_by_provider_order_id(payload.razorpay_order_id)
        payment = refreshed or payment
        if PaymentStatus(payment.status) == PaymentStatus.CAPTURED:
            await self._after_settlement(order)
        return await self._status_out(order, payment)

    # ------------------------------------------------------------------ #
    # 4. Webhook (signed, server-to-server, authoritative)
    # ------------------------------------------------------------------ #
    async def handle_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        event: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, str]:
        event_type = str(event.get("event") or "unknown")
        payment_entity = (event.get("payload", {}).get("payment", {}) or {}).get("entity", {}) or {}
        refund_entity = (event.get("payload", {}).get("refund", {}) or {}).get("entity", {}) or {}
        provider_order_id = payment_entity.get("order_id") or (
            event.get("payload", {}).get("order", {}) or {}
        ).get("entity", {}).get("id")
        provider_payment_id = payment_entity.get("id") or refund_entity.get("payment_id")

        signature_ok = self._razorpay.verify_webhook_signature(
            raw_body=raw_body, signature=signature
        )

        # Record every delivery, valid or not — repeated signature failures against
        # a payments endpoint are a security signal worth keeping.
        claimed = await self._webhooks.claim(
            provider="razorpay",
            provider_event_id=event_id,
            event_type=event_type,
            signature_valid=signature_ok,
            payload=event,
            provider_order_id=provider_order_id,
            provider_payment_id=provider_payment_id,
        )
        await self._session.commit()

        if not signature_ok:
            logger.warning("webhook_signature_invalid event=%s", event_type)
            raise CheckoutError(
                "Invalid webhook signature.", status_code=400, code="invalid_signature"
            )

        if claimed is None:
            # Another delivery of the same event already owns this. Returning 200
            # stops Razorpay retrying something we have handled (Case 9).
            logger.info("webhook_duplicate event=%s id=%s", event_type, event_id)
            return {"status": "duplicate"}

        logger.info("webhook_received event=%s id=%s", event_type, event_id)

        try:
            outcome = await self._dispatch_webhook(
                event_type=event_type,
                payment_entity=payment_entity,
                refund_entity=refund_entity,
                provider_order_id=provider_order_id,
            )
        except Exception as exc:  # noqa: BLE001
            await self._session.rollback()
            await self._webhooks.finish(
                claimed, processing_status="failed", error=f"{type(exc).__name__}: {exc}"
            )
            await self._session.commit()
            logger.exception("webhook_processing_failed event=%s", event_type)
            # Non-2xx asks Razorpay to redeliver; the dedupe row is already
            # marked failed so the retry will be allowed to re-run.
            raise

        await self._webhooks.finish(
            claimed,
            processing_status=outcome["status"],
            payment_id=outcome.get("payment_id"),
            order_id=outcome.get("order_id"),
        )
        await self._session.commit()

        if outcome.get("settled_order") is not None:
            await self._after_settlement(outcome["settled_order"])

        logger.info("webhook_processed event=%s outcome=%s", event_type, outcome["status"])
        return {"status": outcome["status"]}

    async def _dispatch_webhook(
        self,
        *,
        event_type: str,
        payment_entity: dict[str, Any],
        refund_entity: dict[str, Any],
        provider_order_id: str | None,
    ) -> dict[str, Any]:
        if event_type.startswith("refund."):
            return await self._handle_refund_event(event_type, refund_entity)

        target = from_webhook_event(event_type)
        if target is None:
            # Unknown events are acknowledged and ignored — never allowed to
            # touch state, never allowed to crash the endpoint (Case 28).
            logger.info("webhook_ignored event=%s", event_type)
            return {"status": "ignored"}

        if not provider_order_id:
            logger.warning("webhook_missing_order_id event=%s", event_type)
            return {"status": "ignored"}

        payment = await self._payments.lock_by_provider_order_id(provider_order_id)
        if payment is None:
            logger.warning("webhook_unknown_order provider_order_id=%s", provider_order_id)
            return {"status": "ignored"}

        order = await self._orders.get_by_id(payment.order_id)
        if order is None:
            return {"status": "ignored"}

        settled = await self._apply_provider_entity(
            order,
            payment,
            payment_entity,
            source=TransitionSource.WEBHOOK,
            asserted=target,
        )
        return {
            "status": "processed",
            "payment_id": payment.id,
            "order_id": order.id,
            "settled_order": order if settled else None,
        }

    async def _handle_refund_event(
        self, event_type: str, refund_entity: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve a refund that ``RefundService`` left in ``pending``."""
        provider_refund_id = refund_entity.get("id")
        if not provider_refund_id:
            return {"status": "ignored"}

        status_map = {
            "refund.processed": ("processed", "processed"),
            "refund.failed": ("failed", "failed"),
            "refund.created": ("pending", "pending"),
            "refund.speed_changed": ("pending", None),
        }
        mapped = status_map.get(event_type)
        if mapped is None:
            return {"status": "ignored"}
        our_status, _ = mapped

        row = (
            await self._session.execute(
                text(
                    """
                    UPDATE commerce.refunds
                    SET status = :status,
                        provider_status = :provider_status,
                        processed_at = CASE WHEN :status = 'processed' THEN now() ELSE processed_at END,
                        failure_reason = :failure_reason,
                        last_synced_at = now()
                    WHERE provider_refund_id = :rid
                      -- never walk a settled refund backwards
                      AND status <> 'processed'
                    RETURNING id, order_id, payment_id, amount_paise
                    """
                ),
                {
                    "status": our_status,
                    "provider_status": refund_entity.get("status"),
                    "failure_reason": (refund_entity.get("notes") or {}).get("reason")
                    if event_type == "refund.failed"
                    else None,
                    "rid": provider_refund_id,
                },
            )
        ).mappings().first()

        if row is None:
            return {"status": "ignored"}

        if our_status == "processed":
            await self._sync_refund_totals(row["order_id"], row["payment_id"])
        return {"status": "processed", "order_id": row["order_id"], "payment_id": row["payment_id"]}

    async def _sync_refund_totals(self, order_id: uuid.UUID, payment_id: uuid.UUID) -> None:
        """Recompute payment/order refund state from the refunds actually processed."""
        totals = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      (SELECT amount_paise FROM commerce.payments WHERE id = :pid) AS captured,
                      COALESCE((
                        SELECT SUM(amount_paise) FROM commerce.refunds
                        WHERE payment_id = :pid AND status = 'processed'
                      ), 0) AS refunded
                    """
                ),
                {"pid": str(payment_id)},
            )
        ).mappings().first()
        if totals is None:
            return

        full = int(totals["refunded"]) >= int(totals["captured"])
        payment_status = PaymentStatus.REFUNDED if full else PaymentStatus.PARTIALLY_REFUNDED
        await self._session.execute(
            text("UPDATE commerce.payments SET status = :s WHERE id = :pid"),
            {"s": str(payment_status), "pid": str(payment_id)},
        )
        order_status, order_payment_status = project_order(payment_status)
        await self._session.execute(
            text(
                """
                UPDATE commerce.orders
                SET payment_status = :ps,
                    status = COALESCE(:os, status)
                WHERE id = :oid
                """
            ),
            {"ps": order_payment_status, "os": order_status, "oid": str(order_id)},
        )

    # ------------------------------------------------------------------ #
    # Applying a provider entity
    # ------------------------------------------------------------------ #
    async def _apply_provider_entity(
        self,
        order: Order,
        payment: Payment,
        entity: dict[str, Any],
        *,
        source: TransitionSource,
        asserted: PaymentStatus | None = None,
    ) -> bool:
        """Move a payment to whatever the provider says it is. Returns True if settled."""
        target = from_provider_status(entity.get("status")) or asserted
        if target is None:
            logger.info(
                "provider_entity_unmapped order=%s status=%s",
                order.order_number,
                entity.get("status"),
            )
            return False

        # Amount tampering guard. A capture for the wrong amount is never settled
        # silently — it goes to a human.
        if target in SETTLED:
            mismatch = self._amount_mismatch(payment, entity)
            if mismatch:
                logger.error(
                    "payment_amount_mismatch order=%s expected=%s got=%s",
                    order.order_number,
                    payment.amount_paise,
                    entity.get("amount"),
                )
                await self._payments.flag_for_review(payment, reason=mismatch)
                await self._transition(
                    order,
                    payment,
                    target=PaymentStatus.VERIFICATION_REQUIRED,
                    source=source,
                    provider_payment_id=entity.get("id"),
                    failure_reason=mismatch,
                    failure_code="amount_mismatch",
                    raw=entity,
                )
                return False

        return await self._transition(
            order,
            payment,
            target=target,
            source=source,
            provider_payment_id=entity.get("id"),
            method=_normalise_method(entity.get("method")),
            failure_reason=entity.get("error_description"),
            failure_code=entity.get("error_reason") or entity.get("error_code"),
            raw=entity,
        )

    def _amount_mismatch(self, payment: Payment, entity: dict[str, Any]) -> str | None:
        amount = entity.get("amount")
        currency = (entity.get("currency") or "INR").upper()
        if amount is not None and int(amount) != int(payment.amount_paise):
            return f"Amount mismatch: expected {payment.amount_paise}, provider reported {amount}"
        if currency != (payment.currency or "INR").upper():
            return f"Currency mismatch: expected {payment.currency}, provider reported {currency}"
        return None

    async def _transition(
        self,
        order: Order,
        payment: Payment,
        *,
        target: PaymentStatus,
        source: TransitionSource,
        provider_payment_id: str | None = None,
        method: str | None = None,
        failure_reason: str | None = None,
        failure_code: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> bool:
        """Apply one state-machine edge. Returns True if this call settled the order."""
        current = PaymentStatus(payment.status)
        allowed, reason = can_transition(current, target, source=source)
        if not allowed:
            if reason == "no_change":
                logger.info(
                    "payment_transition_noop order=%s status=%s source=%s",
                    order.order_number,
                    current,
                    source,
                )
            else:
                logger.warning(
                    "payment_transition_rejected order=%s %s->%s source=%s reason=%s",
                    order.order_number,
                    current,
                    target,
                    source,
                    reason,
                )
            return False

        now = datetime.now(UTC)
        await self._payments.mark_status(
            payment,
            status=str(target),
            provider_payment_id=provider_payment_id,
            method=method,
            failure_reason=failure_reason,
            failure_code=failure_code,
            verified_at=now if source in (TransitionSource.WEBHOOK, TransitionSource.PROVIDER_FETCH) else None,
            captured_at=now if target == PaymentStatus.CAPTURED else None,
        )
        await self._payments.add_transaction(
            payment.id,
            transaction_type=_TRANSACTION_TYPE.get(target, "status_change"),
            status="success" if target in SETTLED else "failed" if target == PaymentStatus.FAILED else "pending",
            amount_paise=payment.amount_paise,
            provider_transaction_id=provider_payment_id,
            raw_payload={"source": str(source), "to": str(target), **(raw or {})},
        )

        # Unresolved attempts stay in the reconciler's queue; resolved ones leave it.
        if target in UNRESOLVED:
            await self._payments.schedule_reconcile(
                payment, next_at=now + FIRST_RECONCILE_DELAY
            )
        else:
            await self._payments.schedule_reconcile(payment, next_at=None)

        order_status, order_payment_status = project_order(target)
        await self._orders.set_payment_status(
            order, payment_status=order_payment_status, status=order_status
        )
        if order_status:
            await self._orders.add_status_history(
                order.id,
                to_status=order_status,
                from_status=order.status,
                reason=f"Payment {target} ({source})",
            )

        logger.info(
            "payment_%s order=%s attempt=%s source=%s",
            target,
            order.order_number,
            payment.attempt_number,
            source,
        )

        if target == PaymentStatus.CAPTURED:
            return await self._settle_order(order, payment)

        if target == PaymentStatus.FAILED:
            await get_event_bus().publish(
                EventType.PAYMENT_FAILED,
                {
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                    "reason": failure_code or failure_reason or "unknown",
                },
            )
        return False

    async def _settle_order(self, order: Order, payment: Payment) -> bool:
        """Once-only consequences of money actually arriving.

        Guarded against a second captured attempt on the same order (Case 20):
        the money is real either way, so the payment stays ``captured``, but the
        order is not fulfilled twice — the duplicate is flagged for a refund
        decision instead.
        """
        other_captured = (
            await self._session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM commerce.payments
                    WHERE order_id = :oid
                      AND id <> :pid
                      AND status IN ('captured', 'refund_pending',
                                     'partially_refunded', 'refunded')
                    """
                ),
                {"oid": str(order.id), "pid": str(payment.id)},
            )
        ).scalar_one()

        if int(other_captured) > 0:
            logger.error(
                "duplicate_capture order=%s attempt=%s — flagging for refund review",
                order.order_number,
                payment.attempt_number,
            )
            await self._payments.flag_for_review(
                payment,
                reason=(
                    "Duplicate capture: this order already has a settled payment. "
                    "Likely an overpayment requiring a refund."
                ),
            )
            return False

        await self._inventory.commit(order.id)
        if order.coupon_id:
            await self._coupons.record_usage(
                coupon_id=order.coupon_id,
                user_id=order.user_id,
                order_id=order.id,
                discount_paise=order.discount_paise,
            )
        await get_event_bus().publish(
            EventType.PAYMENT_CAPTURED,
            {
                "order_id": str(order.id),
                "order_number": order.order_number,
                "amount_paise": payment.amount_paise,
                "provider_payment_id": payment.provider_payment_id,
                "method": payment.method,
            },
        )
        return True

    # ------------------------------------------------------------------ #
    # Post-settlement (external I/O, after commit)
    # ------------------------------------------------------------------ #
    async def _after_settlement(self, order: Order) -> None:
        """Invoice + confirmation email. Runs after commit; never raises."""
        # Claim the send slot first. If this returns None someone already sent
        # the confirmation, so a redelivered webhook stays silent (Case 8).
        to_email = order.guest_email or await self._user_email(order.user_id)
        claim = await self._notifications.claim(
            order_id=order.id, kind="order_confirmed", recipient=to_email
        )
        await self._session.commit()
        if claim is None:
            logger.info("notification_skipped_duplicate order=%s", order.order_number)
            return

        invoice = None
        pdf_bytes: bytes | None = None
        try:
            invoice = await self._invoice_service.ensure_invoice(order, payment_method="Prepaid")
            await self._session.commit()
            pdf_bytes = await self._invoice_service.render_pdf_bytes(order, invoice)
        except Exception:  # noqa: BLE001
            logger.exception("Invoice generation failed for order %s", order.order_number)
            await self._session.rollback()

        if self._email is None or not to_email:
            return

        order_label = f"CAB{order.order_number}"
        total_label = f"Rs. {order.grand_total_paise / 100:,.2f}"
        track_url = f"{settings.site_url.rstrip('/')}/track-order?order={order.order_number}"
        try:
            if invoice is not None and pdf_bytes:
                await self._email.send_invoice_email(
                    to_email=to_email,
                    order_number=order_label,
                    invoice_number=f"{settings.invoice_prefix}-{invoice.invoice_number}",
                    total_label=total_label,
                    pdf_bytes=pdf_bytes,
                    filename=f"chicaboo-invoice-{order.order_number}.pdf",
                    track_url=track_url,
                )
            else:
                await self._email.send_order_confirmation(
                    to_email=to_email,
                    order_number=order_label,
                    total_label=total_label,
                    track_url=track_url,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Order email failed for order %s", order.order_number)
            # Release the slot so a retry or the reconciler can send it later.
            await self._notifications.mark_failed(claim, error=str(exc))
            await self._session.commit()

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    async def get_status(
        self, *, order_id: uuid.UUID, user_id: uuid.UUID | None
    ) -> PaymentStatusOut:
        order = await self._orders.get_by_id(order_id, user_id=user_id)
        if order is None:
            raise CheckoutError("Order not found.", status_code=404, code="order_not_found")
        payment = await self._payments.get_by_order(order_id)
        return await self._status_out(order, payment)

    async def _status_out(self, order: Order, payment: Payment | None) -> PaymentStatusOut:
        invoice = await self._invoices.get_by_order(order.id)
        status = PaymentStatus(payment.status) if payment else PaymentStatus.CREATED
        return PaymentStatusOut(
            order_id=order.id,
            order_number=order.order_number,
            payment_status=order.payment_status,
            order_status=order.status,
            payment_attempt_status=str(status),
            attempt_number=payment.attempt_number if payment else 0,
            provider=payment.provider if payment else None,
            provider_payment_id=payment.provider_payment_id if payment else None,
            amount_paise=order.grand_total_paise,
            currency=order.currency,
            invoice_number=invoice.invoice_number if invoice else None,
            is_verification_pending=status in UNRESOLVED
            and status != PaymentStatus.CREATED,
            can_retry=status in RETRYABLE or status == PaymentStatus.CREATED,
            needs_attention=bool(payment and payment.needs_admin_review),
            failure_message=failure_copy(payment.failure_code)
            if payment and status == PaymentStatus.FAILED
            else None,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _create_provider_order(self, order: Order, amount_paise: int) -> dict[str, Any]:
        try:
            return await self._razorpay.create_order(
                amount_paise=amount_paise,
                receipt=f"cab-{order.order_number}",
                notes={"order_id": str(order.id), "order_number": str(order.order_number)},
            )
        except PaymentGatewayTimeout as exc:
            # Nothing was charged — no Razorpay order means no checkout could
            # open — so failing the request here is safe and honest.
            raise CheckoutError(
                "The payment provider is not responding. Your bag is saved — please try again.",
                status_code=503,
                code="payment_gateway_timeout",
            ) from exc
        except PaymentGatewayError as exc:
            raise CheckoutError(exc.message, status_code=502, code=exc.code) from exc

    async def _record_attempt(
        self,
        order: Order,
        *,
        user_id: uuid.UUID | None,
        rp_order: dict[str, Any],
        amount_paise: int,
    ) -> Payment:
        attempt = await self._payments.next_attempt_number(order.id)
        return await self._payments.create_payment(
            Payment(
                order_id=order.id,
                user_id=user_id,
                attempt_number=attempt,
                provider="razorpay",
                provider_order_id=rp_order.get("id"),
                amount_paise=amount_paise,
                currency="INR",
                status=str(PaymentStatus.CREATED),
                next_reconcile_at=datetime.now(UTC) + FIRST_RECONCILE_DELAY,
                metadata_={"receipt": rp_order.get("receipt")},
            )
        )

    async def _resolve_items(self, request: CheckoutRequest) -> list[dict]:
        raw_items: list[dict] = []
        for item in request.items:
            if item.custom_bouquet is not None:
                raw_items.append(await self._resolve_custom_bouquet(item))
                continue

            variant = None
            product = None
            if item.variant_id:
                variant = await self._products.get_variant_by_id(item.variant_id)
                if variant:
                    product = await self._products.get_by_id(variant.product_id)
            elif item.product_id:
                product = await self._products.get_by_id(item.product_id)
                if product:
                    variant = await self._products.get_default_variant(product.id)
            elif item.slug:
                product = await self._products.get_by_slug(item.slug)
                if product:
                    variant = await self._products.get_default_variant(product.id)

            if variant is None or product is None:
                raise CheckoutError(
                    "One of the items is no longer available.",
                    status_code=409,
                    code="item_unavailable",
                )
            meta = product.metadata_ or {}
            tax_rate = meta.get("tax_rate_bps")
            raw_items.append(
                {
                    "product_variant_id": str(variant.id),
                    "product_id": str(product.id),
                    "sku": variant.sku,
                    "product_name": product.name,
                    "variant_title": variant.title,
                    "quantity": item.quantity,
                    "unit_price_paise": variant.price_paise,
                    "tax_rate_bps": int(tax_rate) if tax_rate is not None else settings.default_gst_rate_bps,
                    "hsn_code": meta.get("hsn_code"),
                }
            )
        return raw_items

    async def _resolve_custom_bouquet(self, item: CheckoutItemIn) -> dict:
        """Re-price a made-to-order bouquet from the live option catalogue.

        The browser only ever sends which options were picked, so a tampered
        request can't change what the customer is charged — and a bouquet
        configured before an option was retired or repriced fails or repricess
        here rather than shipping at a stale price.
        """
        base = (
            await self._session.execute(BASE_BOUQUET_PRODUCT_SQL)
        ).mappings().first()
        if base is None:
            raise CheckoutError(
                "Custom bouquets aren't available right now.",
                status_code=503,
                code="builder_unavailable",
            )

        try:
            quote = await BouquetService(self._session).quote(item.custom_bouquet)
        except BouquetError as exc:
            raise CheckoutError(exc.message, status_code=exc.status_code, code=exc.code) from exc

        meta = base["metadata"] or {}
        tax_rate = meta.get("tax_rate_bps")
        return {
            "product_variant_id": str(base["variant_id"]),
            "product_id": str(base["id"]),
            "sku": base["sku"],
            "product_name": base["name"],
            # The summary rides on the line so the order, invoice and admin
            # panel all show what was actually ordered.
            "variant_title": quote.summary[:200],
            "quantity": item.quantity,
            "unit_price_paise": quote.total_paise,
            "tax_rate_bps": int(tax_rate) if tax_rate is not None else settings.default_gst_rate_bps,
            "hsn_code": meta.get("hsn_code"),
            "metadata": {"custom_bouquet": quote.model_dump(mode="json")},
        }

    async def _resolve_addresses(
        self, *, user_id: uuid.UUID | None, request: CheckoutRequest
    ) -> tuple[dict, dict | None]:
        shipping: dict | None = None
        if request.shipping_address:
            shipping = request.shipping_address.as_snapshot()
        elif request.address_id and user_id:
            shipping = await self._load_saved_address(request.address_id, user_id)
        if not shipping:
            raise CheckoutError(
                "A shipping address is required to place the order.",
                status_code=400,
                code="address_required",
            )
        billing = request.billing_address.as_snapshot() if request.billing_address else None
        return shipping, billing

    async def _load_saved_address(self, address_id: uuid.UUID, user_id: uuid.UUID) -> dict | None:
        result = await self._session.execute(
            text(
                """
                SELECT full_name, phone, line1, line2, landmark, city, state,
                       postal_code, country
                FROM public.user_addresses
                WHERE id = :id AND user_id = :uid AND deleted_at IS NULL
                """
            ),
            {"id": str(address_id), "uid": str(user_id)},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def _user_email(self, user_id: uuid.UUID | None) -> str | None:
        if user_id is None:
            return None
        result = await self._session.execute(
            text("SELECT email FROM identity.users WHERE id = :id"),
            {"id": str(user_id)},
        )
        row = result.first()
        return row[0] if row else None


#: payment_transactions.transaction_type for each terminal-ish state.
_TRANSACTION_TYPE: dict[PaymentStatus, str] = {
    PaymentStatus.AUTHORIZED: "authorization",
    PaymentStatus.CAPTURED: "capture",
    PaymentStatus.FAILED: "authorization",
    PaymentStatus.REFUNDED: "refund",
    PaymentStatus.PARTIALLY_REFUNDED: "refund",
}


def _normalise_method(method: str | None) -> str | None:
    if not method:
        return None
    allowed = {"upi", "card", "netbanking", "wallet", "emi"}
    return method if method in allowed else None
