"""
Contract generator. Returns PDF bytes for a Theta Xi hosting contract.

The shape of the input dict matches what the original render_contract.py
collected interactively — see CONTRACT_FIELDS for required keys.
"""
from __future__ import annotations

import datetime
from typing import Any, TypedDict

from .base import english_list, render_typst


# (key, placeholder, prompt_label, hint)
CONTRACT_FIELDS: list[tuple[str, str, str, str]] = [
    ("club_name",  "«CLUB_NAME»",    "Club name",                      "e.g. Pi Sigma Delta"),
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
    h, m = s.split(":")
    return int(h), int(m)


def generate_contract(values: dict[str, Any]) -> bytes:
    # ── Required string fields ──
    base: dict[str, str] = {}
    for key, _placeholder, label, _hint in CONTRACT_FIELDS:
        v = values.get(key)
        if v is None or str(v).strip() == "":
            raise ValueError(f"missing required field: {key} ({label})")
        base[key] = str(v).strip()

    same_day = _hhmm(base["end_time"]) > _hhmm(base["start_time"])

    areas: list[str] = list(values.get("areas") or [])
    for a in areas:
        if a not in AREA_LABELS:
            raise ValueError(f"unknown area: {a}")
    cleared: dict[str, bool] = dict(values.get("cleared") or {})

    guest_list      = bool(values.get("guest_list"))
    sound_system    = bool(values.get("sound_system"))
    lighting_system = bool(values.get("lighting_system"))
    sign            = bool(values.get("sign"))

    # ── Build replacement map ──
    repl: dict[str, str] = {placeholder: base[key] for key, placeholder, *_ in CONTRACT_FIELDS}

    repl["«END_DAY_PHRASE»"] = "" if same_day else " on the following day"

    repl["«GUEST_LIST_SENTENCE»"] = (
        f"{base['club_name']} shall provide a guest list to Theta Xi Fraternity "
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

    try:
        max_guests_num = int(base["max_guests"])
    except ValueError:
        max_guests_num = 0

    if max_guests_num > 50:
        try:
            price_val = float(base["price"])
        except ValueError:
            price_val = 0.0

        # Calculate contingency price using the user's formula:
        # ((price - 125) * (50 / max_guests)) * 0.75
        base_for_scale = max(0.0, price_val - 125.0)
        scale_factor = 50.0 / max_guests_num
        contingency_price = (base_for_scale * scale_factor) * 0.75
        contingency_price_fmt = f"{contingency_price:,.2f}"

        repl["«FIRE_PERMIT_CLAUSE»"] = (
            f'#subclause("4d.")[As attendance is expected to exceed 50 guests, Theta Xi '
            f'Fraternity is required to obtain a special event fire permit from the City of '
            f'Berkeley. A fee of \$125.00 has been included in the rental fee to cover the '
            f'cost of this permit. {base["club_name"]} agrees to comply with all '
            f'fire safety regulations and occupancy limits specified by the permit.]\n\n'
            f'#subclause("4e.")[Permit Contingency. Theta Xi\'s ability to host more than '
            f'50 guests is contingent upon the approval of the City of Berkeley fire '
            f'permit. If the permit is denied or cannot be obtained for any reason, '
            f'Theta Xi shall notify {base["club_name"]} immediately. '
            f'{base["club_name"]} may then elect to either (i) cancel the event for '
            f'a full refund of all deposits and fees paid, or (ii) proceed with the '
            f'event subject to a strict #strong[50-guest limit]. If the event proceeds under '
            f'the 50-guest limit, the rental fee will be reduced according to the '
            f'following procedure: first, the \$125.00 permit fee is removed; second, '
            f'the remaining balance is scaled proportionally to the reduced capacity '
            f'(50/{max_guests_num}); and third, an additional 25% "inconvenience credit" '
            f'is applied to the resulting total. For this event, the reduced '
            f'contingency price is #strong[\${contingency_price_fmt}].\n\n'
            f'Should the event proceed at the reduced 50-guest capacity, '
            f'{base["club_name"]} agrees to the following additional restrictions: '
            f'attendance is strictly capped at 50 persons; music and noise levels must '
            f'be kept at a significantly lower volume than originally planned; and all '
            f'guests must remain inside the Fraternity House and are prohibited from '
            f'crowding or loitering on the sidewalk or outdoor areas. Theta Xi '
            f'Fraternity reserves the right to immediately terminate the event and '
            f'retain the security deposit in full if attendance exceeds 50 persons '
            f'or if guests fail to comply with these noise and indoor-only restrictions.]'
        )
    else:
        repl["«FIRE_PERMIT_CLAUSE»"] = ""

    # Subclause 5b — furniture restoration. Either Theta Xi clears items
    # ahead of time, or the renter is on the hook for restoring them.
    club = base["club_name"]
    cleared_keys = [k for k in areas if cleared.get(k)]
    if cleared_keys:
        cleared_desc = "; ".join(
            f"the {AREA_LABELS[k]} (Theta Xi will move {AREA_CLEARING_DESC[k]})"
            for k in cleared_keys
        )
        repl["«SPACE_CLEARING_SUBCLAUSE»"] = (
            f'#subclause("5b.")[For the following areas, Theta Xi Fraternity has agreed to '
            f'clear items prior to the event and will restore them to their original positions '
            f'following the event: {cleared_desc}. In all other accessible areas, any furniture '
            f'or items moved by {club} or its guests during the event must be returned to their '
            f'original positions before the conclusion of the rental period. Failure to restore '
            f'moved items will be treated as damage under Section 06.]'
        )
    else:
        repl["«SPACE_CLEARING_SUBCLAUSE»"] = (
            f'#subclause("5b.")[Any furniture or items moved by {club} or its guests during '
            f'the event must be returned to their original positions before the conclusion of '
            f'the rental period. Failure to restore moved items will be treated as damage '
            f'under Section 06.]'
        )

    # Subclause 5c — cleanup tier (derived from Pricing)
    cleanup_tier = str(values.get("cleanup_tier") or "basic").strip().lower()
    if cleanup_tier not in ("basic", "full"):
        raise ValueError("cleanup_tier must be 'basic' or 'full'")

    if cleanup_tier == "full":
        repl["«CLEANUP_TIER_CLAUSE»"] = (
            f'#subclause("5c.")[Cleanup Tier — Full Service. Theta Xi Fraternity will provide '
            f'full post-event cleanup services, including trash collection and disposal, '
            f'wipe-down of obvious spills or sticky surfaces, and restoration of moved furniture '
            f'and items to their original positions. Theta Xi will mop the premises following '
            f'the event regardless of cleanup tier. Any personal property, decorations, or '
            f'equipment left behind by {club} or its guests after the conclusion of the rental '
            f'period may be treated as abandoned property and may be discarded at Theta Xi '
            f'Fraternity\'s discretion; Theta Xi is not responsible for loss or damage to such '
            f'items.]'
        )
    else:
        repl["«CLEANUP_TIER_CLAUSE»"] = (
            f'#subclause("5c.")[Cleanup Tier — Basic. {club} is responsible for collecting all '
            f'trash and disposables, placing them into bags, and disposing of them in the '
            f'designated bins or dumpster, and for removing any personal property or decorations '
            f'brought in for the event. {club} is also responsible for restoring any moved '
            f'furniture or items to their original positions before the conclusion of the '
            f'rental period. Theta Xi will mop the premises following the event regardless of '
            f'cleanup tier.]'
        )

    if sign:
        d = datetime.date.today()
        repl["«IS_SIGNED»"] = "true"
        repl["«SIG_DATE»"]  = f"{d:%B} {d.day}, {d.year}"
    else:
        repl["«IS_SIGNED»"] = "false"
        repl["«SIG_DATE»"]  = ""

    return render_typst("contract.typ", repl)
