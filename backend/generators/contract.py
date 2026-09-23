"""
Contract generator. Returns PDF bytes for a Theta Xi hosting contract.

The shape of the input dict matches what the original render_contract.py
collected interactively — see CONTRACT_FIELDS for required keys.

Multi-organization events: pass `club_names` (a list of names) instead of
`club_name`. With two or more organizations the contract introduces each one
— "Organization 1", "Organization 2", … — and then refers to them
collectively as the "Renter" for the rest of the text (singular, so verb
agreement holds).

Security: every user-supplied scalar is substituted into the template as an
escaped string literal (see typst_string) and referenced with #TERM-style
bindings — never spliced into markup. The only raw-markup substitutions are
built below from fixed strings, computed numbers, and those same bindings.
"""
from __future__ import annotations

import datetime
import math
from typing import Any, TypedDict

from .base import english_list, normalize_org_name, render_typst, typst_string


# (key, placeholder, prompt_label, hint)
CONTRACT_FIELDS: list[tuple[str, str, str, str]] = [
    ("club_name",  "«CLUB_NAME»",    "Organization name",              "e.g. Pi Sigma Delta"),
    ("date",       "«EVENT_DATE»",   "Event date",                     "e.g. March 15, 2026"),
    ("start_time", "«START_TIME»",   "Start time (24-hour)",           "e.g. 22:00"),
    ("end_time",   "«END_TIME»",     "End time (24-hour)",             "e.g. 02:00"),
    ("price",      "«PRICE»",        "Rental fee in USD (no $)",       "e.g. 1500"),
    ("deposit",    "«DEPOSIT»",      "Security deposit in USD (no $)", "e.g. 100"),
    ("max_guests", "«MAX_GUESTS»",   "Maximum number of guests",       "e.g. 150"),
    ("monitors",   "«NUM_MONITORS»", "Number of sober monitors",       "e.g. 4"),
]

AREA_LABELS: dict[str, str] = {
    "living_room": "Living Room",
    "dining_room": "Dining Room",
    "backyard":    "Backyard",
}

AREA_CLEARING_DESC: dict[str, str] = {
    "living_room": "the couches, tables, and carpet",
    "dining_room": "the dining table and chairs",
    "backyard":    "everything off the cement area in the center",
}


class ContractInput(TypedDict, total=False):
    # required string fields
    club_name: str
    club_names: list[str]           # multi-org events; overrides club_name
    date: str
    start_time: str       # "HH:MM"
    end_time: str         # "HH:MM"
    price: str
    deposit: str
    max_guests: str
    monitors: str
    # tier options
    cleanup_tier: str               # "basic" | "full"
    # toggles
    areas: list[str]                 # subset of AREA_LABELS keys
    cleared: dict[str, bool]         # area key → "Theta Xi clears beforehand?"
    guest_list: bool
    sound_system: bool
    lighting_system: bool
    sign: bool


def _hhmm(s: str) -> tuple[int, int]:
    try:
        h, m = s.split(":")
        hh, mm = int(h), int(m)
    except ValueError:
        raise ValueError(f"invalid time: {s!r} (expected HH:MM)") from None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"invalid time: {s!r} (expected HH:MM)")
    return hh, mm


def _parse_money(value: str, label: str) -> float:
    """Tolerant money parse ($ and commas allowed); rejects garbage/negatives."""
    try:
        v = float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        raise ValueError(f"invalid {label}: {value!r}") from None
    if not math.isfinite(v) or v < 0:
        raise ValueError(f"invalid {label}: {value!r}")
    return v


def _parse_count(value: str, label: str, *, minimum: int = 1) -> int:
    """Whole-number counts. Normalizes '250.0' → 250; rejects 'abc', '-5'."""
    try:
        v = float(str(value).replace(",", "").strip())
    except ValueError:
        raise ValueError(f"invalid {label}: {value!r}") from None
    if not math.isfinite(v) or not v.is_integer() or v < minimum:
        raise ValueError(f"invalid {label}: {value!r}")
    return int(v)


def _dedupe(clubs: list[str]) -> list[str]:
    """Exact duplicates sign once — two identical signature blocks for the
    same organization would be confusing, not more binding."""
    seen: set[str] = set()
    out: list[str] = []
    for c in clubs:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _resolve_clubs(values: dict[str, Any]) -> list[str]:
    """One organization from club_name, or several from club_names. Names
    are print-normalized (see normalize_org_name) before anything else —
    deduping happens after normalization so "Alpha Club" and "alpha club"
    collapse into one signature block."""
    raw = values.get("club_names")
    if raw is not None:
        if not isinstance(raw, list):
            raise ValueError("club_names must be a list of organization names")
        clubs = [normalize_org_name(str(c)) for c in raw if str(c).strip()]
        if clubs:
            return _dedupe(clubs)
        # An empty list falls through to the legacy single-name field rather
        # than erroring — archived payloads may carry an empty club_names.
    single = normalize_org_name(str(values.get("club_name") or ""))
    if not single:
        raise ValueError("missing required field: club_name (Organization name)")
    return [single]


