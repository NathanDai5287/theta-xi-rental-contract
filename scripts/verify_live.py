"""Live verification against https://host-api.cal.taxi after deploy.

Checks the three fixes with a 6-organization event:
  1. DEP invoice stays at 2 pages with the constant contact note.
  2. RNT invoice stays at 2 pages.
  3. Signed contract renders all 6 orgs (single right column).

Saves PDFs to %TEMP% for visual inspection.
"""

import json
import os
import re
import tempfile
import urllib.request
from io import BytesIO
from pathlib import Path

import pypdf

# Path to the admin app's env file holding HOST_BACKEND_KEY.
ENV_FILE = Path(os.environ.get(
    "ADMIN_APP_ENV",
    r"C:\Users\natha\Programming\admin-cal-taxi\.env.local",
))
env = ENV_FILE.read_text()
KEY = re.search(r"HOST_BACKEND_KEY=(.+)", env).group(1).strip().strip('"')

ORGS = [
    "The Delta Project",
    "Alpha Alpha",
    "Beta Beta",
    "Gamma Gamma",
    "Delta Delta",
    "Epsilon Epsilon",
]
JOINED = ", ".join(ORGS[:-1]) + ", and " + ORGS[-1]


def post(route: str, payload: dict) -> bytes:
    req = urllib.request.Request(
        f"https://host-api.cal.taxi/api/generate/{route}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Admin-Key": KEY,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def pages(pdf: bytes) -> list[str]:
    return [(p.extract_text() or "") for p in pypdf.PdfReader(BytesIO(pdf)).pages]


invoice_base = {
    "club_name": JOINED,
    "event_date": "2026-10-03",
    "issue_date": "September 23, 2026",
    "due_date": "October 3, 2026",
}

failures = []

for kind, extra in [
    ("deposit", {"amount": 500}),
    ("rental", {"line_items": [
        {"description": "Base rental — Theta Xi Fraternity House", "amount": "940.00"},
        {"description": "Capacity fee — 200 guests (50 over included threshold)", "amount": "380.00"},
        {"description": "Fire permit (Required for > 50 guests)", "amount": "120.00"},
        {"description": "Date surcharge — Weekend night (Fri/Sat)", "amount": "280.00"},
        {"description": "Cleanup — Basic", "amount": "240.00"},
    ]}),
]:
    pdf = post(f"invoice/{kind}", {**invoice_base, **extra})
    out = Path(tempfile.gettempdir()) / f"live-{kind}.pdf"
    out.write_bytes(pdf)
    pg = pages(pdf)
    contact_pages = [i + 1 for i, t in enumerate(pg) if "Questions about this invoice" in t]
    status = "OK" if len(pg) == 2 else "FAIL"
    if len(pg) != 2:
        failures.append(f"{kind} invoice: {len(pg)} pages (want 2)")
    print(f"{kind.upper()} invoice: {len(pg)} pages [{status}], "
          f"contact note on pages {contact_pages}, saved to {out}")

contract_pdf = post("contract", {
    "club_names": ORGS,
    "date": "October 3, 2026",
    "start_time": "22:00",
    "end_time": "02:00",
    "price": "1960",
    "deposit": "500",
    "max_guests": "200",
    "monitors": "4",
    "areas": ["living_room"],
    "cleared": {"living_room": True},
    "guest_list": True,
    "sound_system": True,
    "lighting_system": False,
    "cleanup_tier": "basic",
    "sign": True,
})
out = Path(tempfile.gettempdir()) / "live-contract.pdf"
out.write_bytes(contract_pdf)
pg = pages(contract_pdf)
full = " ".join(" ".join(t.split()) for t in pg)
missing = [o for o in ORGS if o not in full]
status = "OK" if not missing else "FAIL"
if missing:
    failures.append(f"contract missing orgs: {missing}")
print(f"Contract: {len(pg)} pages, all 6 orgs present: {not missing} [{status}], saved to {out}")
for o in ORGS:
    print(f"  {o}: {full.count(o)} occurrence(s)")

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("All live checks passed.")
