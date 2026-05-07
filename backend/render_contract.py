#!/usr/bin/env python3
"""
render_contract.py — interactive CLI for the Theta Xi Hosting Contract.

Run from the backend/ directory:
    python render_contract.py

Same-day vs next-day is auto-detected from the times. The shield logo,
signature image, and typst template all live under backend/templates/
and backend/assets/ — see generators/ for the rendering pipeline.

Requires the `typst` binary on PATH:
    Windows  winget install --id Typst.Typst
    macOS    brew install typst
    Linux    cargo install --locked typst-cli
"""
import sys
from pathlib import Path

# Allow `python render_contract.py` to import the sibling `generators/` package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generators import generate_contract  # noqa: E402
from generators.base import slug          # noqa: E402
from generators.contract import (         # noqa: E402
    AREA_LABELS,
    CONTRACT_FIELDS,
)


def prompt(label: str, hint: str) -> str:
    while True:
        try:
            s = input(f"  {label} ({hint}): ").strip()
        except EOFError:
            sys.exit("\naborted")
        if s:
            return s
        print("    ↳ value required")


def prompt_yn(label: str, default: bool = False) -> bool:
    suffix = "[y/N]" if not default else "[Y/n]"
    while True:
        try:
            s = input(f"  {label} {suffix}: ").strip().lower()
        except EOFError:
            sys.exit("\naborted")
        if not s:
            return default
        if s in ("y", "yes"):
            return True
        if s in ("n", "no"):
            return False
        print("    ↳ please answer y or n")


def main() -> None:
    print("Theta Xi Hosting Contract — fill in the fields below:")
    values: dict = {}
    for name, _placeholder, label, hint in CONTRACT_FIELDS:
        values[name] = prompt(label, hint)

    print("\nAllowed areas — toggle which spaces guests may access:")
    areas: list[str] = []
    for key, label in AREA_LABELS.items():
        if prompt_yn(f"  Include {label}?"):
            areas.append(key)

    cleared: dict[str, bool] = {}
    for key in areas:
        cleared[key] = prompt_yn(
            f"  Clear {AREA_LABELS[key]} beforehand? "
            "(Theta Xi will move items)"
        )
    values["areas"] = areas
    values["cleared"] = cleared

    print()
    values["guest_list"]      = prompt_yn("Require guest list 5 days in advance?")
    values["sound_system"]    = prompt_yn("Include sound system? (Theta Xi will set up)")
    values["lighting_system"] = prompt_yn("Include lighting system? (Theta Xi will set up)")
    values["sign"]            = prompt_yn("Auto-sign the Theta Xi side with today's date?")

    pdf_bytes = generate_contract(values)

    out_pdf = Path(f"theta_xi_{slug(values['club_name'])}_contract.pdf").resolve()
    out_pdf.write_bytes(pdf_bytes)
    print(f"\nwrote {out_pdf}")


if __name__ == "__main__":
    main()
