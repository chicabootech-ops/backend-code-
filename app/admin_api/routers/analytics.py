from fastapi import APIRouter
from sqlalchemy import text

from app.admin_api.dependencies import CurrentAdmin, DbSession

router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"])


@router.get("/revenue")
async def revenue_report(_admin: CurrentAdmin, db: DbSession) -> dict:
    result = await db.execute(
        text(
            """
            SELECT created_at::date AS day,
                   COALESCE(SUM(grand_total_paise), 0)::bigint AS revenue_paise,
                   COUNT(*)::int AS order_count
            FROM commerce.orders
            WHERE payment_status = 'paid'
              AND created_at >= CURRENT_DATE - INTERVAL '29 days'
            GROUP BY created_at::date
            ORDER BY day
            """
        )
    )
    return {"items": [dict(row) for row in result.mappings().all()]}


@router.get("/top-products")
async def top_selling_products(_admin: CurrentAdmin, db: DbSession) -> dict:
    result = await db.execute(
        text(
            """
            SELECT oi.product_id, MAX(oi.product_name) AS product_name,
                   SUM(oi.quantity)::bigint AS quantity
            FROM commerce.order_items oi
            JOIN commerce.orders o ON o.id = oi.order_id
            WHERE o.payment_status = 'paid'
            GROUP BY oi.product_id
            ORDER BY quantity DESC, product_name
            LIMIT 10
            """
        )
    )
    return {"items": [dict(row) for row in result.mappings().all()]}
