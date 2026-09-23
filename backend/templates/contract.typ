// =====================================================================
//  Theta Xi Hosting Contract — premium letterhead edition
//  Requires shield.png + signature.png in the same directory.
// =====================================================================

#import "_shared.typ": *

// Auto-signature toggle (substituted by the Python script)
#let SIGNED   = «IS_SIGNED»
#let SIG_DATE = "«SIG_DATE»"

// ── Party + event values ──────────────────────────────────────────────
// Substituted by the Python generator as escaped string literals and
// referenced with # — user input can never inject markup this way.
//   TERM    — how the body refers to the renter: the organization's own
//             name, or "the Renter" for multi-organization events (see
//             OPENING).
//   TERM_CAP — sentence-initial variant of TERM ("The Renter" for
//             multi-organization events) so no sentence starts lowercase.
//   PARTIES — the renter side of the preamble's "by and between": the
//             organization's own name, or the labeled list that introduces
//             each organization and defines "the Renter".
//   OPENING — the Section 01 subject, including "hereby agree(s)".
#let TERM         = "«TERM»"
#let TERM_CAP     = "«TERM_CAP»"
#let PARTIES      = "«PARTIES»"
#let OPENING      = "«OPENING»"
#let EVENT_DATE   = "«EVENT_DATE»"
#let START_TIME   = "«START_TIME»"
#let END_TIME     = "«END_TIME»"
#let PRICE        = "«PRICE»"
#let DEPOSIT      = "«DEPOSIT»"
#let MAX_GUESTS   = "«MAX_GUESTS»"
#let NUM_MONITORS = "«NUM_MONITORS»"
#let HARD_CAP     = "«HARD_CAP»"

#set document(title: "Theta Xi Hosting Contract")
#set page(
  paper: "us-letter",
  margin: (left: 0.85in, right: 0.85in, top: 0.7in, bottom: 0.9in),
  footer: page_footer("Theta Xi Fraternity  ·  Hosting Contract"),
)
#set text(size: 10.5pt, font: standard_fonts, fill: ink)
#set par(justify: false, leading: 0.74em, spacing: 1.5em, first-line-indent: 0pt)

// ===================== LETTERHEAD ====================================
#letterhead("HOSTING CONTRACT", "Terms and Conditions")

// ===================== PREAMBLE ======================================
// Names both parties up front — a contract that never identifies the
// counterparty isn't much of a contract. The house is always referenced
// by its full name in the body, so no short defined term is introduced
// for it here; multi-organization events define "the Renter" in #PARTIES.
This Hosting Contract (this "Agreement") is entered into by and between Theta Xi Fraternity, located at 2639 Durant Avenue, Berkeley, California, and #PARTIES.

// ===================== BODY ==========================================

#section("01", "Rental Period")
#OPENING to rent the Theta Xi Fraternity premises located at 2639 Durant Avenue (hereinafter referred to as the "Fraternity House") for the purpose of hosting an event on #EVENT_DATE. The rental period will commence at #START_TIME and conclude at #END_TIME«END_DAY_PHRASE».

#subclause("1a.")[#TERM_CAP shall have 30 minutes following the conclusion of the rental period to clean up and vacate the Fraternity House. Should #TERM or any of its guests fail to vacate the premises within this window, Theta Xi Fraternity reserves the right to retain the security deposit in full.]

#section("02", "Rental Fee")
#TERM_CAP agrees to pay a rental fee of \$#PRICE for the use of the Fraternity House for the event. The full rental fee is due within 2 days after the conclusion of the event. Accepted payment methods are cash, Zelle, or credit card (subject to a 3% processing surcharge). Rental fee is subject to change based on the Theta Xi Fraternity Executive Board's decision.

#subclause("2a.")[#TERM_CAP shall provide a security deposit of \$#DEPOSIT, due in full no later than 1 hour before the event start time. If the security deposit is not received by that time, Theta Xi Fraternity reserves the right to cancel the event and terminate this Agreement. Upon receipt of the full rental fee, Theta Xi Fraternity will return the \$#DEPOSIT security deposit to #TERM, subject to the conditions specified in this Agreement. The security deposit is a separate obligation from the rental fee; any forfeiture or retention of the security deposit by Theta Xi Fraternity does not reduce or offset the rental fee owed by #TERM. Partial payment of the rental fee does not constitute settlement — the full rental fee remains due regardless of any amount remitted. Should the rental fee not be received within 2 days after the event, Theta Xi Fraternity reserves the right to retain the security deposit.]

#subclause("2b.")[If either party cancels this Agreement prior to the event, no cancellation fee will be assessed. Any security deposit already paid by #TERM will be returned in full.]

#subclause("2c.")[Should Theta Xi Fraternity be required to pursue collection of any outstanding balance under this Agreement, #TERM shall be liable for all reasonable costs incurred in doing so, including but not limited to court filing fees and collection fees.]

#section("03", "Attendance Restrictions")
Fraternity brothers are not allowed to attend the event unless they are directly affiliated with #TERM or have been invited by #TERM representatives. Theta Xi Fraternity shall respect and uphold this condition to ensure a secure and private event for #TERM.

#section("04", "Guest Authorization")
#TERM_CAP is solely responsible for managing and verifying guest access to the event. #TERM shall ensure that only authorized guests are admitted to the Fraternity House. «GUEST_LIST_SENTENCE»Any damages, incidents, or liabilities arising from the conduct of admitted guests are the full responsibility of #TERM.

#subclause("4a.")[Attendance at the event shall not exceed #MAX_GUESTS guests. Exceeding this number requires prior written approval from Theta Xi Fraternity. Under no circumstances may attendance exceed #HARD_CAP guests, as this is the maximum capacity of the Fraternity House. Should attendance exceed #HARD_CAP guests, Theta Xi Fraternity reserves the right to retain the security deposit and terminate the event immediately.]

