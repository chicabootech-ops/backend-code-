from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response

from app.admin_api.dependencies import CurrentAdmin, InvoiceAdminServiceDep
from app.admin_api.schemas.invoice import AdminInvoiceDetail, AdminInvoiceListResponse

router = APIRouter(prefix="/admin/invoices", tags=["admin-invoices"])


@router.get("", response_model=AdminInvoiceListResponse)
async def list_invoices(
    _admin: CurrentAdmin,
    service: InvoiceAdminServiceDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
) -> AdminInvoiceListResponse:
    return await service.list_invoices(page=page, page_size=page_size, search=search)


@router.get("/{invoice_id}", response_model=AdminInvoiceDetail)
async def get_invoice(
    invoice_id: uuid.UUID, _admin: CurrentAdmin, service: InvoiceAdminServiceDep
) -> AdminInvoiceDetail:
    return await service.get_invoice(invoice_id)


@router.get("/{invoice_id}/pdf")
async def download_invoice_pdf(
    invoice_id: uuid.UUID, _admin: CurrentAdmin, service: InvoiceAdminServiceDep
) -> Response:
    pdf, filename = await service.render_pdf(invoice_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/{invoice_id}/regenerate", response_model=AdminInvoiceDetail)
async def regenerate_invoice(
    invoice_id: uuid.UUID, _admin: CurrentAdmin, service: InvoiceAdminServiceDep
) -> AdminInvoiceDetail:
    return await service.regenerate(invoice_id)
