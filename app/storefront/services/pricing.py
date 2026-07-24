"""GST pricing engine for checkout.

Indian retail convention: catalog prices are GST-inclusive. We back-calculate the
taxable (net) value and the tax portion for each line, then split the tax into
CGST + SGST for intra-state sales or IGST for inter-state sales, based on the
shipping state code versus the company (seller) state code.

All money is integer paise. Rounding is applied per-line, then aggregated, so the
printed invoice always reconciles to the amount charged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings


@dataclass
class PricedItem:
    product_variant_id: str
    product_id: str
    sku: str
    product_name: str
    variant_title: str
    quantity: int
    hsn_code: str | None
    tax_rate_bps: int
    unit_price_paise: int  # GST-inclusive unit price shown to the customer
    line_gross_paise: int  # unit_price * quantity (inclusive)
    taxable_paise: int  # net value before tax
    tax_paise: int  # GST portion within the gross


@dataclass
class TaxLine:
    tax_type: str  # cgst | sgst | igst
    tax_rate_bps: int
    taxable_amount_paise: int
    tax_amount_paise: int


@dataclass
class PricedOrder:
    items: list[PricedItem]
    tax_lines: list[TaxLine] = field(default_factory=list)
    subtotal_paise: int = 0  # sum of taxable (net) values
    discount_paise: int = 0
    tax_paise: int = 0
    shipping_paise: int = 0
    grand_total_paise: int = 0
    intra_state: bool = True


def _split_inclusive(gross_paise: int, rate_bps: int) -> tuple[int, int]:
    """Split a GST-inclusive gross amount into (taxable, tax) in paise."""
    if rate_bps <= 0:
        return gross_paise, 0
    taxable = round(gross_paise * 10000 / (10000 + rate_bps))
    tax = gross_paise - taxable
    return taxable, tax


def price_order(
    raw_items: list[dict],
    *,
    shipping_state_code: str | None,
) -> PricedOrder:
    """Compute an itemised, GST-aware price breakdown.

    Each ``raw_items`` entry must contain: product_variant_id, product_id, sku,
    product_name, variant_title, quantity, unit_price_paise, tax_rate_bps, hsn_code.
    """
    company_code = (settings.company_state_code or "").strip()
    ship_code = (shipping_state_code or "").strip()
    # Default to intra-state when we cannot determine the buyer's state code.
    intra_state = (not company_code) or (not ship_code) or (ship_code == company_code)

    priced: list[PricedItem] = []
    for it in raw_items:
        qty = int(it["quantity"])
        unit = int(it["unit_price_paise"])
        rate = int(it.get("tax_rate_bps") or 0)
        gross = unit * qty
        if settings.prices_include_gst:
            taxable, tax = _split_inclusive(gross, rate)
        else:
            taxable = gross
            tax = round(gross * rate / 10000)
        priced.append(
            PricedItem(
                product_variant_id=str(it["product_variant_id"]),
                product_id=str(it["product_id"]),
                sku=it["sku"],
                product_name=it["product_name"],
                variant_title=it["variant_title"],
                quantity=qty,
                hsn_code=it.get("hsn_code"),
                tax_rate_bps=rate,
                unit_price_paise=unit,
                line_gross_paise=gross,
                taxable_paise=taxable,
                tax_paise=tax,
            )
        )

    subtotal = sum(p.taxable_paise for p in priced)
    tax_total = sum(p.tax_paise for p in priced)

    shipping = _shipping_paise(sum(p.line_gross_paise for p in priced))
    grand_total = subtotal + tax_total + shipping

    return PricedOrder(
        items=priced,
        tax_lines=_build_tax_lines(priced, intra_state=intra_state),
        subtotal_paise=subtotal,
        discount_paise=0,
        tax_paise=tax_total,
        shipping_paise=shipping,
        grand_total_paise=grand_total,
        intra_state=intra_state,
    )


def _shipping_paise(goods_gross_paise: int) -> int:
    flat = max(0, int(settings.shipping_flat_paise))
    threshold = int(settings.free_shipping_threshold_paise)
    if flat == 0:
        return 0
    if threshold > 0 and goods_gross_paise >= threshold:
        return 0
    return flat


def _build_tax_lines(items: list[PricedItem], *, intra_state: bool) -> list[TaxLine]:
    # Aggregate taxable + tax by GST rate, ignoring exempt (0%) lines.
    by_rate: dict[int, list[int]] = {}
    for it in items:
        if it.tax_rate_bps <= 0:
            continue
        bucket = by_rate.setdefault(it.tax_rate_bps, [0, 0])
        bucket[0] += it.taxable_paise
        bucket[1] += it.tax_paise

    lines: list[TaxLine] = []
    for rate in sorted(by_rate):
        taxable, tax = by_rate[rate]
        if intra_state:
            half = rate // 2
            cgst = tax // 2
            sgst = tax - cgst
            lines.append(TaxLine("cgst", half, taxable, cgst))
            lines.append(TaxLine("sgst", half, taxable, sgst))
        else:
            lines.append(TaxLine("igst", rate, taxable, tax))
    return lines