#subclause("4b.")[#TERM_CAP must appoint #NUM_MONITORS Sober Monitors, based on the total number of event attendees, to ensure responsible alcohol consumption and to act as representatives of the organization in case of emergency. These individuals must be clearly identifiable and capable of managing alcohol-related situations.]

#subclause("4c.")[Theta Xi Fraternity will *NOT* be held responsible nor liable for emergency events and members of Theta Xi Fraternity are permitted to step in at their discretion on the grounds of preventing any potential risk.]

«FIRE_PERMIT_CLAUSE»

#section("05", "Use of Fraternity House Property")
#TERM_CAP is hereby granted permission to utilize the Fraternity House for the duration of its event. «AMENITIES_SENTENCE»It is understood that #TERM is responsible for the respectful use of all property.

#subclause("5a.")[#TERM_CAP shall ensure that all event activities comply with applicable local noise ordinances and City of Berkeley regulations. Should law enforcement or city officials respond to a noise complaint arising from the event, #TERM shall bear full responsibility for the situation and shall be liable for any associated fines, fees, or costs incurred.]

«SPACE_CLEARING_SUBCLAUSE»

«CLEANUP_TIER_CLAUSE»

#section("06", "Damages and Security Deposit")
If any furniture or house property belonging to Theta Xi Fraternity is damaged, lost, or stolen during the event, the cost of repairs or replacement will be deducted from the \$#DEPOSIT security deposit provided by #TERM. If the cost of repairs or replacement exceeds \$#DEPOSIT, #TERM agrees to cover the additional expenses.

#subclause("6a.")[#TERM_CAP acknowledges and agrees that Theta Xi Fraternity, its officers, and members, shall not be liable for any injuries, damages, or losses that may occur to any party or guest during the event. #TERM_CAP further agrees to bear all costs associated with such emergency services and Theta Xi Fraternity will not be held accountable for any claims, damages, or expenses arising out of or in connection with the use of such services.]

#section("07", "Excessive Waste Policy")
In the event that excessive waste is not properly disposed of by #TERM, following the conclusion of its event, such negligence will be classified under "Damage to Property." Theta Xi Fraternity reserves the right to assess and impose necessary charges for the cleanup and disposal of this waste. For purposes of this Agreement, cleaning obligations are limited to the disposal of trash and garbage; #TERM is not responsible for mopping, sweeping, or any other deep cleaning of the premises.

#section("08", "Restricted Areas")
Guests of #TERM are permitted to access the following designated areas of the Fraternity House during the event: «ALLOWED_AREAS_LIST». Upstairs areas of the Fraternity House are strictly prohibited at all times. All other areas not listed above may only be entered when accompanied by a member of Theta Xi Fraternity. Any unauthorized access to restricted or prohibited areas will result in forfeiture of the \$#DEPOSIT security deposit.

#section("09", "Termination of Agreement")
In the event of a breach of any of the terms and conditions outlined in this Agreement, Theta Xi Fraternity reserves the right to terminate this Agreement, remove #TERM from the premises, and retain the security deposit.

#section("10", "Governing Law")
This Agreement is governed by the laws of the State of California, and any disputes arising from this Agreement will be resolved under applicable state laws.

#section("11", "Entire Agreement")
This Agreement constitutes the entire understanding between Theta Xi Fraternity and #TERM concerning the event rental. Any modifications or amendments to this Agreement must be made in writing and signed by both parties.

// ===================== SIGNATURES ====================================
#v(28pt)

// SIG_BLOCK_BREAKABLE is substituted by the generator: with 5+ renter
// organizations the right-hand column can exceed a page, and an
// unbreakable block taller than the page gets its overflow silently
// clipped — a contract missing a party's signature line. In that case the
// block may flow across pages (each renter block is itself unbreakable).
#block(breakable: «SIG_BLOCK_BREAKABLE»)[
  #line(length: 100%, stroke: 1.2pt + brand)
  #v(6pt)
  #text(size: 8pt, weight: "medium", tracking: 1.6pt, fill: brand)[EXECUTION]

  #v(12pt)

  By signing below, both parties acknowledge and agree to the terms and conditions set forth in this Agreement.

  #v(20pt)

  // Helper: a signature/date cell with an optional overlay (image or text)
  #let sig_cell(overlay, label, gap) = [
    #box(width: 100%, height: gap)[#overlay]
    #box(width: 100%, stroke: (bottom: 0.6pt + ink))[#h(1pt)]
    #v(-3pt)
    #text(size: 8pt, fill: muted, tracking: 0.5pt)[#label]
  ]

  #grid(
    columns: (1fr, 1fr),
    column-gutter: 36pt,

    // --- Theta Xi column — optionally pre-signed ---
    [
      #text(size: 9.5pt, weight: "bold", fill: ink)[Theta Xi Fraternity Executive Board]
      #v(8pt)
      #sig_cell(
        if SIGNED [
          #place(bottom + left, dx: 10pt, dy: 6pt)[
            #image("signature.png", height: 64pt)
          ]
        ],
        "SIGNATURE",
        54pt,
      )
      #v(28pt)
      #sig_cell(
        if SIGNED [
          #align(bottom + left)[
            #pad(left: 6pt, bottom: 3pt)[
              #text(size: 10.5pt, fill: ink)[#SIG_DATE]
            ]
          ]
        ],
        "DATE",
        22pt,
      )
    ],

    // --- Renter column: one row per organization ---
    «RENTER_SIG_COLUMN»
  )
]
