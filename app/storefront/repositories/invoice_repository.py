from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.models.commerce import Invoice


class InvoiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_order(self, order_id: uuid.UUID) -> Invoice | None:
        result = await self._session.execute(
            select(Invoice).where(Invoice.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def create(self, order_id: uuid.UUID) -> Invoice:
        invoice = Invoice(order_id=order_id)
        self._session.add(invoice)
        await self._session.flush()
        await self._session.refresh(invoice)
        return invoice

    async def set_pdf_key(self, invoice: Invoice, key: str) -> None:
        invoice.pdf_r2_key = key
        await self._session.flush()
