"use client";

import { useEffect, useState } from "react";
import { ApiCallError, generatePdf } from "@/lib/api";
import { AreaKey, useSharedData } from "@/lib/shared-state";
import { formatDateISO } from "@/lib/format";
import { StepIndicator, StepNav } from "@/components/StepNav";

const AREAS: { key: AreaKey; label: string; clearedDesc: string }[] = [
  { key: "living_room", label: "Living Room", clearedDesc: "the couches, tables, and carpet" },
  { key: "dining_room", label: "Dining Room", clearedDesc: "the dining table and chairs" },
  { key: "backyard",    label: "Backyard",    clearedDesc: "everything off the cement area in the center" },
];

export default function ContractPage() {
  const { hydrated, data, update } = useSharedData();
  // Sign is intentionally local + reset on each page visit.
  const [sign, setSign] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // ── Auto-fill from pricing on first visit ─────────────────────────
  // Only writes when the field is empty; if the user has typed something
  // we leave it alone. Pricing changes after this don't clobber.
  useEffect(() => {
    if (!hydrated) return;
    const breakdown = data.pricingBreakdown;
    const finalNum = parseFloat(data.finalPrice);

    // Rental price ← finalPrice (or pricingBreakdown.total) if not yet set
    if (!data.rentalPrice) {
      if (Number.isFinite(finalNum) && finalNum > 0) {
        update("rentalPrice", String(Math.round(finalNum)));
      } else if (breakdown?.total) {
        update("rentalPrice", String(Math.round(breakdown.total)));
      }
    }

    // Deposit ← pricingBreakdown.suggestedDeposit if not yet set
    if (!data.depositAmount && breakdown?.suggestedDeposit) {
      update("depositAmount", String(Math.round(breakdown.suggestedDeposit)));
    }

    // Max guests ← numGuests if not yet set
    if (!data.maxGuests && data.numGuests) {
      update("maxGuests", data.numGuests);
    }
    // Run only once after hydration; subsequent edits stay user-controlled.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated]);

  function setArea(key: AreaKey, on: boolean) {
    update("areas", { ...data.areas, [key]: on });
    if (!on) update("cleared", { ...data.cleared, [key]: false });
  }
  function setCleared(key: AreaKey, on: boolean) {
    update("cleared", { ...data.cleared, [key]: on });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setSuccess(null); setBusy(true);
    try {
      const selectedAreas = AREAS.filter(a => data.areas[a.key]).map(a => a.key);
      const clearedMap: Record<string, boolean> = {};
      selectedAreas.forEach(k => { clearedMap[k] = data.cleared[k]; });

      const { filename } = await generatePdf("/api/generate/contract", {
        club_name:  data.clubName,
        date:       formatDateISO(data.eventDate),  // "May 5, 2026"
        start_time: data.startTime,
        end_time:   data.endTime,
        price:      data.rentalPrice,
        deposit:    data.depositAmount,
        max_guests: data.maxGuests,
        monitors:   data.monitors,
        areas:      selectedAreas,
        cleared:    clearedMap,
        guest_list:      data.guestList,
        sound_system:    data.soundSystem,
        lighting_system: data.lightingSystem,
        sign,
      });
      setSuccess(`Downloaded ${filename}`);
    } catch (err) {
      setError(
        err instanceof ApiCallError ? err.message
        : err instanceof Error      ? err.message
        : "request failed",
      );
    } finally {
      setBusy(false);
    }
  }

  const txt = <K extends keyof typeof data>(k: K) =>
    (e: React.ChangeEvent<HTMLInputElement>) =>
      update(k, e.target.value as never);

  const eventDateReadable = data.eventDate ? formatDateISO(data.eventDate) : "";

  return (
    <form onSubmit={submit} className="space-y-8">
      <div>
        <StepIndicator current="contract" />
        <h1 className="page-title mt-6">Hosting Contract</h1>
        <p className="page-lede">
          Fill in the event details. Organization, event date, and (when available) rental fee
          and deposit are auto-filled from the previous steps.
        </p>
      </div>

      {/* ── Renter & Event ── */}
      <section className="card">
        <div className="card-header"><span className="card-title">Renter & Event</span></div>
        <div className="card-body grid gap-5 sm:grid-cols-2">
          <Field label="Organization">
            <input className="field-input" required placeholder="e.g. Pi Sigma Delta"
              value={hydrated ? data.clubName : ""} onChange={txt("clubName")} autoComplete="off" />
          </Field>
          <Field
            label="Event Date"
            hint={eventDateReadable ? `Will print as: ${eventDateReadable}` : undefined}
          >
            <input type="date" className="field-input" required
              value={hydrated ? data.eventDate : ""} onChange={txt("eventDate")} />
          </Field>
          <Field label="Start Time (24h)">
            <input className="field-input" required pattern="\d{1,2}:\d{2}" placeholder="e.g. 22:00"
              value={hydrated ? data.startTime : ""} onChange={txt("startTime")} autoComplete="off" />
          </Field>
          <Field label="End Time (24h)">
            <input className="field-input" required pattern="\d{1,2}:\d{2}" placeholder="e.g. 02:00"
              value={hydrated ? data.endTime : ""} onChange={txt("endTime")} autoComplete="off" />
          </Field>
        </div>
      </section>

      {/* ── Money + Capacity (side by side) ── */}
      <div className="grid gap-8 md:grid-cols-2">
        <section className="card">
          <div className="card-header"><span className="card-title">Fees</span></div>
          <div className="card-body grid gap-5">
            <Field
              label="Rental Fee (USD)"
              hint={data.pricingBreakdown && data.rentalPrice
                ? "Auto-filled from the negotiated price on the previous step."
                : undefined}
            >
              <input className="field-input" type="number" min={0} required placeholder="e.g. 1500"
                value={hydrated ? data.rentalPrice : ""} onChange={txt("rentalPrice")} />
            </Field>
            <Field
              label="Security Deposit (USD)"
              hint={data.pricingBreakdown?.suggestedDeposit
                && typeof data.pricingBreakdown.depositRate === "number"
                && data.depositAmount
                ? `Auto-filled at ${Math.round(data.pricingBreakdown.depositRate * 100)}% of total.`
                : undefined}
            >
              <input className="field-input" type="number" min={0} required placeholder="e.g. 100"
                value={hydrated ? data.depositAmount : ""} onChange={txt("depositAmount")} />
            </Field>
          </div>
        </section>

        <section className="card">
          <div className="card-header"><span className="card-title">Capacity & Monitors</span></div>
          <div className="card-body grid gap-5">
            <Field label="Maximum Guests">
              <input className="field-input" type="number" min={1} max={200} required
                placeholder="e.g. 150"
                value={hydrated ? data.maxGuests : ""} onChange={txt("maxGuests")} />
            </Field>
            <Field label="Sober Monitors">
              <input className="field-input" type="number" min={0} required
                placeholder="e.g. 4"
                value={hydrated ? data.monitors : ""} onChange={txt("monitors")} />
            </Field>
          </div>
        </section>
      </div>

      {/* ── Allowed Areas ── */}
      <section className="card">
        <div className="card-header">
          <span className="card-title">Allowed Areas</span>
          <span className="card-subtitle">Spaces guests may access during the event.</span>
        </div>
        <div className="card-body">
          <p className="text-[12.5px] text-muted mb-4 max-w-2xl leading-relaxed">
            For each enabled area, optionally have Theta Xi clear furniture beforehand —
            otherwise the renter is responsible for restoring moved items to their original
            positions before the rental period ends.
          </p>
          <div className="grid gap-x-8 sm:grid-cols-3">
            {AREAS.map(a => {
              const enabled = !!data.areas[a.key];
              return (
                <div key={a.key} className="border-t border-rule pt-3 sm:border-t-0 sm:pt-0">
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={hydrated ? enabled : false}
                      onChange={e => setArea(a.key, e.target.checked)}
                    />
                    <span className="font-semibold text-[14px]">{a.label}</span>
                  </label>
                  <label className={"check-row pl-6 text-muted " + (enabled ? "" : "disabled")}>
                    <input
                      type="checkbox"
                      disabled={!enabled}
                      checked={hydrated ? !!data.cleared[a.key] : false}
                      onChange={e => setCleared(a.key, e.target.checked)}
                    />
                    <span className="text-[12px] leading-snug">
                      Theta Xi clears beforehand ({a.clearedDesc})
                    </span>
                  </label>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── Guest List ── */}
      <section className="card">
        <div className="card-header">
          <span className="card-title">Guest List</span>
          <span className="card-subtitle">Optional advance roster for security review.</span>
        </div>
        <div className="card-body">
          <p className="text-[12.5px] text-muted mb-3 max-w-2xl leading-relaxed">
            When required, the organization must submit a written guest list at least 5 days
            before the event start time. Theta Xi reviews the list ahead of the event.
          </p>
          <label className="check-row">
            <input
              type="checkbox"
              checked={hydrated ? data.guestList : false}
              onChange={e => update("guestList", e.target.checked)}
            />
            <span className="text-[14px]">
              Require guest list 5 days in advance
            </span>
          </label>
        </div>
      </section>

      {/* ── Amenities ── */}
      <section className="card">
        <div className="card-header">
          <span className="card-title">Amenities</span>
          <span className="card-subtitle">Theta Xi sets up any selected amenities before the event.</span>
        </div>
        <div className="card-body grid gap-1 sm:grid-cols-2">
          <label className="check-row">
            <input
              type="checkbox"
              checked={hydrated ? data.soundSystem : false}
              onChange={e => update("soundSystem", e.target.checked)}
            />
            <span className="text-[14px]">Sound system</span>
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={hydrated ? data.lightingSystem : false}
              onChange={e => update("lightingSystem", e.target.checked)}
            />
            <span className="text-[14px]">Lighting system</span>
          </label>
        </div>
      </section>

      {/* ── Auto-sign ── */}
      <section className="card">
        <div className="card-header">
          <span className="card-title">Execution</span>
          <span className="card-subtitle">Optionally pre-sign the Theta Xi side.</span>
        </div>
        <div className="card-body">
          <p className="text-[12.5px] text-muted mb-3 max-w-2xl leading-relaxed">
            When enabled, the contract is generated with the Theta Xi Executive Board signature
            and today&rsquo;s date already filled in. The renter&rsquo;s side is left blank.
          </p>
          <label className="check-row">
            <input
              type="checkbox"
              checked={sign}
              onChange={e => setSign(e.target.checked)}
            />
            <span className="text-[14px]">Auto-sign Theta Xi side with today&rsquo;s date</span>
          </label>
        </div>
      </section>

      {/* ── Submit + step nav ── */}
      <div className="flex items-center justify-between gap-6 flex-wrap pt-2 border-t border-rule">
        <div className="flex-1 min-w-[200px] pt-4">
          {error && <p className="text-warn text-[13px]">{error}</p>}
          {success && <p className="text-ok text-[13px]">{success}</p>}
        </div>
        <button type="submit" disabled={busy || !hydrated} className="btn-primary mt-4">
          {busy ? "Generating…" : "Generate Contract"}
        </button>
      </div>

      <StepNav current="contract" />
    </form>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="field-label">{label}</label>
      {children}
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}
