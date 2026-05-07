"""
Invoice generator. Handles two kinds:

  - "deposit": security deposit invoice, due before the event starts.
               Mentions forfeiture conditions per the hosting contract.
  - "rental":  main rental fee invoice, due 2 days after the event.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from .base import fmt_currency, render_typst, slug, typst_string

InvoiceKind = Literal["deposit", "rental"]


def _parse_amount(v: Any) -> float:
    """Accepts numbers or strings. Strips $ and commas."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("$", "").replace(",", "").strip()
    if not s:
        raise ValueError("amount is empty")
    return float(s)


def _terms_block(kind: InvoiceKind, club: str) -> str:
    """Returns typst markup for the kind-specific terms section(s)."""
    if kind == "deposit":
        return (
            f'#section("02", "Security Deposit Terms")\n'
            f'This invoice represents the security deposit required to secure the rental '
            f'of the Theta Xi Fraternity House. Payment of this deposit confirms {club}\'s '
            f'agreement to the Hosting Contract executed for this event.\n\n'
            f'#subclause("2a.")[The security deposit must be received in full no later than 1 hour before '
            f'the event start time. The event will not be permitted to commence until '
            f'this invoice is paid.]\n\n'
            f'#subclause("2b.")[Upon receipt of the full rental fee following the event, '
            f'and provided no breach of the Hosting Contract has occurred, the security '
            f'deposit will be returned to {club} via a credit memo issued by Theta Xi '
            f'Fraternity.]\n\n'
            f'#section("03", "Conditions for Forfeiture")\n'
            f'The security deposit is at risk of being forfeited, in whole or in part, '
            f'under any of the conditions specified in the Hosting Contract, including '
            f'but not limited to:\n\n'
            f'#subclause("3a.")[Damage to, loss of, or theft of Theta Xi Fraternity '
            f'property during the event. Repair or replacement costs are deducted from '
            f'this deposit, and any excess remains owed by {club} (Section 06 of the '
            f'Hosting Contract).]\n\n'
            f'#subclause("3b.")[Failure to vacate the Fraternity House within the '
            f'30-minute window following the conclusion of the rental period '
            f'(Subclause 1a).]\n\n'
            f'#subclause("3c.")[Attendance exceeding 200 guests, the maximum capacity '
            f'of the Fraternity House (Subclause 4a).]\n\n'
            f'#subclause("3d.")[Unauthorized access to restricted or prohibited areas '
            f'of the Fraternity House (Section 08).]\n\n'
            f'#subclause("3e.")[Failure to remit the rental fee within 2 days following '
            f'the event (Subclause 2a).]\n\n'
            f'#subclause("3f.")[Breach of any other term or condition outlined in the '
            f'Hosting Contract (Section 09).]\n\n'
            f'The security deposit is a separate obligation from the rental fee. Forfeiture '
            f'of any portion of this deposit does not reduce or offset the rental fee owed.'
        )
    # rental
    return (
        f'#section("02", "Rental Fee Terms")\n'
        f'This invoice represents the full rental fee for use of the Theta Xi Fraternity '
        f'House. Payment is due in full within 2 days following the conclusion of the '
        f'event, in accordance with Section 02 of the Hosting Contract.\n\n'
        f'#subclause("2a.")[Partial payment does not constitute settlement; the full '
        f'rental fee remains due regardless of any amount remitted.]\n\n'
        f'#subclause("2b.")[Failure to remit the rental fee within 2 days following the '
        f'event entitles Theta Xi Fraternity to retain the security deposit in addition '
        f'to pursuing collection of the outstanding rental fee.]\n\n'
        f'#subclause("2c.")[{club} shall be liable for all reasonable costs incurred by '
        f'Theta Xi Fraternity in pursuing collection of any outstanding balance, '
        f'including but not limited to court filing fees and collection fees.]'
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


def _treasurer_contact_sentence(name: str, contact: str) -> str:
    """
    Footer "Questions about this invoice?" sentence:

      "Contact the Theta Xi treasurer."          (no name, no contact)
      "Contact Nathan Dai."                      (name only)
      "Contact Nathan Dai at nathan@x.edu."      (name + contact)
      "Contact the Theta Xi treasurer at …"      (contact only — unusual)
    """
    name = (name or "").strip()
    contact = (contact or "").strip()
    who = name if name else "the Theta Xi treasurer"
    if contact:
        return f"Contact {who} at {contact}."
    return f"Contact {who}."


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
        return override
    prefix = "DEP" if kind == "deposit" else "RNT"
    # Try to pull "YYYY-MMDD" out of an ISO date; fall back to today.
    try:
        d = date.fromisoformat(event_date)
    except ValueError:
        d = date.today()
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

    Returns (pdf_bytes, invoice_number).
    """
    kind = values.get("kind")
    if kind not in ("deposit", "rental"):
        raise ValueError("kind must be 'deposit' or 'rental'")

    for k in ("club_name", "event_date", "issue_date", "due_date"):
        if not values.get(k):
            raise ValueError(f"missing required field: {k}")

    club        = str(values["club_name"]).strip()
    event_date  = str(values["event_date"]).strip()
    issue_date  = str(values["issue_date"]).strip()
    due_date    = str(values["due_date"]).strip()

    invoice_number = _generate_invoice_number(
        kind, club, event_date, values.get("invoice_number")
    )

    # ── Build line items + total ──────────────────────────────────────
    raw_line_items = values.get("line_items")
    if raw_line_items and kind == "rental":
        # Itemized rental invoice
        rows: list[tuple[str, str]] = []
        total = 0.0
        for i, item in enumerate(raw_line_items):
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

    treasurer_name    = str(values.get("treasurer_name") or "").strip()
    treasurer_contact = str(values.get("treasurer_contact") or "").strip()

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
        "«TREASURER_CONTACT_SENTENCE»": typst_string(
            _treasurer_contact_sentence(treasurer_name, treasurer_contact)
        ),
        "«TERMS_BLOCK»":        _terms_block(kind, club),
    }

    pdf = render_typst("invoice.typ", repl)
    return pdf, invoice_number
