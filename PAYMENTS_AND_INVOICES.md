# Payments (Razorpay) & Invoices

Built on the existing FastAPI backend. The commerce schema (orders, order_items,
order_tax_lines, payments, payment_transactions, invoices) already exists in
migrations `000016`/`000017` — this adds the **application layer** on top.

## Flow

```
POST /api/payments/checkout   (auth)  -> validate items, GST-price, create pending
                                          order + Razorpay order, return checkout params
  → browser opens Razorpay Checkout with { key_id, razorpay_order_id, amount }
POST /api/payments/verify     (auth)  -> verify browser signature, capture payment,
                                          mark order paid, generate invoice, email it
POST /api/payments/webhook    (public)-> Razorpay server webhook (HMAC verified),
                                          same terminal state — source of truth
GET  /api/payments/config     (public)-> { enabled, key_id }  (is online pay available?)
GET  /api/payments/{order_id} (auth)  -> payment/invoice status
```

Orders & invoices (customer):

```
GET  /api/orders                      -> my order history
GET  /api/orders/{id}                 -> order detail (items, totals, invoice info)
POST /api/orders/{id}/cancel          -> cancel a pending/confirmed order
GET  /api/orders/{id}/invoice         -> download the invoice PDF
```

Invoices (admin — every invoice is visible):

```
GET  /admin/invoices                       -> list/search all invoices
GET  /admin/invoices/{id}                  -> full invoice detail
GET  /admin/invoices/{id}/pdf              -> download the PDF
POST /admin/invoices/{id}/regenerate       -> re-render the PDF
```

## Configuration (`.env`)

```env
# Razorpay — Dashboard → Settings → API Keys, and → Webhooks
RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxx
RAZORPAY_WEBHOOK_SECRET=xxxxxxxx          # set the same secret in the Razorpay webhook

# Webhook URL to register in Razorpay: https://<api-host>/api/payments/webhook
# Subscribe to events: payment.captured, order.paid

# Seller block printed on the invoice
COMPANY_LEGAL_NAME=Chic A Boo
COMPANY_ADDRESS_LINE1=...
COMPANY_CITY=...
COMPANY_STATE=...
COMPANY_POSTAL_CODE=...
COMPANY_EMAIL=support@chicaboo.co
```

## GST is optional

No GST registration yet → leave `COMPANY_GSTIN` blank and `DEFAULT_GST_RATE_BPS=0`.
The document is issued as a **"Bill of Supply"** with no tax break-up (correct for a
non-GST seller). When you register:

1. Set `COMPANY_GSTIN`, `COMPANY_STATE_CODE` (e.g. `05` for Uttarakhand).
2. Set `DEFAULT_GST_RATE_BPS` (e.g. `1800` = 18%) or per-product
   `metadata.tax_rate_bps` + `metadata.hsn_code`.

It then auto-upgrades to a full **"Tax Invoice"**, splitting tax into CGST+SGST for
intra-state orders or IGST for inter-state — no code change. Prices are treated as
GST-inclusive (`PRICES_INCLUDE_GST=true`).

## Notes

- PDFs are rendered with `fpdf2` (pure-Python) and stored in Cloudflare R2 under
  `invoices/`. If R2 is unavailable the PDF is regenerated on the fly for download.
- Razorpay is called over its REST API with `httpx`; signatures use stdlib HMAC —
  no SDK dependency.
- Invoice numbering uses the DB sequence `commerce.invoice_number_seq`, formatted
  `CAB/<FY>/<00001>`.
