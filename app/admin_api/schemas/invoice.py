from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AdminInvoiceListItem(BaseModel):
    id: UUID
    invoice_number: int
    order_id: UUID
    order_number: int
    customer_name: str | None = None
    grand_total_paise: int
    payment_status: str
    order_status: str
    has_pdf: bool
    issued_at: datetime


class AdminInvoiceListResponse(BaseModel):
    items: list[AdminInvoiceListItem]
    meta: dict


class AdminInvoiceItemOut(BaseModel):
    product_name: str
    variant_title: str
    sku: str
    hsn_code: str | None = None
    tax_rate_bps: int | None = None
    quantity: int
    unit_price_paise: int
    tax_paise: int
    line_total_paise: int


class AdminInvoiceTaxOut(BaseModel):
    tax_type: str
    tax_rate_bps: int
    taxable_amount_paise: int
    tax_amount_paise: int


class AdminInvoiceDetail(BaseModel):
    id: UUID
    invoice_number: int
    issued_at: datetime
    has_pdf: bool
    order_id: UUID
    order_number: int
    order_status: str
    payment_status: str
    currency: str
    subtotal_paise: int
    discount_paise: int
    tax_paise: int
    shipping_paise: int
    grand_total_paise: int
    gstin: str | None = None
    shipping_address: dict = Field(default_factory=dict)
    billing_address: dict = Field(default_factory=dict)
    created_at: datetime
    items: list[AdminInvoiceItemOut] = Field(default_factory=list)
    tax_lines: list[AdminInvoiceTaxOut] = Field(default_factory=list)
