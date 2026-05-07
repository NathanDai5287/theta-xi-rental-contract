"use client";

import { useEffect } from "react";
import PricingCalculator from "@/components/PricingCalculator";
import { StepIndicator, StepNav } from "@/components/StepNav";
import { useSharedData } from "@/lib/shared-state";

const fmtUSD = (n: number) =>
  "$" + n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 });

export default function PricingPage() {
  const { hydrated, data, update } = useSharedData();

  // If finalPrice has never been set and we have a computed total, seed it.
  // After that, finalPrice is whatever the user typed (even if cleared).
  useEffect(() => {
    if (!hydrated) return;
    if (data.finalPrice === "" && data.pricingBreakdown?.total) {
      update("finalPrice", String(Math.round(data.pricingBreakdown.total)));
    }
  }, [hydrated, data.finalPrice, data.pricingBreakdown?.total, update]);

  const calcTotal = data.pricingBreakdown?.total ?? null;
  const finalNum  = parseFloat(data.finalPrice) || 0;
  const overridden =
    calcTotal !== null && finalNum > 0 && Math.abs(finalNum - calcTotal) > 0.01;

  return (
    <div className="space-y-10">
      <div>
        <StepIndicator current="pricing" />
        <h1 className="page-title mt-6">Pricing</h1>
        <p className="page-lede">
          Pick the tier for each cost factor. The breakdown updates live. After you&rsquo;ve
          settled on a number, the negotiated total below feeds the contract and the rental
          invoice.
        </p>
      </div>

      <PricingCalculator />

      {/* ── Negotiated Price ── */}
      <section className="card">
        <div className="card-header">
          <span className="card-title">Negotiated Price</span>
          <span className="card-subtitle">Final number that will appear on the contract and invoices.</span>
        </div>
        <div className="card-body grid gap-6 sm:grid-cols-[260px_1fr] items-start">
          <div>
            <label className="field-label">Final Price (USD)</label>
            <input
              type="number"
              min={0}
              step={1}
              className="field-input"
              placeholder={calcTotal !== null ? String(Math.round(calcTotal)) : "e.g. 1500"}
              value={hydrated ? data.finalPrice : ""}
              onChange={e => update("finalPrice", e.target.value)}
            />
            {calcTotal !== null && (
              <button
                type="button"
                onClick={() => update("finalPrice", String(Math.round(calcTotal)))}
                className="btn-link mt-2"
                disabled={!overridden}
              >
                Reset to calculated ({fmtUSD(Math.round(calcTotal))})
              </button>
            )}
          </div>

          <div className="text-[13px] text-muted leading-relaxed">
            {calcTotal === null ? (
              <p>The calculator hasn&rsquo;t produced a total yet — set guests above first.</p>
            ) : overridden ? (
              <p>
                You&rsquo;ve overridden the calculator. The contract&rsquo;s rental fee and the
                rental invoice&rsquo;s line items will scale to <strong className="text-ink">{fmtUSD(finalNum)}</strong>.
              </p>
            ) : (
              <p>
                Currently matches the calculator. You can override here if you negotiate a
                different price; line items on the rental invoice will scale proportionally.
              </p>
            )}
            {data.pricingBreakdown
              && typeof data.pricingBreakdown.suggestedDeposit === "number"
              && typeof data.pricingBreakdown.depositRate === "number" && (
              <p className="mt-3 text-[12px]">
                Suggested security deposit:&nbsp;
                <strong className="text-ink">{fmtUSD(data.pricingBreakdown.suggestedDeposit)}</strong>
                <span className="text-muted">
                  {" "}({Math.round(data.pricingBreakdown.depositRate * 100)}% of total)
                </span>
              </p>
            )}
          </div>
        </div>
      </section>

      <StepNav current="pricing" />
    </div>
  );
}
