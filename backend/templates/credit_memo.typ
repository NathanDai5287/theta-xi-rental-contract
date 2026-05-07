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
#set text(size: 10.5pt, font: standard_fonts, fill: ink)
#set par(justify: false, leading: 0.74em, spacing: 1.0em, first-line-indent: 0pt)

#letterhead("CREDIT MEMO", "Security Deposit Refund")

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
  (
    (REFUND_DESCRIPTION, "(" + REFUND_AMOUNT_FMT + ")"),
  ),
  "Refund Amount",
  REFUND_AMOUNT_FMT,
  total_color: accent_green,
)

// ---- Body ----------------------------------------------------------
#section("01", "Refund Summary")
Theta Xi Fraternity hereby issues this credit memo to #CLUB_NAME for the refund of the security deposit collected under invoice #ORIGINAL_INVOICE in connection with the event held on #EVENT_DATE. The amount referenced above will be returned in full.

#section("02", "Refund Method")
The refund will be issued via #REFUND_METHOD. Please allow up to 5 business days for the funds to be received. If the refund has not arrived within this window, contact #TREASURER_PHRASE with this memo number for follow-up.

#section("03", "Acknowledgement")
This credit memo confirms that all conditions of the Hosting Contract relating to the security deposit have been satisfied. No further obligation exists between the parties with respect to the deposit. Any unrelated outstanding balances under the Hosting Contract remain unaffected by this credit memo.

// ---- Issued-by footer ----------------------------------------------
#v(14pt)
#line(length: 100%, stroke: 1.2pt + brand)
#v(5pt)
#text(size: 8pt, weight: "medium", tracking: 1.6pt, fill: brand)[ISSUED BY]
#v(8pt)
#text(size: 9.5pt, fill: ink, weight: "semibold")[#ISSUER_HEADING]
#if ISSUER_CONTACT != "" {
  v(2pt)
  text(size: 9pt, fill: muted)[#ISSUER_CONTACT]
}
#v(2pt)
#text(size: 9pt, fill: muted)[2639 Durant Avenue]
