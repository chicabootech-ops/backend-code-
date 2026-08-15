"""Customer-facing order queries: history, detail, cancel, invoice download."""

from __future__ import annotations

import uuid
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.lib.media import product_image_url
from app.storefront.models.commerce import Invoice, Order, OrderStatusHistory
from app.storefront.models.product import Product
from app.storefront.repositories.invoice_repository import InvoiceRepository
from app.storefront.repositories.order_repository import OrderRepository
from app.storefront.services.inventory_service import InventoryService
from app.storefront.schemas.order import (
    OrderInvoiceOut,
    OrderItemOut,
    OrderItemPreviewOut,
    OrderListItemOut,
    OrderListResponse,
    OrderOut,
    OrderStatusEventOut,
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

    #: How many thumbnails a list row shows before collapsing into "+N more".
    PREVIEW_LIMIT = 4

    async def list_orders(
        self, user_id: uuid.UUID, *, page: int = 1, page_size: int = 20
    ) -> OrderListResponse:
        orders, total = await self._orders.list_by_user(user_id, page=page, page_size=page_size)

        # Items were already being loaded here and thrown away except for the
        # quantity sum, which is why the list could only ever render an order
        # number. Collect them once, then resolve every product in ONE query
        # rather than per order.
        items_by_order = {o.id: await self._orders.get_items(o.id) for o in orders}
        media = await self._product_media(
            line for lines in items_by_order.values() for line in lines
        )

        items = []
        for order in orders:
            order_items = items_by_order[order.id]
            items.append(
                OrderListItemOut(
                    id=order.id,
                    order_number=order.order_number,
                    status=order.status,
                    payment_status=order.payment_status,
                    grand_total_paise=order.grand_total_paise,
                    item_count=sum(i.quantity for i in order_items),
                    created_at=order.created_at,
                    items_preview=[
                        OrderItemPreviewOut(
                            product_name=i.product_name,
                            image_url=media.get(i.product_id, (None, None))[0],
                            quantity=i.quantity,
                        )
                        for i in order_items[: self.PREVIEW_LIMIT]
                    ],
                )
            )
        return OrderListResponse(items=items, total=total, page=page, page_size=page_size)

    async def _product_media(
        self, lines: Iterable[Any]
    ) -> dict[uuid.UUID, tuple[str | None, str | None]]:
        """product_id -> (image_url, slug) for every product referenced.

        Batched deliberately: resolving media per line turns an order list into
        one query per item. Missing ids are simply absent — a deleted product
        must not break order history.
        """
        ids = {line.product_id for line in lines}
        if not ids:
            return {}
        rows = (
            await self._session.execute(
                select(Product.id, Product.metadata_, Product.slug).where(Product.id.in_(ids))
            )
        ).all()
        return {row[0]: (product_image_url(row[1]), row[2]) for row in rows}

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
        media = await self._product_media(items)
        history = (
            await self._session.execute(
                select(OrderStatusHistory)
                .where(OrderStatusHistory.order_id == order.id)
                .order_by(OrderStatusHistory.created_at)
            )
        ).scalars().all()
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
                    product_id=i.product_id,
                    product_name=i.product_name,
                    variant_title=i.variant_title,
                    sku=i.sku,
                    quantity=i.quantity,
                    unit_price_paise=i.unit_price_paise,
                    line_total_paise=i.line_total_paise,
                    hsn_code=i.hsn_code,
                    tax_rate_bps=i.tax_rate_bps,
                    image_url=media.get(i.product_id, (None, None))[0],
                    slug=media.get(i.product_id, (None, None))[1],
                )
                for i in items
            ],
            status_history=[
                OrderStatusEventOut(
                    from_status=h.from_status,
                    to_status=h.to_status,
                    changed_by_type=h.changed_by_type,
                    reason=h.reason,
                    created_at=h.created_at,
                )
                for h in history
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