def _party_terms(clubs: list[str]) -> tuple[str, str, str, bool]:
    """
    Returns (term, parties, opening, multi):
      term    — how the body refers to the renter: the organization's own
                name, or "the Renter" for multi-org events. Singular either
                way, so the template's verb agreement ("is", "shall") holds.
      parties — the renter side of the preamble's "by and between": the
                organization's own name, or the labeled list that introduces
                each organization ("Organization 1", "Organization 2", …)
                and defines "the Renter" for the rest of the document.
      opening — the Section 01 subject, e.g. 'Pi Sigma Delta hereby agrees'
                or 'The Renter hereby agrees' once the preamble has defined
                the term.
    """
    if len(clubs) == 1:
        return clubs[0], clubs[0], f"{clubs[0]} hereby agrees", False
    labeled = [f'{name} ("Organization {i}")' for i, name in enumerate(clubs, 1)]
    parties = (
        english_list(labeled, article=None)
        + ' (collectively referred to as the "Renter")'
    )
    return "the Renter", parties, "The Renter hereby agrees", True


def _renter_sig_column(clubs: list[str], multi: bool) -> str:
    """
    Typst markup for the renter column of the signature grid — one
    signature + date block per organization in multi mode, stacked down
    the right-hand column. References the template's #TERM binding and
    #sig_cell helper. Organization names are emitted as escaped string
    literals (#"...") so they can never inject markup.
    """
    if not multi:
        return (
            "[\n"
            '  #text(size: 9.5pt, weight: "bold", fill: ink)[#TERM Executive Board]\n'
            "  #v(8pt)\n"
            '  #sig_cell([], "SIGNATURE", 54pt)\n'
            "  #v(28pt)\n"
            '  #sig_cell([], "DATE", 22pt)\n'
            "]"
        )
    blocks: list[str] = []
    for i, name in enumerate(clubs, 1):
        # Each block is unbreakable: if the execution area ever flows
        # across pages, a party's name and its lines stay together.
        blocks.append(
            "#block(breakable: false)[\n"
            "  #stack(dir: ttb, spacing: 6pt)[\n"
            f'    #text(size: 9.5pt, weight: "bold", fill: ink)[#"{typst_string(name)}"]\n'
            f'    #text(size: 8pt, fill: muted)[Organization {i}]\n'
            "  ]\n"
            '  #sig_cell([], "SIGNATURE", 40pt)\n'
            "  #v(6pt)\n"
            '  #sig_cell([], "DATE", 18pt)\n'
            "]"
        )
    # Always a single stack down the right column — never split into
    # sub-columns. With many organizations the stack can exceed one page;
    # «SIG_BLOCK_BREAKABLE» lets it flow instead of clipping (each block
    # above is unbreakable, so no party's lines ever split).
    return "[\n" + "\n  #v(16pt)\n".join(blocks) + "\n]"


