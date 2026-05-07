"""
Shared helpers for typst-based PDF generation.

All generators follow the same pattern:
  1. Load a .typ template from backend/templates/
  2. Substitute placeholders (everything wrapped in « ... »)
  3. Stage the template + assets in a temp dir
  4. Run `typst compile` and return PDF bytes.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

# backend/generators/base.py  →  backend/
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PACKAGE_ROOT / "templates"
ASSETS_DIR = PACKAGE_ROOT / "assets"


class TypstNotFoundError(RuntimeError):
    """Raised when the `typst` CLI is not on PATH."""


class TypstCompileError(RuntimeError):
    """Raised when typst compilation fails. Carries stderr."""

    def __init__(self, stderr: str, returncode: int) -> None:
        super().__init__(f"typst compile failed (exit {returncode})\n{stderr}")
        self.stderr = stderr
        self.returncode = returncode


def find_typst() -> str:
    path = shutil.which("typst")
    if not path:
        raise TypstNotFoundError(
            "`typst` binary not found on PATH. Install it from "
            "https://github.com/typst/typst/releases — for example: "
            "`winget install --id Typst.Typst` on Windows, "
            "`brew install typst` on macOS, or "
            "`cargo install --locked typst-cli` on Linux."
        )
    return path


def slug(s: str) -> str:
    """Lowercase, ASCII, underscore-separated. Used for output filenames."""
    s = s.strip().lower().replace(" ", "_")
    out: list[str] = []
    prev_us = False
    for ch in s:
        ok = ("a" <= ch <= "z") or ("0" <= ch <= "9")
        if ok:
            out.append(ch)
            prev_us = False
        else:
            if not prev_us:
                out.append("_")
                prev_us = True
    result = "".join(out).strip("_")
    while "__" in result:
        result = result.replace("__", "_")
    return result or "partner"


def english_list(items: list[str], article: str = "the") -> str:
    if not items:
        return "no designated areas"
    prefixed = [f"{article} {item}" for item in items]
    if len(prefixed) == 1:
        return prefixed[0]
    if len(prefixed) == 2:
        return f"{prefixed[0]} and {prefixed[1]}"
    return ", ".join(prefixed[:-1]) + f", and {prefixed[-1]}"


def fmt_currency(amount: float | int) -> str:
    """`1500` → `$1,500.00`. Used by line items."""
    return f"${amount:,.2f}"


def typst_string(s: str) -> str:
    """
    Escape a Python string for safe inclusion inside a typst string literal.
    Typst uses backslash for escapes like JSON; double quotes and backslashes
    need escaping. Newlines convert to spaces because all sites that use
    this are inline (single-line text values).
    """
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", "")
    )


def render_typst(template_name: str, replacements: Mapping[str, str]) -> bytes:
    """
    Compile a typst template with placeholders to PDF bytes.

    `template_name`: filename in backend/templates/ (e.g. "contract.typ").
    `replacements`: { placeholder_token (with «»): replacement_string }.

    The shared assets (shield.png, signature.png) and the _shared.typ
    helper are staged into the same temp directory as the template.
    """
    typst_bin = find_typst()
    template_path = TEMPLATES_DIR / template_name
    if not template_path.is_file():
        raise FileNotFoundError(f"template not found: {template_path}")

    src = template_path.read_text(encoding="utf-8")
    for token, value in replacements.items():
        src = src.replace(token, value)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Copy every asset and every .typ helper into the tmp dir so
        # `#import "_shared.typ"` and `#image("shield.png")` resolve.
        for asset in ASSETS_DIR.glob("*"):
            if asset.is_file():
                (tmp_path / asset.name).write_bytes(asset.read_bytes())
        for helper in TEMPLATES_DIR.glob("_*.typ"):
            (tmp_path / helper.name).write_text(
                helper.read_text(encoding="utf-8"), encoding="utf-8"
            )

        typ_path = tmp_path / template_name
        typ_path.write_text(src, encoding="utf-8")
        out_pdf = tmp_path / "out.pdf"

        result = subprocess.run(
            [typst_bin, "compile", str(typ_path), str(out_pdf)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise TypstCompileError(result.stderr, result.returncode)

        return out_pdf.read_bytes()
