"""SQLAlchemy models for admin service."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.categories.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    image_r2_key: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="category")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    path: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Product(Base):
    __tablename__ = "products"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    primary_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.categories.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    short_description: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductVariant(Base):
    __tablename__ = "product_variants"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.products.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    option_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    price_paise: Mapped[int] = mapped_column(nullable=False)
    compare_at_price_paise: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = {"schema": "admin"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("admin.roles.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = {"schema": "admin"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class CustomerUser(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    phone_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    customer_number: Mapped[int] = mapped_column(nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = {"schema": "public"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), primary_key=True
    )
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    gender: Mapped[str | None] = mapped_column(Text)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    loyalty_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserAddress(Base):
    __tablename__ = "user_addresses"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    line1: Mapped[str] = mapped_column(Text, nullable=False)
    line2: Mapped[str | None] = mapped_column(Text)
    landmark: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    postal_code: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(Text, nullable=False, default="IN")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    address_type: Mapped[str] = mapped_column(Text, nullable=False, default="shipping")
    custom_label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=False, unique=True
    )
    email_marketing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sms_marketing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    preferred_language: Mapped[str] = mapped_column(String, nullable=False, default="en")
    currency: Mapped[str] = mapped_column(String, nullable=False, default="INR")
    push_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    analytics_tracking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    order_updates_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    order_updates_sms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    order_number: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    guest_email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    payment_status: Mapped[str] = mapped_column(Text, nullable=False)
    fulfillment_status: Mapped[str] = mapped_column(Text, nullable=False)
    grand_total_paise: Mapped[int] = mapped_column(nullable=False, default=0)
    shipping_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    admin_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    __table_args__ = {"schema": "commerce"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commerce.orders.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