def generate_contract(values: dict[str, Any]) -> bytes:
    # ── Required string fields (club identity resolved separately) ──
    base: dict[str, str] = {}
    for key, _placeholder, label, _hint in CONTRACT_FIELDS:
        if key == "club_name":
            continue
        v = values.get(key)
        if v is None or str(v).strip() == "":
            raise ValueError(f"missing required field: {key} ({label})")
        base[key] = str(v).strip()

    clubs = _resolve_clubs(values)
    term, parties, opening, multi = _party_terms(clubs)

    # ── Numeric validation (values still print as entered) ──
    price_val = _parse_money(base["price"], "rental fee")
    _parse_money(base["deposit"], "security deposit")
    max_guests_num = _parse_count(base["max_guests"], "maximum guests")
    base["max_guests"] = str(max_guests_num)
    monitors_num = _parse_count(base["monitors"], "sober monitors", minimum=0)
    base["monitors"] = str(monitors_num)

    same_day = _hhmm(base["end_time"]) > _hhmm(base["start_time"])

    raw_areas = values.get("areas") or []
    if not isinstance(raw_areas, (list, tuple)):
        raise ValueError("areas must be a list of area keys")
    areas: list[str] = list(raw_areas)
    for a in areas:
        if a not in AREA_LABELS:
            raise ValueError(f"unknown area: {a}")
    raw_cleared = values.get("cleared") or {}
    if not isinstance(raw_cleared, dict):
        raise ValueError("cleared must be an object mapping area keys to booleans")
    cleared: dict[str, bool] = dict(raw_cleared)

    guest_list      = bool(values.get("guest_list"))
    sound_system    = bool(values.get("sound_system"))
    lighting_system = bool(values.get("lighting_system"))
    sign            = bool(values.get("sign"))

    # ── Escaped string-literal bindings (the template references these
    #    with #TERM, #PRICE, … so user input stays inert text) ──
    repl: dict[str, str] = {
        "«TERM»":         typst_string(term),
        # Sentence-initial variant: "The Renter" for multi-org events so a
        # sentence never starts lowercase. Identical to TERM for a single
        # organization (a proper noun is already capitalized).
        "«TERM_CAP»":     typst_string("The Renter" if multi else term),
        "«PARTIES»":      typst_string(parties),
        "«OPENING»":      typst_string(opening),
        "«EVENT_DATE»":   typst_string(base["date"]),
        "«START_TIME»":   typst_string(base["start_time"]),
        "«END_TIME»":     typst_string(base["end_time"]),
        "«PRICE»":        typst_string(base["price"]),
        "«DEPOSIT»":      typst_string(base["deposit"]),
        "«MAX_GUESTS»":   typst_string(base["max_guests"]),
        "«NUM_MONITORS»": typst_string(base["monitors"]),
        # Clause 4a's absolute cap is the house capacity (200), unless the
        # agreed maximum is higher — then that number is the cap everywhere.
        "«HARD_CAP»":     str(max(200, max_guests_num)),
    }

    # ── Raw-markup substitutions. Built from fixed strings, computed
    #    numbers, and #TERM references — never from raw user input. ──
    repl["«END_DAY_PHRASE»"] = "" if same_day else " on the following day"

    repl["«GUEST_LIST_SENTENCE»"] = (
        "#TERM_CAP shall provide a guest list to Theta Xi Fraternity "
        "at least 5 days prior to the start of the event. "
        if guest_list else ""
    )

    amenity_names: list[str] = []
    if sound_system:
        amenity_names.append("sound system")
    if lighting_system:
        amenity_names.append("lighting system")
    if amenity_names:
        repl["«AMENITIES_SENTENCE»"] = (
            f"Theta Xi Fraternity will set up {english_list(amenity_names, article='the')} "
            "for the event. "
        )
    else:
        repl["«AMENITIES_SENTENCE»"] = ""

    repl["«ALLOWED_AREAS_LIST»"] = english_list([AREA_LABELS[k] for k in areas])

    if max_guests_num > 50:
        # Contingency price formula: ((price - 125) * (50 / max_guests)) * 0.75
        base_for_scale = max(0.0, price_val - 125.0)
        scale_factor = 50.0 / max_guests_num
        contingency_price = (base_for_scale * scale_factor) * 0.75
        contingency_price_fmt = f"{contingency_price:,.2f}"

        repl["«FIRE_PERMIT_CLAUSE»"] = (
            '#subclause("4d.")[As attendance is expected to exceed 50 guests, Theta Xi '
            'Fraternity is required to obtain a special event fire permit from the City of '
            'Berkeley. A fee of \\$125.00 has been included in the rental fee to cover the '
            'cost of this permit. #TERM_CAP agrees to comply with all '
            'fire safety regulations and occupancy limits specified by the permit.]\n\n'
            '#subclause("4e.")[Permit Contingency. Theta Xi Fraternity\'s ability to host more than '
            '50 guests is contingent upon the approval of the City of Berkeley fire '
            'permit. If the permit is denied or cannot be obtained for any reason, '
            'Theta Xi Fraternity shall notify #TERM immediately. '
            '#TERM_CAP may then elect to either (i) cancel the event for '
            'a full refund of all deposits and fees paid, or (ii) proceed with the '
            'event subject to a strict #strong[50-guest limit]. If the event proceeds under '
            'the 50-guest limit, the rental fee will be reduced according to the '
            'following procedure: first, the \\$125.00 permit fee is removed; second, '
            'the remaining balance is scaled proportionally to the reduced capacity '
            f'(50/{max_guests_num}); and third, an additional 25% "inconvenience credit" '
            'is applied to the resulting total. For this event, the reduced '
            f'contingency price is #strong[\\${contingency_price_fmt}].\n\n'
            'Should the event proceed at the reduced 50-guest capacity, '
            '#TERM agrees to the following additional restrictions: '
            'attendance is strictly capped at 50 persons; music and noise levels must '
            'be kept at a significantly lower volume than originally planned; and all '
            'guests must remain inside the Fraternity House and are prohibited from '
            'crowding or loitering on the sidewalk or outdoor areas. Theta Xi '
            'Fraternity reserves the right to immediately terminate the event and '
            'retain the security deposit in full if attendance exceeds 50 persons '
            'or if guests fail to comply with these noise and indoor-only restrictions.]'
        )
    else:
        repl["«FIRE_PERMIT_CLAUSE»"] = ""

    # Subclause 5b — furniture restoration. Either Theta Xi clears items
    # ahead of time, or the renter is on the hook for restoring them.
    cleared_keys = [k for k in areas if cleared.get(k)]
    if cleared_keys:
        cleared_desc = "; ".join(
            f"the {AREA_LABELS[k]} (Theta Xi Fraternity will move {AREA_CLEARING_DESC[k]})"
            for k in cleared_keys
        )
        repl["«SPACE_CLEARING_SUBCLAUSE»"] = (
            '#subclause("5b.")[For the following areas, Theta Xi Fraternity has agreed to '
            'clear items prior to the event and will restore them to their original positions '
            f'following the event: {cleared_desc}. In all other accessible areas, any furniture '
            'or items moved by #TERM or its guests during the event must be returned to their '
            'original positions before the conclusion of the rental period. Failure to restore '
            'moved items will be treated as damage under Section 06.]'
        )
    else:
        repl["«SPACE_CLEARING_SUBCLAUSE»"] = (
            '#subclause("5b.")[Any furniture or items moved by #TERM or its guests during '
            'the event must be returned to their original positions before the conclusion of the '
            'rental period. Failure to restore moved items will be treated as damage '
            'under Section 06.]'
        )

    # Subclause 5c — cleanup tier (derived from Pricing)
    cleanup_tier = str(values.get("cleanup_tier") or "basic").strip().lower()
    if cleanup_tier not in ("basic", "full"):
        raise ValueError("cleanup_tier must be 'basic' or 'full'")

    if cleanup_tier == "full":
        repl["«CLEANUP_TIER_CLAUSE»"] = (
            '#subclause("5c.")[Cleanup Tier — Full Service. Theta Xi Fraternity will provide '
            'full post-event cleanup services, including trash collection and disposal, '
            'wipe-down of obvious spills or sticky surfaces, and restoration of moved furniture '
            'and items to their original positions. Theta Xi Fraternity will mop the premises '
            'following the event regardless of cleanup tier. Any personal property, '
            'decorations, or equipment left behind by #TERM or its guests after the '
            'conclusion of the rental period may be treated as abandoned property and may '
            'be discarded at Theta Xi Fraternity\'s discretion; Theta Xi Fraternity is not '
            'responsible for loss or damage to such '
            'items.]'
        )
    else:
        repl["«CLEANUP_TIER_CLAUSE»"] = (
            '#subclause("5c.")[Cleanup Tier — Basic. #TERM_CAP is responsible for collecting all '
            'trash and disposables, placing them into bags, and disposing of them in the '
            'designated bins or dumpster, and for removing any personal property or decorations '
            'brought in for the event. #TERM_CAP is also responsible for restoring any moved '
            'furniture or items to their original positions before the conclusion of the '
            'rental period. Theta Xi Fraternity will mop the premises following the event '
            'regardless of cleanup tier.]'
        )

    # ── Signature block: Theta Xi on the left, one row per organization
    #    down the right column ──
    repl["«RENTER_SIG_COLUMN»"] = _renter_sig_column(clubs, multi)
    # The execution block is unbreakable so it never splits across pages —
    # but an unbreakable block taller than one page gets its overflow
    # silently clipped. Five renter organizations stacked in the right
    # column comes within a few points of a full page, so from there the
    # block is allowed to flow; each renter block is itself unbreakable,
    # so no party's lines ever split.
    repl["«SIG_BLOCK_BREAKABLE»"] = "true" if len(clubs) >= 5 else "false"

    if sign:
        d = datetime.date.today()
        repl["«IS_SIGNED»"] = "true"
        repl["«SIG_DATE»"]  = f"{d:%B} {d.day}, {d.year}"
    else:
        repl["«IS_SIGNED»"] = "false"
        repl["«SIG_DATE»"]  = ""

    return render_typst("contract.typ", repl)
