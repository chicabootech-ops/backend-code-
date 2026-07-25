"""SQLAlchemy models for the storefront checkout / order / payment / invoice flow.

These map 1:1 to the commerce.* tables created in migrations 000016 and 000017.
Prices are always stored in integer paise.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.storefront.db.base import Base


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_number: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("nextval('commerce.order_number_seq')"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    guest_email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    payment_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    fulfillment_status: Mapped[str] = mapped_column(Text, nullable=False, default="unfulfilled")
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="INR")
    subtotal_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    discount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tax_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    shipping_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    grand_total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    coupon_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    coupon_code: Mapped[str | None] = mapped_column(Text)
    shipping_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    billing_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    gstin: Mapped[str | None] = mapped_column(Text)
    customer_note: Mapped[str | None] = mapped_column(Text)
    admin_note: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.orders.id"), nullable=False
    )
    product_variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    variant_title: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tax_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    line_total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hsn_code: Mapped[str | None] = mapped_column(Text)
    tax_rate_bps: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderTaxLine(Base):
    __tablename__ = "order_tax_lines"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.orders.id"), nullable=False
    )
    tax_type: Mapped[str] = mapped_column(Text, nullable=False)  # cgst|sgst|igst|cess
    tax_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    taxable_amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.orders.id"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by_type: Mapped[str] = mapped_column(Text, nullable=False)  # system|customer|admin
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.orders.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(Text)
    provider_payment_id: Mapped[str | None] = mapped_column(Text)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="INR")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="created")
    method: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.payments.id"), nullable=False
    )
    transaction_type: Mapped[str] = mapped_column(Text, nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(Text)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.orders.id"), nullable=False
    )
    invoice_number: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("nextval('commerce.invoice_number_seq')"),
    )
    pdf_r2_key: Mapped[str | None] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
