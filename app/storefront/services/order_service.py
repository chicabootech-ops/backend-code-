"""Customer-facing order queries: history, detail, cancel, invoice download."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.models.commerce import Invoice, Order
from app.storefront.repositories.invoice_repository import InvoiceRepository
from app.storefront.repositories.order_repository import OrderRepository
from app.storefront.services.inventory_service import InventoryService
from app.storefront.schemas.order import (
    OrderInvoiceOut,
    OrderItemOut,
    OrderListItemOut,
    OrderListResponse,
    OrderOut,
)
from app.storefront.services.invoice_service import InvoiceService


class OrderError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "order_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._invoices = InvoiceRepository(session)
        self._invoice_service = InvoiceService(session)
        self._inventory = InventoryService(session)

    async def list_orders(
        self, user_id: uuid.UUID, *, page: int = 1, page_size: int = 20
    ) -> OrderListResponse:
        orders, total = await self._orders.list_by_user(user_id, page=page, page_size=page_size)
        items = []
        for order in orders:
            order_items = await self._orders.get_items(order.id)
            items.append(
                OrderListItemOut(
                    id=order.id,
                    order_number=order.order_number,
                    status=order.status,
                    payment_status=order.payment_status,
                    grand_total_paise=order.grand_total_paise,
                    item_count=sum(i.quantity for i in order_items),
                    created_at=order.created_at,
                )
            )
        return OrderListResponse(items=items, total=total, page=page, page_size=page_size)

    async def get_order(self, order_id: uuid.UUID, user_id: uuid.UUID) -> OrderOut:
        order = await self._require_order(order_id, user_id)
        return await self._to_out(order)

    async def cancel_order(
        self, order_id: uuid.UUID, user_id: uuid.UUID, *, reason: str | None
    ) -> OrderOut:
        order = await self._require_order(order_id, user_id)
        if order.status in ("shipped", "delivered", "completed", "cancelled", "refunded", "returned"):
            raise OrderError(
                f"Order cannot be cancelled once it is {order.status}.",
                status_code=409,
                code="not_cancellable",
            )
        previous_status = order.status
        await self._orders.cancel(order, reason=reason)
        await self._orders.add_status_history(
            order.id,
            to_status="cancelled",
            from_status=previous_status,
            changed_by_type="customer",
            changed_by_id=user_id,
            reason=reason,
        )
        # Return any reserved stock to the pool.
        await self._inventory.release(order.id)
        await self._session.commit()
        return await self._to_out(order)

    async def get_invoice(
        self, order_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[bytes, str]:
        order = await self._require_order(order_id, user_id)
        if order.payment_status != "paid":
            raise OrderError(
                "Invoice is available once payment is completed.",
                status_code=409,
                code="invoice_not_ready",
            )
        invoice = await self._invoices.get_by_order(order.id)
        if invoice is None:
            invoice = await self._invoice_service.ensure_invoice(order)
            await self._session.commit()
        pdf = await self._invoice_service.render_pdf_bytes(order, invoice)
        filename = f"chicaboo-invoice-{order.order_number}.pdf"
        return pdf, filename

    async def _require_order(self, order_id: uuid.UUID, user_id: uuid.UUID) -> Order:
        order = await self._orders.get_by_id(order_id, user_id=user_id)
        if order is None:
            raise OrderError("Order not found.", status_code=404, code="order_not_found")
        return order

    async def _to_out(self, order: Order) -> OrderOut:
        items = await self._orders.get_items(order.id)
        invoice: Invoice | None = await self._invoices.get_by_order(order.id)
        return OrderOut(
            id=order.id,
            order_number=order.order_number,
            status=order.status,
            payment_status=order.payment_status,
            fulfillment_status=order.fulfillment_status,
            currency=order.currency,
            subtotal_paise=order.subtotal_paise,
            discount_paise=order.discount_paise,
            tax_paise=order.tax_paise,
            shipping_paise=order.shipping_paise,
            grand_total_paise=order.grand_total_paise,
            shipping_address=order.shipping_address or {},
            billing_address=order.billing_address or {},
            customer_note=order.customer_note,
            created_at=order.created_at,
            items=[
                OrderItemOut(
                    product_name=i.product_name,
                    variant_title=i.variant_title,
                    sku=i.sku,
                    quantity=i.quantity,
                    unit_price_paise=i.unit_price_paise,
                    line_total_paise=i.line_total_paise,
                    hsn_code=i.hsn_code,
                    tax_rate_bps=i.tax_rate_bps,
                )
                for i in items
            ],
            invoice=(
                OrderInvoiceOut(
                    invoice_number=invoice.invoice_number,
                    has_pdf=bool(invoice.pdf_r2_key),
                    issued_at=invoice.issued_at,
                )
                if invoice
                else None
            ),
        )
