# Theta Xi Rental Tools

Three tools for managing rentals of the Theta Xi Fraternity House at 2639 Durant Avenue:

- **Hosting Contract** — generate the rental contract PDF.
- **Price Estimator** — interactive calculator for the rental fee.
- **Invoices & Credit Memo** — security-deposit invoice, rental-fee invoice, and a credit memo for refunding the deposit.

All four PDFs (contract + two invoice kinds + credit memo) are rendered with [Typst](https://typst.app/) and share a single brand kit — same letterhead, color palette, section markers, and footer.

---

## Project layout

```
.
├── README.md
├── formula.md                          Pricing formula reference
├── backend/
│   ├── app.py                          Flask app (PDF endpoints)
│   ├── render_contract.py              Interactive CLI for contracts
│   ├── requirements.txt
│   ├── generators/
│   │   ├── base.py                     Typst compile helper, slug, currency fmt
│   │   ├── contract.py                 generate_contract(values) → PDF bytes
│   │   ├── invoice.py                  generate_invoice(values)  → (PDF, number)
│   │   └── credit_memo.py              generate_credit_memo(...) → (PDF, number)
│   ├── templates/
│   │   ├── _shared.typ                 Brand colors, letterhead, section helpers
│   │   ├── contract.typ
│   │   ├── invoice.typ
│   │   └── credit_memo.typ
│   └── assets/
│       ├── shield.png
│       └── signature.png
└── frontend/
    ├── app/
    │   ├── layout.tsx, page.tsx        Landing page
    │   ├── contract/page.tsx           Contract form
    │   ├── pricing/page.tsx            Live calculator
    │   └── invoice/page.tsx            Three-tab invoice / memo form
    ├── components/
    │   ├── Nav.tsx
    │   └── PricingCalculator.tsx
    └── lib/
        ├── api.ts                      fetch helper that triggers PDF download
        └── pricing-constants.ts        Edit me to recalibrate pricing
```

---

## Prerequisites

- **Python 3.10+** for the backend.
- **Node 20+** for the frontend.
- **typst** CLI on PATH:
  - Windows: `winget install --id Typst.Typst`
  - macOS: `brew install typst`
  - Linux: `cargo install --locked typst-cli`

---

## Running in development

Open **two terminals** — one for each side.

### Backend (Flask, port 5000)

```bash
cd backend
pip install -r requirements.txt
python app.py
```

The server listens on `http://127.0.0.1:5000` and exposes:

| Endpoint                              | Body keys                                                                     |
| ------------------------------------- | ----------------------------------------------------------------------------- |
| `POST /api/generate/contract`         | `club_name, date, start_time, end_time, price, deposit, max_guests, monitors, areas, cleared, guest_list, sound_system, lighting_system, sign` |
| `POST /api/generate/invoice/deposit`  | `club_name, event_date, amount, issue_date, due_date, [invoice_number]`       |
| `POST /api/generate/invoice/rental`   | `club_name, event_date, amount, issue_date, due_date, [invoice_number]`       |
| `POST /api/generate/credit-memo`      | `club_name, event_date, amount, issue_date, original_invoice, [memo_number, refund_method, refund_description]` |

Each endpoint returns `application/pdf` with a `Content-Disposition` filename.

### Frontend (Next.js, port 3000)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. `next.config.mjs` rewrites `/api/*` to the Flask backend, so the frontend can call relative paths and there's no CORS to worry about in dev.

---

## Generating a contract from the command line

The interactive CLI from the original codebase is preserved at `backend/render_contract.py`:

```bash
cd backend
python render_contract.py
```

It prompts for every field, then writes `theta_xi_<club>_contract.pdf` into the current directory.

---

## Recalibrating pricing

The pricing model is **purely client-side** in the Next.js app. To change the numbers (base rate, per-guest rate, alcohol tiers, relationship discounts/surcharges, etc.), edit:

```
frontend/lib/pricing-constants.ts
```

Hot reload picks up the change. The reference for what each variable means is in [`formula.md`](./formula.md).

---

## Adjusting the document branding

All four PDFs import [`backend/templates/_shared.typ`](./backend/templates/_shared.typ), which defines:

- Brand colors (`brand`, `ink`, `muted`, `rule`, `accent_red`, `accent_green`)
- The `letterhead(doc_kind, doc_subtitle)` block (logo + organization + document type)
- The `section(num, title)` and `subclause(label, body)` helpers
- The `billing_block(client, meta)` and `line_items(rows, total_label, total)` helpers used by invoices and the credit memo
- The `page_footer(footer_left)` helper

Logo and signature images are PNGs at `backend/assets/shield.png` and `backend/assets/signature.png`. Replace those files to swap branding.
