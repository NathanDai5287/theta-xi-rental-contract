#!/usr/bin/env python3
"""Render two test contracts (CLI + interactive) and open the PDFs."""
import os
import subprocess
import sys
from pathlib import Path

HERE   = Path(__file__).parent
SCRIPT = HERE / "render_contract.py"
PYTHON = sys.executable

# --- CLI test: next-day event (22:00 → 02:00 crosses midnight) --------------
CLI_ARGS = [
    "--club-name",   "Test Club",
    "--date",        "May 15, 2026",
    "--start-time",  "22:00",
    "--end-time",    "02:00",
    "--price",       "1500",
    "--deposit",     "100",
    "--max-guests",  "150",
    "--monitors",    "4",
    "--no-sign",
]
CLI_OUT = HERE / "test_cli_nextday.pdf"

# --- Interactive test: same-day event (18:00 → 22:00, auto-detected) --------
# Answers in field order: club_name, date, start_time, end_time, price,
# deposit, max_guests, monitors — then sign prompt.
INTERACTIVE_INPUT = "\n".join([
    "Test Club Interactive",
    "June 1, 2026",
    "18:00",
    "22:00",
    "2000",
    "150",
    "120",
    "5",
    "n",
]) + "\n"
INTERACTIVE_OUT = HERE / "test_interactive_sameday.pdf"


def run(label: str, extra_args: list[str], stdin: str | None = None) -> bool:
    print(f"\n{'─' * 55}")
    print(f"  {label}")
    print(f"{'─' * 55}")
    result = subprocess.run(
        [PYTHON, str(SCRIPT)] + extra_args,
        input=stdin,
        text=True,
    )
    if result.returncode != 0:
        print(f"  FAILED (exit {result.returncode})")
    return result.returncode == 0


def open_pdf(path: Path) -> None:
    if not path.exists():
        print(f"  {path.name}: not found, skipping")
        return
    print(f"  Opening {path.name} …")
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)])
    else:
        subprocess.run(["xdg-open", str(path)])


def main() -> None:
    ok_cli         = run("CLI — next-day  (22:00 → 02:00)",
                         CLI_ARGS + ["-o", str(CLI_OUT)])
    ok_interactive = run("Interactive — same-day (18:00 → 22:00)",
                         ["-o", str(INTERACTIVE_OUT)],
                         stdin=INTERACTIVE_INPUT)

    print()
    for path, ok in [(CLI_OUT, ok_cli), (INTERACTIVE_OUT, ok_interactive)]:
        if ok:
            open_pdf(path)

    sys.exit(0 if (ok_cli and ok_interactive) else 1)


if __name__ == "__main__":
    main()
