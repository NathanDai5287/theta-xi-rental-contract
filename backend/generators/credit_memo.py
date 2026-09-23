"""
Credit memo generator. Issued when a security deposit is refunded.
"""
from __future__ import annotations

import math
from typing import Any

from .base import (
    fmt_currency,
    normalize_org_name,
    parse_event_date,
    render_typst,
    slug,
    typst_string,
)


def _parse_amount(v: Any) -> float:
    """Accepts numbers or strings. Strips $ and commas. Rejects non-finite
    and negative amounts — a credit memo for $nan or $-50 must not render."""
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


def _generate_memo_number(club: str, event_date: str, override: str | None) -> str:
    if override:
        # Callers occasionally send a JSON number; it becomes the download
        # filename, so normalize to a stripped string.
        return str(override).strip()
    # The number stamps the EVENT date; callers send it ISO or display-formatted.
    d = parse_event_date(event_date)
    suffix = "".join(c for c in slug(club).upper() if c.isalnum())[:6] or "PARTNR"
    return f"CM-{d:%Y-%m%d}-{suffix}"


def _treasurer_phrase(name: str) -> str:
    """Inline mention used inside the Refund Method paragraph."""
    name = (name or "").strip()
    return name if name else "the Theta Xi treasurer"


def _issuer_heading(name: str) -> str:
    """Bold heading inside the ISSUED BY block at the bottom."""
    name = (name or "").strip()
    if not name:
        return "Theta Xi Fraternity Treasurer"
    return f"{name}, Theta Xi Fraternity Treasurer"


def generate_credit_memo(values: dict[str, Any]) -> tuple[bytes, str]:
    """
    Render a credit memo for a refunded security deposit to PDF.

    Required keys:
      club_name:        organization receiving the refund
      event_date:       human-readable date string for the event
      amount:           amount being refunded
      issue_date:       date the memo is issued
      original_invoice: number of the deposit invoice this memo refunds

    Optional:
      memo_number:        override; otherwise auto-generated
      refund_method:      e.g. "Zelle to organization treasurer" (default)
      refund_description: line description (default uses event date)
      treasurer_name:     officer name; replaces the generic treasurer mention
      treasurer_contact:  email/phone shown in the ISSUED BY block

    Returns (pdf_bytes, memo_number).
    """
    required = ["club_name", "event_date", "amount", "issue_date", "original_invoice"]
    for k in required:
        if not values.get(k):
            raise ValueError(f"missing required field: {k}")

    club             = normalize_org_name(str(values["club_name"]))
    event_date       = str(values["event_date"]).strip()
    issue_date       = str(values["issue_date"]).strip()
    original_invoice = str(values["original_invoice"]).strip()
    amount           = _parse_amount(values["amount"])
    amount_fmt       = fmt_currency(amount)

    memo_number = _generate_memo_number(club, event_date, values.get("memo_number"))
    refund_method = (
        str(values.get("refund_method") or "Zelle to organization treasurer").strip()
    )
    refund_description = (
        str(values.get("refund_description") or "").strip()
        or f"Refund of security deposit for the event on {event_date}"
    )
    treasurer_name    = str(values.get("treasurer_name") or "").strip()
    treasurer_contact = str(values.get("treasurer_contact") or "").strip()

    repl: dict[str, str] = {
        "«CLUB_NAME»":          typst_string(club),
        "«MEMO_NUMBER»":        typst_string(memo_number),
        "«ISSUE_DATE»":         typst_string(issue_date),
        "«EVENT_DATE»":         typst_string(event_date),
        "«ORIGINAL_INVOICE»":   typst_string(original_invoice),
        "«REFUND_DESCRIPTION»": typst_string(refund_description),
        "«REFUND_AMOUNT_FMT»":  typst_string(amount_fmt),
        "«REFUND_METHOD»":      typst_string(refund_method),
        "«ISSUER_HEADING»":     typst_string(_issuer_heading(treasurer_name)),
        "«ISSUER_CONTACT»":     typst_string(treasurer_contact),
        "«TREASURER_PHRASE»":   typst_string(_treasurer_phrase(treasurer_name)),
    }

    pdf = render_typst("credit_memo.typ", repl)
    return pdf, memo_number
