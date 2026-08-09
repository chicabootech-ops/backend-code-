"""Checkout + Razorpay payment orchestration.

Flow:
  1. create_checkout  -> validate items, price with GST, create a pending order,
                          create a Razorpay order, return checkout params.
  2. verify_payment   -> verify the browser signature, capture the payment,
                          mark the order paid, generate the invoice, email it.
  3. handle_webhook   -> same terminal state, driven by Razorpay's server webhook
                          (idempotent; the source of truth for reconciliation).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.events.bus import get_event_bus
from app.events.types import EventType
from app.storefront.lib.razorpay_client import PaymentGatewayError, RazorpayClient
from app.storefront.models.commerce import Order, OrderItem, OrderTaxLine, Payment
from app.storefront.repositories.invoice_repository import InvoiceRepository
from app.storefront.repositories.order_repository import OrderRepository
from app.storefront.repositories.payment_repository import PaymentRepository
from app.storefront.repositories.product_repository import ProductRepository
from app.storefront.schemas.order import CheckoutRequest
from app.storefront.schemas.payment import (
    CheckoutResponse,
    PaymentStatusOut,
    RazorpayCheckoutOut,
    VerifyPaymentRequest,
)
from app.storefront.services.coupon_service import CouponError, CouponService
from app.storefront.services.inventory_service import InventoryService, OutOfStockError
from app.storefront.services.invoice_service import InvoiceService
from app.storefront.services.pricing import price_order

logger = logging.getLogger(__name__)


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
        # order + Razorpay order instead of creating duplicates.
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

        # Apply a coupon if provided (validates expiry / usage limits / min order).
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

        # Reserve stock (opt-in per variant). Raises within this transaction, so a
        # stock shortfall rolls the whole checkout back — no order/reservation left.
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

        # Create the Razorpay order
        try:
            rp_order = await self._razorpay.create_order(
                amount_paise=final_total,
                receipt=f"cab-{order.order_number}",
                notes={"order_id": str(order.id), "order_number": str(order.order_number)},
            )
        except PaymentGatewayError as exc:
            raise CheckoutError(exc.message, status_code=502, code=exc.code) from exc

        attempt = await self._payments.next_attempt_number(order.id)
        await self._payments.create_payment(
            Payment(
                order_id=order.id,
                user_id=user_id,
                attempt_number=attempt,
                provider="razorpay",
                provider_order_id=rp_order.get("id"),
                amount_paise=final_total,
                currency="INR",
                status="created",
                metadata_={"receipt": rp_order.get("receipt")},
            )
        )
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
    # 2. Verify (browser callback)
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

        if payment.status == "captured" and order.payment_status == "paid":
            return await self._status_out(order, payment)

        valid = self._razorpay.verify_checkout_signature(
            order_id=payload.razorpay_order_id,
            payment_id=payload.razorpay_payment_id,
            signature=payload.razorpay_signature,
        )
        if not valid:
            await self._payments.mark_status(
                payment, status="failed", failure_reason="signature_verification_failed"
            )
            await self._payments.add_transaction(
                payment.id,
                transaction_type="authorization",
                status="failed",
                amount_paise=payment.amount_paise,
                raw_payload={"reason": "signature_mismatch"},
            )
            await self._session.commit()
            await get_event_bus().publish(
                EventType.PAYMENT_FAILED,
                {
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                    "reason": "signature_verification_failed",
                },
            )
            raise CheckoutError(
                "Payment could not be verified.", status_code=400, code="signature_invalid"
            )

        await self._capture(order, payment, provider_payment_id=payload.razorpay_payment_id)
        await self._session.commit()
        await self._post_payment(order)
        return await self._status_out(order, payment)

    # ------------------------------------------------------------------ #
    # 3. Webhook (server-to-server, source of truth)
    # ------------------------------------------------------------------ #
    async def handle_webhook(self, *, raw_body: bytes, signature: str, event: dict[str, Any]) -> None:
        if not self._razorpay.verify_webhook_signature(raw_body=raw_body, signature=signature):
            raise CheckoutError("Invalid webhook signature.", status_code=400, code="invalid_signature")

        event_type = event.get("event", "")
        payment_entity = (
            event.get("payload", {}).get("payment", {}).get("entity", {}) or {}
        )
        provider_order_id = payment_entity.get("order_id")
        provider_payment_id = payment_entity.get("id")
        method = payment_entity.get("method")

        if event_type not in ("payment.captured", "payment.authorized", "order.paid"):
            logger.info("Ignoring Razorpay webhook event=%s", event_type)
            return
        if not provider_order_id:
            logger.warning("Webhook %s missing order_id", event_type)
            return

        payment = await self._payments.get_by_provider_order_id(provider_order_id)
        if payment is None:
            logger.warning("Webhook for unknown order_id=%s", provider_order_id)
            return
        order = await self._orders.get_by_id(payment.order_id)
        if order is None:
            return
        if payment.status == "captured" and order.payment_status == "paid":
            return  # already reconciled

        await self._capture(
            order, payment, provider_payment_id=provider_payment_id, method=method, raw=payment_entity
        )
        await self._session.commit()
        await self._post_payment(order)

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

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _capture(
        self,
        order: Order,
        payment: Payment,
        *,
        provider_payment_id: str | None,
        method: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        await self._payments.mark_status(
            payment,
            status="captured",
            provider_payment_id=provider_payment_id,
            method=_normalise_method(method),
        )
        await self._payments.add_transaction(
            payment.id,
            transaction_type="capture",
            status="success",
            amount_paise=payment.amount_paise,
            provider_transaction_id=provider_payment_id,
            raw_payload=raw or {},
        )
        await self._orders.set_payment_status(order, payment_status="paid", status="confirmed")
        await self._orders.add_status_history(
            order.id,
            to_status="confirmed",
            from_status="pending",
            reason="Payment captured",
        )
        # Commit the reserved stock and record coupon redemption.
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
                "provider_payment_id": provider_payment_id,
                "method": _normalise_method(method),
            },
        )

    async def _post_payment(self, order: Order) -> None:
        """After commit: generate the invoice and email it to the customer (best-effort)."""
        invoice = None
        pdf_bytes: bytes | None = None
        try:
            invoice = await self._invoice_service.ensure_invoice(order, payment_method="Prepaid")
            await self._session.commit()
            pdf_bytes = await self._invoice_service.render_pdf_bytes(order, invoice)
        except Exception:  # noqa: BLE001
            logger.exception("Invoice generation failed for order %s", order.order_number)
            await self._session.rollback()

        if self._email is None:
            return
        to_email = order.guest_email or await self._user_email(order.user_id)
        if not to_email:
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
        except Exception:  # noqa: BLE001
            logger.exception("Order email failed for order %s", order.order_number)

    async def _status_out(self, order: Order, payment: Payment | None) -> PaymentStatusOut:
        invoice = await self._invoices.get_by_order(order.id)
        return PaymentStatusOut(
            order_id=order.id,
            order_number=order.order_number,
            payment_status=order.payment_status,
            order_status=order.status,
            provider=payment.provider if payment else None,
            provider_payment_id=payment.provider_payment_id if payment else None,
            amount_paise=order.grand_total_paise,
            invoice_number=invoice.invoice_number if invoice else None,
        )

    async def _resolve_items(self, request: CheckoutRequest) -> list[dict]:
        raw_items: list[dict] = []
        for item in request.items:
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


def _normalise_method(method: str | None) -> str | None:
    if not method:
        return None
    allowed = {"upi", "card", "netbanking", "wallet", "emi"}
    return method if method in allowed else None
