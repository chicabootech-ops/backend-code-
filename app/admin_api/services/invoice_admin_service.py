"""Admin invoice browsing. Reuses the storefront invoice engine for rendering."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import NotFoundError
from app.admin_api.schemas.invoice import (
    AdminInvoiceDetail,
    AdminInvoiceItemOut,
    AdminInvoiceListItem,
    AdminInvoiceListResponse,
    AdminInvoiceTaxOut,
)
from app.storefront.models.commerce import Invoice, Order
from app.storefront.repositories.order_repository import OrderRepository
from app.storefront.services.invoice_service import InvoiceService


class InvoiceAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._invoice_service = InvoiceService(session)

    async def list_invoices(
        self, *, page: int = 1, page_size: int = 20, search: str | None = None
    ) -> AdminInvoiceListResponse:
        stmt = select(Invoice, Order).join(Order, Invoice.order_id == Order.id)
        if search:
            term = search.strip()
            filters = []
            if term.isdigit():
                filters.append(Invoice.invoice_number == int(term))
                filters.append(Order.order_number == int(term))
            filters.append(Order.shipping_address["full_name"].astext.ilike(f"%{term}%"))
            filters.append(Order.guest_email.ilike(f"%{term}%"))
            stmt = stmt.where(or_(*filters))

        total = (
            await self._session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()

        rows = (
            await self._session.execute(
                stmt.order_by(Invoice.invoice_number.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()

        items = [
            AdminInvoiceListItem(
                id=inv.id,
                invoice_number=inv.invoice_number,
                order_id=order.id,
                order_number=order.order_number,
                customer_name=(order.shipping_address or {}).get("full_name"),
                grand_total_paise=order.grand_total_paise,
                payment_status=order.payment_status,
                order_status=order.status,
                has_pdf=bool(inv.pdf_r2_key),
                issued_at=inv.issued_at,
            )
            for inv, order in rows
        ]
        return AdminInvoiceListResponse(
            items=items,
            meta={
                "page": page,
                "page_size": page_size,
                "total": int(total),
                "total_pages": max(1, (int(total) + page_size - 1) // page_size),
            },
        )

    async def get_invoice(self, invoice_id: uuid.UUID) -> AdminInvoiceDetail:
        inv, order = await self._load(invoice_id)
        items = await self._orders.get_items(order.id)
        tax_lines = await self._orders.get_tax_lines(order.id)
        return AdminInvoiceDetail(
            id=inv.id,
            invoice_number=inv.invoice_number,
            issued_at=inv.issued_at,
            has_pdf=bool(inv.pdf_r2_key),
            order_id=order.id,
            order_number=order.order_number,
            order_status=order.status,
            payment_status=order.payment_status,
            currency=order.currency,
            subtotal_paise=order.subtotal_paise,
            discount_paise=order.discount_paise,
            tax_paise=order.tax_paise,
            shipping_paise=order.shipping_paise,
            grand_total_paise=order.grand_total_paise,
            gstin=order.gstin,
            shipping_address=order.shipping_address or {},
            billing_address=order.billing_address or {},
            created_at=order.created_at,
            items=[
                AdminInvoiceItemOut(
                    product_name=i.product_name,
                    variant_title=i.variant_title,
                    sku=i.sku,
                    hsn_code=i.hsn_code,
                    tax_rate_bps=i.tax_rate_bps,
                    quantity=i.quantity,
                    unit_price_paise=i.unit_price_paise,
                    tax_paise=i.tax_paise,
                    line_total_paise=i.line_total_paise,
                )
                for i in items
            ],
            tax_lines=[
                AdminInvoiceTaxOut(
                    tax_type=t.tax_type,
                    tax_rate_bps=t.tax_rate_bps,
                    taxable_amount_paise=t.taxable_amount_paise,
                    tax_amount_paise=t.tax_amount_paise,
                )
                for t in tax_lines
            ],
        )

    async def render_pdf(self, invoice_id: uuid.UUID) -> tuple[bytes, str]:
        inv, order = await self._load(invoice_id)
        pdf = await self._invoice_service.render_pdf_bytes(order, inv)
        return pdf, f"chicaboo-invoice-{order.order_number}.pdf"

    async def regenerate(self, invoice_id: uuid.UUID) -> AdminInvoiceDetail:
        inv, order = await self._load(invoice_id)
        await self._invoice_service.ensure_invoice(order, regenerate=True)
        await self._session.commit()
        return await self.get_invoice(invoice_id)

    async def _load(self, invoice_id: uuid.UUID) -> tuple[Invoice, Order]:
        row = (
            await self._session.execute(
                select(Invoice, Order)
                .join(Order, Invoice.order_id == Order.id)
                .where(Invoice.id == invoice_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("Invoice not found")
        return row[0], row[1]
