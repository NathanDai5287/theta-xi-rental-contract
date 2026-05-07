// =====================================================================
//  Theta Xi Invoice — used for both security deposit and rental payment
//  Requires shield.png in the same directory as the rendered .typ.
// =====================================================================

#import "_shared.typ": *

// Substituted by the Python invoice generator
#let INVOICE_KIND = "«INVOICE_KIND»"            // "deposit" | "rental"
#let DOC_KIND_LABEL = "«DOC_KIND_LABEL»"        // "SECURITY DEPOSIT INVOICE" / "RENTAL INVOICE"
#let DOC_SUBTITLE   = "«DOC_SUBTITLE»"          // small grey subtitle next to letterhead
#let CLUB_NAME      = "«CLUB_NAME»"
#let INVOICE_NUMBER = "«INVOICE_NUMBER»"
#let ISSUE_DATE     = "«ISSUE_DATE»"
#let DUE_DATE       = "«DUE_DATE»"
#let DUE_LABEL      = "«DUE_LABEL»"             // "Due Before Event" or "Due 2 Days After Event"
#let EVENT_DATE     = "«EVENT_DATE»"
// Line items as a typst array of (description, formatted-amount) tuples.
// Built by the Python generator — single-row for deposit, multi-row for itemized rental.
#let LINE_ITEMS = «LINE_ITEMS»
#let TOTAL_AMOUNT_FMT = "«TOTAL_AMOUNT_FMT»"     // pre-formatted, e.g. "$1,500.00"
// Treasurer mention. Pre-built phrase: either "the Theta Xi treasurer"
// (default) or e.g. "Nathan Dai (Theta Xi Treasurer)" when a name is set.
#let TREASURER_PHRASE = "«TREASURER_PHRASE»"
// Footer line about who to contact with questions. Defaults to a generic
// "Contact the Theta Xi treasurer." when no name is set.
#let TREASURER_CONTACT_SENTENCE = "«TREASURER_CONTACT_SENTENCE»"

#set document(title: "Theta Xi Invoice " + INVOICE_NUMBER)
#set page(
  paper: "us-letter",
  margin: (left: 0.85in, right: 0.85in, top: 0.7in, bottom: 0.9in),
  footer: page_footer("Theta Xi Fraternity  ·  Invoice " + INVOICE_NUMBER),
)
#set text(size: 10.5pt, font: standard_fonts, fill: ink)
#set par(justify: false, leading: 0.74em, spacing: 1.0em, first-line-indent: 0pt)

#letterhead(DOC_KIND_LABEL, DOC_SUBTITLE, bottom_gap: 20pt)

// ---- Bill-to + invoice metadata ------------------------------------
#billing_block(
  CLUB_NAME,
  (
    ("Invoice #", INVOICE_NUMBER),
    ("Issue Date", ISSUE_DATE),
    ("Event Date", EVENT_DATE),
    (DUE_LABEL, DUE_DATE),
  ),
)

// ---- Line items ----------------------------------------------------
#line_items(LINE_ITEMS, "Total Due", TOTAL_AMOUNT_FMT)

// Payment instructions and all terms start on a fresh page so the bill
// summary above stands alone — easier to scan, easier to file.
#pagebreak()

// ---- Payment instructions ------------------------------------------
#section("01", "Payment Instructions")
Accepted payment methods are cash, Zelle, or credit card (subject to a 3% processing surcharge). Payments by Zelle should be sent to #TREASURER_PHRASE. Please reference invoice number #INVOICE_NUMBER in the memo.

// ---- Kind-specific terms -------------------------------------------
«TERMS_BLOCK»

// ---- Footer note ---------------------------------------------------
#v(20pt)

#block(breakable: false)[
  #line(length: 100%, stroke: 1.2pt + brand)
  #v(6pt)
  #text(size: 8pt, weight: "medium", tracking: 1.6pt, fill: brand)[NOTES]
  #v(10pt)
  #text(size: 9.5pt, fill: muted)[
    Questions about this invoice? #TREASURER_CONTACT_SENTENCE
    All terms herein are governed by, and incorporated into, the Hosting Contract executed
    between #CLUB_NAME and Theta Xi Fraternity for the event on #EVENT_DATE.
  ]
]
