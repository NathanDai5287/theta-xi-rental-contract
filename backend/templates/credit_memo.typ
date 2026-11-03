// =====================================================================
//  Theta Xi Credit Memo — issued when a security deposit is refunded
//  Requires shield.png in the same directory as the rendered .typ.
// =====================================================================

#import "_shared.typ": *

#let CLUB_NAME       = "«CLUB_NAME»"
#let MEMO_NUMBER     = "«MEMO_NUMBER»"
#let ISSUE_DATE      = "«ISSUE_DATE»"
#let EVENT_DATE      = "«EVENT_DATE»"
#let ORIGINAL_INVOICE = "«ORIGINAL_INVOICE»"   // e.g. "DEP-2026-0317-PSD"
#let SETTLEMENT_ROWS = («SETTLEMENT_ROWS»)
#let HAS_DEDUCTIONS = «HAS_DEDUCTIONS»
#let HAS_REFUND = «HAS_REFUND»
#let REFUND_DESCRIPTION = "«REFUND_DESCRIPTION»"
#let REFUND_AMOUNT_FMT  = "«REFUND_AMOUNT_FMT»"
#let REFUND_METHOD   = "«REFUND_METHOD»"        // e.g. "Zelle to organization treasurer"
// Issuer block fields. ISSUER_HEADING is "Theta Xi Fraternity Treasurer" by default
// or "Nathan Dai, Theta Xi Fraternity Treasurer" when a name is set.
// ISSUER_CONTACT is empty unless a contact value was provided.
#let ISSUER_HEADING = "«ISSUER_HEADING»"
#let ISSUER_CONTACT = "«ISSUER_CONTACT»"
// "the Theta Xi treasurer" or "Nathan Dai" — used inline in the Refund Method paragraph.
#let TREASURER_PHRASE = "«TREASURER_PHRASE»"

#set document(title: "Theta Xi Credit Memo " + MEMO_NUMBER)
#set page(
  paper: "us-letter",
  margin: (left: 0.85in, right: 0.85in, top: 0.7in, bottom: 0.9in),
  footer: page_footer("Theta Xi Fraternity  ·  Credit Memo " + MEMO_NUMBER),
)
#set text(size: 10pt, font: standard_fonts, fill: ink)
#set par(justify: false, leading: 0.70em, spacing: 0.85em, first-line-indent: 0pt)

#letterhead("CREDIT MEMO", if HAS_DEDUCTIONS { "Security Deposit Settlement" } else { "Security Deposit Refund" }, bottom_gap: 18pt)

// ---- Issued-to + memo metadata -------------------------------------
#billing_block(
  CLUB_NAME,
  (
    ("Credit Memo #", MEMO_NUMBER),
    ("Issue Date", ISSUE_DATE),
    ("Event Date", EVENT_DATE),
    ("Refunds Invoice", ORIGINAL_INVOICE),
  ),
)

// ---- Refund line item (rendered in green) --------------------------
#line_items(
  SETTLEMENT_ROWS,
  if HAS_DEDUCTIONS { "Remaining Refund" } else { "Refund Amount" },
  REFUND_AMOUNT_FMT,
  total_color: accent_green,
)

// Refund details start on a fresh page so the memo summary above stands
// alone — same layout contract as the invoices: page 1 numbers, page 2
// details.
#pagebreak()

// ---- Body ----------------------------------------------------------
#section("01", "Refund Summary")
#if HAS_DEDUCTIONS [
  This memo records the security deposit settlement for #CLUB_NAME and the event on #EVENT_DATE under invoice #ORIGINAL_INVOICE. The itemized costs above reduce the original deposit. The remaining refund appears on the first page.
] else [
  Theta Xi Fraternity hereby issues this credit memo to #CLUB_NAME for the refund of the security deposit collected under invoice #ORIGINAL_INVOICE in connection with the event held on #EVENT_DATE. The amount referenced above will be returned in full.
]

#section("02", "Refund Method")
#if HAS_REFUND [
  The refund will be issued via #REFUND_METHOD. Contact #TREASURER_PHRASE with this memo number for payment questions. This memo does not confirm payment.
] else [
  The full deposit was applied to the itemized costs. No refund remains. No payment is required to complete this settlement.
]

#section("03", "Acknowledgement")
This memo records the deposit amount, any deductions, and the remaining refund. It does not change unrelated balances under the Hosting Contract.

// ---- Issued-by footer ----------------------------------------------
#v(8pt)
#line(length: 100%, stroke: 1.2pt + brand)
#v(5pt)
#text(size: 8pt, weight: "medium", tracking: 1.6pt, fill: brand)[ISSUED BY]
#v(6pt)
#text(size: 9.25pt, fill: ink, weight: "semibold")[#ISSUER_HEADING]
#if ISSUER_CONTACT != "" {
  v(2pt)
  text(size: 9pt, fill: muted)[#ISSUER_CONTACT]
}
#v(2pt)
#text(size: 8.75pt, fill: muted)[2639 Durant Avenue]
