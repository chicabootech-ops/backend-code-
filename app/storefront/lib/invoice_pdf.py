"""GST Tax Invoice PDF renderer (fpdf2).

Layout mirrors a standard Indian "Tax Invoice / Bill of Supply": seller + GSTIN
header, Bill To / Ship To blocks, order/invoice meta, an itemised table with HSN
and tax, a GST tax break-up (CGST/SGST or IGST), grand total and an authorised
signatory footer — rebranded for Chic A Boo (gold on ivory).

Amounts are passed in paise and printed as "Rs. 1,234.00" (core PDF fonts are
latin-1, so the Rupee glyph is rendered as the widely-used "Rs." prefix).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fpdf import FPDF

# Brand palette (from the logo / globals.css)
GOLD = (193, 155, 84)      # #c19b54
BRONZE = (148, 106, 43)    # #946a2b
INK = (60, 50, 30)
MUTED = (120, 110, 95)
LINE = (210, 198, 178)
IVORY = (245, 239, 235)    # #f5efeb

CONTENT_W = 186.0  # A4 width (210) minus 12mm margins each side


@dataclass
class InvoiceParty:
    name: str
    address_lines: list[str] = field(default_factory=list)
    phone: str | None = None
    state: str | None = None
    gstin: str | None = None


@dataclass
class InvoiceLine:
    name: str
    hsn: str | None
    tax_rate_bps: int
    quantity: int
    unit_price_paise: int
    taxable_paise: int
    tax_paise: int
    total_paise: int


@dataclass
class InvoiceTaxLine:
    label: str  # e.g. "CGST 9%", "IGST 18%"
    taxable_paise: int
    tax_paise: int


@dataclass
class InvoiceData:
    title: str
    invoice_number: str
    invoice_date: str
    order_number: str
    order_date: str
    payment_method: str
    seller: InvoiceParty
    bill_to: InvoiceParty
    ship_to: InvoiceParty
    lines: list[InvoiceLine]
    tax_lines: list[InvoiceTaxLine]
    subtotal_paise: int
    discount_paise: int
    shipping_paise: int
    tax_paise: int
    grand_total_paise: int
    amount_in_words: str
    seller_pan: str | None = None
    seller_email: str | None = None
    seller_phone: str | None = None


def _money(paise: int) -> str:
    return f"Rs. {paise / 100:,.2f}"


def _num(paise: int) -> str:
    return f"{paise / 100:,.2f}"


class _InvoicePDF(FPDF):
    def __init__(self, data: InvoiceData) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._data = data
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(12, 12, 12)

    # --- header / footer -------------------------------------------------
    def header(self) -> None:
        d = self._data
        self.set_fill_color(*IVORY)
        self.rect(0, 0, 210, 30, style="F")
        self.set_xy(12, 8)
        self.set_text_color(*BRONZE)
        self.set_font("Helvetica", "B", 20)
        self.cell(120, 8, "CHIC A BOO", align="L")
        self.set_xy(12, 17)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*MUTED)
        self.cell(120, 5, "Handcrafted crochet blooms, keepsakes & magazines", align="L")

        self.set_xy(120, 9)
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(*INK)
        self.cell(78, 8, d.title, align="R")
        self.set_xy(120, 18)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(78, 4, f"Invoice: {d.invoice_number}", align="R")
        self.set_xy(120, 22)
        self.cell(78, 4, f"Date: {d.invoice_date}", align="R")
        self.set_y(34)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_draw_color(*LINE)
        self.line(12, self.get_y(), 198, self.get_y())
        self.set_y(-12)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*MUTED)
        self.cell(
            0,
            4,
            "This is a computer-generated invoice and does not require a physical signature.",
            align="C",
        )
        self.set_y(-8)
        self.cell(0, 4, f"Page {self.page_no()}  |  www.chicaboo.co", align="C")

    # --- helpers ---------------------------------------------------------
    def _section_label(self, text: str) -> None:
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*GOLD)
        self.cell(0, 5, text.upper())
        self.ln(5)

    def _party_block(self, x: float, w: float, label: str, party: InvoiceParty) -> float:
        y0 = self.get_y()
        self.set_xy(x, y0)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*GOLD)
        self.cell(w, 4.5, label.upper())
        self.set_xy(x, y0 + 4.5)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*INK)
        self.multi_cell(w, 4.2, party.name)
        self.set_x(x)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        for ln in party.address_lines:
            if ln:
                self.set_x(x)
                self.multi_cell(w, 4, ln)
        if party.phone:
            self.set_x(x)
            self.multi_cell(w, 4, f"Phone: {party.phone}")
        if party.state:
            self.set_x(x)
            self.multi_cell(w, 4, f"State: {party.state}")
        if party.gstin:
            self.set_x(x)
            self.multi_cell(w, 4, f"GSTIN: {party.gstin}")
        return self.get_y()


def render_invoice_pdf(data: InvoiceData) -> bytes:
    pdf = _InvoicePDF(data)
    pdf.add_page()

    # --- seller / GSTIN --------------------------------------------------
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*INK)
    pdf.cell(0, 5, f"Sold By: {data.seller.name}")
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MUTED)
    for ln in data.seller.address_lines:
        if ln:
            pdf.multi_cell(CONTENT_W, 4, ln)
    meta_bits = []
    if data.seller.gstin:
        meta_bits.append(f"GSTIN: {data.seller.gstin}")
    if data.seller_pan:
        meta_bits.append(f"PAN: {data.seller_pan}")
    if data.seller_email:
        meta_bits.append(data.seller_email)
    if data.seller_phone:
        meta_bits.append(data.seller_phone)
    if meta_bits:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*BRONZE)
        pdf.multi_cell(CONTENT_W, 4.4, "   |   ".join(meta_bits))
    pdf.ln(2)
    pdf.set_draw_color(*LINE)
    pdf.line(12, pdf.get_y(), 198, pdf.get_y())
    pdf.ln(3)

    # --- Bill To / Ship To -----------------------------------------------
    col_w = (CONTENT_W - 6) / 2
    y_start = pdf.get_y()
    y1 = pdf._party_block(12, col_w, "Bill To", data.bill_to)
    pdf.set_y(y_start)
    y2 = pdf._party_block(12 + col_w + 6, col_w, "Ship To", data.ship_to)
    pdf.set_y(max(y1, y2) + 2)

    # --- order / invoice meta strip --------------------------------------
    pdf.set_fill_color(*IVORY)
    strip_y = pdf.get_y()
    pdf.rect(12, strip_y, CONTENT_W, 12, style="F")
    pdf.set_xy(14, strip_y + 1.5)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*INK)
    meta_pairs = [
        ("Order ID", data.order_number),
        ("Order Date", data.order_date),
        ("Invoice No", data.invoice_number),
        ("Invoice Date", data.invoice_date),
        ("Payment", data.payment_method),
        ("Items", str(len(data.lines))),
    ]
    cell_w = CONTENT_W / 3 - 1.3
    for idx, (label, value) in enumerate(meta_pairs):
        row = idx // 3
        col = idx % 3
        x = 14 + col * (cell_w + 1.3)
        y = strip_y + 1.5 + row * 5
        pdf.set_xy(x, y)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*MUTED)
        pdf.cell(22, 4, f"{label}:")
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*INK)
        pdf.cell(cell_w - 22, 4, value)
    pdf.set_y(strip_y + 14)

    # --- items table -----------------------------------------------------
    # Sr | Description | HSN(Tax%) | Qty | Rate | Taxable | Tax | Total
    widths = [8, 56, 24, 10, 24, 24, 18, 22]
    headers = ["#", "Description", "HSN (Tax%)", "Qty", "Rate", "Taxable", "Tax", "Total"]
    aligns = ["C", "L", "L", "C", "R", "R", "R", "R"]

    pdf.set_fill_color(*GOLD)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 7.5)
    for w, h, a in zip(widths, headers, aligns):
        pdf.cell(w, 7, h, border=0, align=a, fill=True)
    pdf.ln(7)

    pdf.set_text_color(*INK)
    fill = False
    for idx, line in enumerate(data.lines, start=1):
        line_h = 4.2
        desc = line.name
        # measure wrapped description height
        wrapped = pdf.multi_cell(
            widths[1] - 2, line_h, desc, dry_run=True, output="LINES"
        )
        n_lines = max(1, len(wrapped))
        row_h = max(9, n_lines * line_h + 3)

        if pdf.get_y() + row_h > pdf.page_break_trigger:
            pdf.add_page()
            pdf.set_fill_color(*GOLD)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 7.5)
            for w, h, a in zip(widths, headers, aligns):
                pdf.cell(w, 7, h, border=0, align=a, fill=True)
            pdf.ln(7)
            pdf.set_text_color(*INK)

        x0 = pdf.get_x()
        y0 = pdf.get_y()
        if fill:
            pdf.set_fill_color(250, 247, 242)
            pdf.rect(x0, y0, CONTENT_W, row_h, style="F")
        fill = not fill

        hsn = f"{line.hsn or '-'}"
        if line.tax_rate_bps:
            hsn += f" ({line.tax_rate_bps / 100:g}%)"
        cells = [
            str(idx),
            None,  # description drawn via multi_cell
            hsn,
            str(line.quantity),
            _num(line.unit_price_paise),
            _num(line.taxable_paise),
            _num(line.tax_paise),
            _num(line.total_paise),
        ]
        # description
        pdf.set_xy(x0 + widths[0], y0 + 1.5)
        pdf.set_font("Helvetica", "B", 8)
        pdf.multi_cell(widths[1] - 2, line_h, desc, align="L")
        # other cells vertically centred-ish
        cy = y0 + (row_h - 4) / 2
        cx = x0
        pdf.set_font("Helvetica", "", 8)
        for i, (w, val, a) in enumerate(zip(widths, cells, aligns)):
            if i == 1:
                cx += w
                continue
            pdf.set_xy(cx, cy)
            pdf.cell(w - 1, 4, val or "", align=a)
            cx += w
        pdf.set_draw_color(*LINE)
        pdf.line(x0, y0 + row_h, x0 + CONTENT_W, y0 + row_h)
        pdf.set_xy(x0, y0 + row_h)

    pdf.ln(3)

    # --- totals + tax break-up (two columns) -----------------------------
    tax_x = 12
    tax_w = 96
    tot_x = 12 + CONTENT_W - 74
    tot_w = 74
    block_y = pdf.get_y()

    # tax break-up table (left)
    if data.tax_lines:
        pdf.set_xy(tax_x, block_y)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*GOLD)
        pdf.cell(tax_w, 5, "TAX BREAK-UP")
        pdf.set_xy(tax_x, block_y + 5)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(tax_w * 0.5, 5, "Component", border="B")
        pdf.cell(tax_w * 0.28, 5, "Taxable", border="B", align="R")
        pdf.cell(tax_w * 0.22, 5, "Tax", border="B", align="R")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*INK)
        for tl in data.tax_lines:
            pdf.set_x(tax_x)
            pdf.cell(tax_w * 0.5, 4.6, tl.label)
            pdf.cell(tax_w * 0.28, 4.6, _num(tl.taxable_paise), align="R")
            pdf.cell(tax_w * 0.22, 4.6, _num(tl.tax_paise), align="R")
            pdf.ln(4.6)
    else:
        pdf.set_xy(tax_x, block_y)
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(tax_w, 4.2, "Bill of Supply - no GST charged on the goods in this invoice.")

    # totals (right)
    def total_row(label: str, value: str, *, bold: bool = False, big: bool = False) -> None:
        pdf.set_x(tot_x)
        pdf.set_font("Helvetica", "B" if bold else "", 10 if big else 8)
        pdf.set_text_color(*(BRONZE if big else INK))
        pdf.cell(tot_w * 0.55, 6 if big else 5, label, align="L")
        pdf.cell(tot_w * 0.45, 6 if big else 5, value, align="R")
        pdf.ln(6 if big else 5)

    pdf.set_xy(tot_x, block_y)
    total_row("Taxable value", _money(data.subtotal_paise))
    if data.discount_paise:
        total_row("Discount", f"- {_money(data.discount_paise)}")
    if data.tax_paise:
        total_row("Total GST", _money(data.tax_paise))
    total_row("Shipping", _money(data.shipping_paise) if data.shipping_paise else "Free")
    pdf.set_x(tot_x)
    pdf.set_draw_color(*GOLD)
    pdf.line(tot_x, pdf.get_y(), tot_x + tot_w, pdf.get_y())
    pdf.ln(1)
    total_row("GRAND TOTAL", _money(data.grand_total_paise), bold=True, big=True)

    end_y = max(pdf.get_y(), block_y + 30)
    pdf.set_y(end_y + 2)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(CONTENT_W, 4, f"Amount in words: {data.amount_in_words}")
    pdf.ln(4)

    # --- signatory + thank you ------------------------------------------
    sig_y = pdf.get_y()
    pdf.set_xy(12 + CONTENT_W - 70, sig_y)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*INK)
    pdf.cell(70, 5, f"For {data.seller.name}", align="R")
    pdf.set_xy(12 + CONTENT_W - 70, sig_y + 14)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.cell(70, 5, "Authorised Signatory", align="R")

    pdf.set_xy(12, sig_y + 2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*GOLD)
    pdf.cell(80, 6, "Thank you for shopping with us!")
    pdf.set_xy(12, sig_y + 9)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(
        100,
        3.6,
        "Every loop of yarn and every keepsake is crafted to be cherished. "
        "For returns or help, write to us at support@chicaboo.co within 7 days of delivery.",
    )

    out = pdf.output()
    return bytes(out)
