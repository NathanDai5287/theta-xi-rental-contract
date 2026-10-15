"""Versioned hosting payment timing; missing values retain legacy terms."""
ADVANCE_PAYMENT_TIMING = "day_before"
LEGACY_PAYMENT_TIMING = "after_conclusion"


def rental_payment_timing(values):
    timing = values.get("rental_payment_timing", LEGACY_PAYMENT_TIMING)
    if timing not in (ADVANCE_PAYMENT_TIMING, LEGACY_PAYMENT_TIMING):
        raise ValueError("unknown rental payment timing")
    return timing


def snapshot_payment_timing(snapshot):
    return rental_payment_timing({"rental_payment_timing": snapshot.get("rentalPaymentTiming", LEGACY_PAYMENT_TIMING)})
