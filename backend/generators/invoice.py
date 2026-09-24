"""
Invoice generator. Handles two kinds:

  - "deposit": security deposit invoice, due before the event starts.
               Mentions forfeiture conditions per the hosting contract.
  - "rental":  main rental fee invoice, due 2 days after the event.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from .base import (
    fmt_currency,
    normalize_org_name,
    parse_event_date,
    render_typst,
    slug,
    typst_string,
)

InvoiceKind = Literal["deposit", "rental"]


def _parse_amount(v: Any) -> float:
    """Accepts numbers or strings. Strips $ and commas. Rejects non-finite
    and negative amounts — an invoice for $nan or $-50 must not render."""
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = str(v).replace("$", "").replace(",", "").strip()
        if not s:
            raise ValueError("amount is empty")
        try:
            f = float(s)
        except ValueError:
            raise ValueError(f"invalid amount: {v!r}") from None
    if not math.isfinite(f) or f < 0:
        raise ValueError(f"invalid amount: {v!r}")
    return f


def _terms_block(kind: InvoiceKind, hard_cap: int = 200) -> str:
    """
    Returns typst markup for the kind-specific terms section(s).

    The organization is named in full once, in the opening sentence, via
    the template's #CLUB_NAME string binding rather than interpolated —
    this block lands in markup context, so an interpolated name would be
    a markup-injection hole. Later clauses say "the Organization" (the
    party named in the Bill To block): repeating a long multi-org list
    several times can push the details page past one page.
    """
    if kind == "deposit":
        return (
            '#section("02", "Security Deposit Terms")\n'
            'This invoice represents the security deposit required to secure the rental '
            'of the Theta Xi Fraternity House. Payment of this deposit confirms #CLUB_NAME\'s '
            'agreement to the Hosting Contract executed for this event.\n\n'
            '#subclause("2a.")[The security deposit must be received in full no later than 1 hour before '
            'the event start time. The event will not be permitted to commence until '
            'this invoice is paid.]\n\n'
            '#subclause("2b.")[Upon receipt of the full rental fee following the event, '
            'and provided no breach of the Hosting Contract has occurred, the security '
            'deposit will be returned to the Organization via a credit memo issued by Theta Xi '
            'Fraternity.]\n\n'
            '#section("03", "Conditions for Forfeiture")\n'
            'The security deposit is at risk of being forfeited, in whole or in part, '
            'under any of the conditions specified in the Hosting Contract, including '
            'but not limited to:\n\n'
            '#subclause("3a.")[Damage to, loss of, or theft of Theta Xi Fraternity '
            'property during the event. Repair or replacement costs are deducted from '
            'this deposit, and any excess remains owed by the Organization (Section 05 of the '
            'Hosting Contract).]\n\n'
            '#subclause("3b.")[Failure to vacate the Fraternity House within the '
            '30-minute window following the conclusion of the rental period '
            '(Subclause 1a).]\n\n'
            f'#subclause("3c.")[Attendance exceeding {hard_cap} guests, the maximum capacity '
            'of the Fraternity House (Subclause 3a).]\n\n'
            '#subclause("3d.")[Unauthorized access to restricted or prohibited areas '
            'of the Fraternity House (Section 07).]\n\n'
            '#subclause("3e.")[Failure to remit the rental fee within 2 days following '
            'the event (Subclause 2a).]\n\n'
            '#subclause("3f.")[Breach of any other term or condition outlined in the '
            'Hosting Contract (Section 08).]\n\n'
            'The security deposit is a separate obligation from the rental fee. Forfeiture '
            'of any portion of this deposit does not reduce or offset the rental fee owed.'
        )
    # rental
    return (
        '#section("02", "Rental Fee Terms")\n'
        'This invoice represents the full rental fee for use of the Theta Xi Fraternity '
        'House. Payment is due in full within 2 days following the conclusion of the '
        'event, in accordance with Section 02 of the Hosting Contract.\n\n'
        '#subclause("2a.")[Partial payment does not constitute settlement; the full '
        'rental fee remains due regardless of any amount remitted.]\n\n'
        '#subclause("2b.")[Failure to remit the rental fee within 2 days following the '
        'event entitles Theta Xi Fraternity to retain the security deposit in addition '
        'to pursuing collection of the outstanding rental fee.]\n\n'
        '#subclause("2c.")[The Organization shall be liable for all reasonable costs incurred by '
        'Theta Xi Fraternity in pursuing collection of any outstanding balance, '
        'including but not limited to court filing fees and collection fees.]'
    )


def _treasurer_phrase(name: str) -> str:
    """
    "the Theta Xi treasurer"  (default — name not provided)
    "Nathan Dai (Theta Xi Treasurer)"  (name provided)

    Used inline in the Payment Instructions sentence.
    """
    name = (name or "").strip()
    if not name:
        return "the Theta Xi treasurer"
    return f"{name} (Theta Xi Treasurer)"


def _format_line_items(rows: list[tuple[str, str]]) -> str:
    """
    Build a typst array literal of the form `(("desc", "$1.00"), ("desc2", "$2.00"),)`.
    The trailing comma keeps single-element tuples valid typst syntax.
    """
    if not rows:
        raise ValueError("line_items must contain at least one row")
    inner = ", ".join(
        f'("{typst_string(desc)}", "{typst_string(amt)}")'
        for desc, amt in rows
    )
    return f"({inner},)"


def _generate_invoice_number(kind: InvoiceKind, club: str, event_date: str, override: str | None) -> str:
    if override:
        # Callers occasionally send a JSON number; it becomes the download
        # filename, so normalize to a stripped string.
        return str(override).strip()
    prefix = "DEP" if kind == "deposit" else "RNT"
    # The number stamps the EVENT date; callers send it ISO or display-formatted.
    d = parse_event_date(event_date)
    suffix = "".join(c for c in slug(club).upper() if c.isalnum())[:6] or "PARTNR"
    return f"{prefix}-{d:%Y-%m%d}-{suffix}"


def generate_invoice(values: dict[str, Any]) -> tuple[bytes, str]:
    """
    Render a security-deposit or rental invoice to PDF.

    Required keys:
      kind:        "deposit" | "rental"
      club_name:   organization being billed
      event_date:  human-readable date string for the event
      issue_date:  human-readable date this invoice is issued
      due_date:    human-readable date this invoice is due

    Plus exactly one of:
      amount:      total invoiced amount (used to auto-generate a single line)
      line_items:  list of {description, amount} dicts — multiple rows in the
                   invoice table. The total is the sum of these amounts.
                   Only honored for kind="rental"; deposit invoices are always
                   single-line by design.

    Optional:
      invoice_number: override; otherwise auto-generated.
      max_guests:     agreed maximum guests from the contract. The deposit
                      invoice's forfeiture clause 3c cites the 4a attendance
                      cap, which is max(200, max_guests). Defaults to 200.

    Returns (pdf_bytes, invoice_number).
    """
    kind = values.get("kind")
    if kind not in ("deposit", "rental"):
        raise ValueError("kind must be 'deposit' or 'rental'")

    for k in ("club_name", "event_date", "issue_date", "due_date"):
        if not values.get(k):
            raise ValueError(f"missing required field: {k}")

    club        = normalize_org_name(str(values["club_name"]))
    event_date  = str(values["event_date"]).strip()
    issue_date  = str(values["issue_date"]).strip()
    due_date    = str(values["due_date"]).strip()

    invoice_number = _generate_invoice_number(
        kind, club, event_date, values.get("invoice_number")
    )

    # ── Build line items + total ──────────────────────────────────────
    raw_line_items = values.get("line_items")
    if raw_line_items and kind == "rental":
        if not isinstance(raw_line_items, list):
            raise ValueError("line_items must be a list of {description, amount} objects")
        # Itemized rental invoice
        rows: list[tuple[str, str]] = []
        total = 0.0
        for i, item in enumerate(raw_line_items):
            if not isinstance(item, dict):
                raise ValueError(f"line item #{i + 1} must be an object")
            desc = str((item.get("description") or "")).strip()
            if not desc:
                raise ValueError(f"line item #{i + 1} is missing a description")
            try:
                amt = _parse_amount(item.get("amount"))
            except ValueError:
                raise ValueError(f"line item #{i + 1} has invalid amount")
            total += amt
            rows.append((desc, fmt_currency(amt)))
        total_fmt = fmt_currency(total)
    else:
        # Single-line invoice (deposit, or rental without explicit line_items)
        if values.get("amount") is None or values.get("amount") == "":
            raise ValueError("missing required field: amount (or line_items)")
        amt = _parse_amount(values["amount"])
        if kind == "deposit":
            line_description = (
                f"Security deposit for use of the Theta Xi Fraternity House on {event_date}"
            )
        else:
            line_description = (
                f"Rental of the Theta Xi Fraternity House for the event on {event_date}"
            )
        rows = [(line_description, fmt_currency(amt))]
        total_fmt = fmt_currency(amt)

    if kind == "deposit":
        doc_kind_label = "SECURITY DEPOSIT INVOICE"
        doc_subtitle   = "Due Before Event"
        due_label      = "Due Before"
    else:
        doc_kind_label = "RENTAL INVOICE"
        doc_subtitle   = "Rental Fee · Due 2 Days After Event"
        due_label      = "Due Date"

    treasurer_name = str(values.get("treasurer_name") or "").strip()

    # Deposit forfeiture clause 3c cites the contract's 4a cap: the house
    # capacity (200), or the agreed maximum guests when that's higher.
    # Tolerant on purpose: archived payloads predate this field and must
    # keep replaying, so anything missing/unparseable falls back to 200.
    hard_cap = 200
    try:
        mg = float(str(values.get("max_guests") or "").replace(",", "").strip())
        if math.isfinite(mg) and mg > 200:
            hard_cap = int(mg)
    except (ValueError, OverflowError):
        pass

    repl: dict[str, str] = {
        "«INVOICE_KIND»":       kind,
        "«DOC_KIND_LABEL»":     typst_string(doc_kind_label),
        "«DOC_SUBTITLE»":       typst_string(doc_subtitle),
        "«CLUB_NAME»":          typst_string(club),
        "«INVOICE_NUMBER»":     typst_string(invoice_number),
        "«ISSUE_DATE»":         typst_string(issue_date),
        "«DUE_DATE»":           typst_string(due_date),
        "«DUE_LABEL»":          typst_string(due_label),
        "«EVENT_DATE»":         typst_string(event_date),
        "«LINE_ITEMS»":         _format_line_items(rows),
        "«TOTAL_AMOUNT_FMT»":   typst_string(total_fmt),
        "«TREASURER_PHRASE»":   typst_string(_treasurer_phrase(treasurer_name)),
        "«TERMS_BLOCK»":        _terms_block(kind, hard_cap),
    }

    pdf = render_typst("invoice.typ", repl)
    return pdf, invoice_number
