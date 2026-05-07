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

    if sign:
        d = datetime.date.today()
        repl["«IS_SIGNED»"] = "true"
        repl["«SIG_DATE»"]  = f"{d:%B} {d.day}, {d.year}"
    else:
        repl["«IS_SIGNED»"] = "false"
        repl["«SIG_DATE»"]  = ""

    return render_typst("contract.typ", repl)
