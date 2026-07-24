"""Invoice generation: build the document, render a PDF, store it in R2."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.storefront.lib import r2_storage
from app.storefront.lib.invoice_pdf import (
    InvoiceData,
    InvoiceLine,
    InvoiceParty,
    InvoiceTaxLine,
    render_invoice_pdf,
)
from app.storefront.lib.num_to_words import rupees_in_words
from app.storefront.models.commerce import Invoice, Order, OrderItem, OrderTaxLine
from app.storefront.repositories.invoice_repository import InvoiceRepository
from app.storefront.repositories.order_repository import OrderRepository

logger = logging.getLogger(__name__)

_TAX_LABELS = {"cgst": "CGST", "sgst": "SGST", "igst": "IGST", "cess": "Cess"}


def _address_lines(addr: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key in ("line1", "line2", "landmark"):
        val = (addr.get(key) or "").strip()
        if val:
            parts.append(val)
    city = (addr.get("city") or "").strip()
    state = (addr.get("state") or "").strip()
    pin = (addr.get("postal_code") or addr.get("pincode") or "").strip()
    locality = ", ".join(p for p in (city, state) if p)
    if pin:
        locality = f"{locality} - {pin}" if locality else pin
    if locality:
        parts.append(locality)
    return parts


def _party_from_address(addr: dict[str, Any], *, fallback_name: str) -> InvoiceParty:
    addr = addr or {}
    return InvoiceParty(
        name=(addr.get("full_name") or fallback_name or "Customer").strip(),
        address_lines=_address_lines(addr),
        phone=(addr.get("phone") or None),
        state=(addr.get("state") or None),
    )


def _seller_party() -> InvoiceParty:
    lines = [
        settings.company_address_line1,
        settings.company_address_line2,
        ", ".join(
            p
            for p in (
                settings.company_city,
                settings.company_state,
                settings.company_postal_code,
            )
            if p
        ),
    ]
    return InvoiceParty(
        name=settings.company_legal_name,
        address_lines=[ln for ln in lines if ln],
        state=settings.company_state or None,
        gstin=settings.company_gstin or None,
    )


def _invoice_number_str(number: int) -> str:
    fy = _financial_year(datetime.now(timezone.utc))
    prefix = settings.invoice_prefix or "CAB"
    return f"{prefix}/{fy}/{number:05d}"


def _financial_year(dt: datetime) -> str:
    # Indian FY runs Apr-Mar, e.g. 2026-27
    year = dt.year
    if dt.month < 4:
        return f"{year - 1}-{str(year)[-2:]}"
    return f"{year}-{str(year + 1)[-2:]}"


class InvoiceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._invoices = InvoiceRepository(session)
        self._orders = OrderRepository(session)

    async def ensure_invoice(
        self,
        order: Order,
        *,
        payment_method: str = "Prepaid",
        regenerate: bool = False,
    ) -> Invoice:
        """Create the invoice row + PDF for an order if it does not exist yet."""
        invoice = await self._invoices.get_by_order(order.id)
        if invoice and invoice.pdf_r2_key and not regenerate:
            return invoice
        if invoice is None:
            invoice = await self._invoices.create(order.id)

        items = await self._orders.get_items(order.id)
        tax_lines = await self._orders.get_tax_lines(order.id)
        pdf_bytes = self._render(order, items, tax_lines, invoice, payment_method=payment_method)

        key = f"{settings.invoice_r2_prefix}/{invoice.invoice_number}-{order.order_number}.pdf"
        if r2_storage.put_bytes(key, pdf_bytes, content_type="application/pdf"):
            await self._invoices.set_pdf_key(invoice, key)
        else:
            logger.warning("Invoice %s PDF could not be stored in R2", invoice.invoice_number)
        return invoice

    async def render_pdf_bytes(self, order: Order, invoice: Invoice, *, payment_method: str = "Prepaid") -> bytes:
        # Prefer the stored PDF; regenerate on the fly if storage is unavailable.
        if invoice.pdf_r2_key:
            stored = r2_storage.get_bytes(invoice.pdf_r2_key)
            if stored:
                return stored
        items = await self._orders.get_items(order.id)
        tax_lines = await self._orders.get_tax_lines(order.id)
        return self._render(order, items, tax_lines, invoice, payment_method=payment_method)

    def _render(
        self,
        order: Order,
        items: list[OrderItem],
        tax_lines: list[OrderTaxLine],
        invoice: Invoice,
        *,
        payment_method: str,
    ) -> bytes:
        seller = _seller_party()
        billing = order.billing_address or order.shipping_address or {}
        shipping = order.shipping_address or order.billing_address or {}
        fallback = order.guest_email or "Customer"

        inv_lines = [
            InvoiceLine(
                name=(
                    f"{it.product_name} - {it.variant_title}"
                    if it.variant_title and it.variant_title.lower() != "default"
                    else it.product_name
                ),
                hsn=it.hsn_code,
                tax_rate_bps=it.tax_rate_bps or 0,
                quantity=it.quantity,
                unit_price_paise=it.unit_price_paise,
                taxable_paise=it.line_total_paise - it.tax_paise,
                tax_paise=it.tax_paise,
                total_paise=it.line_total_paise,
            )
            for it in items
        ]
        inv_tax = [
            InvoiceTaxLine(
                label=f"{_TAX_LABELS.get(t.tax_type, t.tax_type.upper())} {t.tax_rate_bps / 100:g}%",
                taxable_paise=t.taxable_amount_paise,
                tax_paise=t.tax_amount_paise,
            )
            for t in tax_lines
        ]

        has_gst = bool(seller.gstin) and bool(inv_tax)
        title = "Tax Invoice" if has_gst else "Bill of Supply"

        data = InvoiceData(
            title=title,
            invoice_number=_invoice_number_str(invoice.invoice_number),
            invoice_date=(invoice.issued_at or datetime.now(timezone.utc)).strftime("%d-%m-%Y"),
            order_number=f"CAB{order.order_number}",
            order_date=order.created_at.strftime("%d-%m-%Y"),
            payment_method=payment_method,
            seller=seller,
            bill_to=_party_from_address(billing, fallback_name=fallback),
            ship_to=_party_from_address(shipping, fallback_name=fallback),
            lines=inv_lines,
            tax_lines=inv_tax,
            subtotal_paise=order.subtotal_paise,
            discount_paise=order.discount_paise,
            shipping_paise=order.shipping_paise,
            tax_paise=order.tax_paise,
            grand_total_paise=order.grand_total_paise,
            amount_in_words=rupees_in_words(order.grand_total_paise),
            seller_pan=settings.company_pan or None,
            seller_email=settings.company_email or None,
            seller_phone=settings.company_phone or None,
        )
        return render_invoice_pdf(data)
