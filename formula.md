# Party Rental Pricing Model

## Formula

The total price $T$ is computed as:

$$
T = \max\Bigg(\Big(\underbrace{B}_{\text{base}} + \underbrace{C_p}_{\text{capacity}} + \underbrace{A}_{\text{alcohol}} + \underbrace{P}_{\text{protection}} + \underbrace{D}_{\text{date}} + \underbrace{S_u}_{\text{setup}} + \underbrace{S_c}_{\text{cleanup}}\Big) \times W - R,\;\; 0\Bigg)
$$

where each variable is defined below.

---

### Variable Definitions

**B — Base Rate ($)**
The minimum flat fee charged for any event.
Default: `200`

**C_p — Capacity Fee ($)**
Extra charge per guest above a threshold.

$$C_p = \max(n - n_0, \; 0) \times c$$

- $n$ = number of guests
- $n_0$ = included guest threshold (default: `20`)
- $c$ = per-guest rate (default: `$2.50`)

**A — Alcohol Surcharge ($)**
Flat fee based on alcohol tier.

| Tier | Label | Default $ |
|------|-------|-----------|
| 0 | No alcohol | 0 |
| 1 | Beer & wine | 75 |
| 2 | Full open bar | 175 |

**P — Property Protection Fee ($)**
Based on expected cleanliness risk.

| Tier | Label | Default $ |
|------|-------|-----------|
| 0 | Very clean | 0 |
| 1 | Average | 50 |
| 2 | Messy | 150 |
| 3 | Disaster likely | 300 |

**D — Date Surcharge ($)**
Opportunity cost for inconvenient scheduling.

| Tier | Label | Default $ |
|------|-------|-----------|
| 0 | Free anyway | 0 |
| 1 | Slight | 50 |
| 2 | Had plans | 125 |
| 3 | Holiday / major | 250 |

**S_u — Setup Fee ($)**

| Tier | Label | Default $ |
|------|-------|-----------|
| 0 | They handle it | 0 |
| 1 | Light setup | 75 |
| 2 | Full production | 200 |

**S_c — Cleanup Fee ($)**

| Tier | Label | Default $ |
|------|-------|-----------|
| 0 | They clean | 0 |
| 1 | Light cleanup | 75 |
| 2 | Full service | 200 |

**W — Wealth / Budget Multiplier**
Scales the entire subtotal based on the client's ability to pay.

| Tier | Label | Multiplier |
|------|-------|------------|
| 0 | Budget-conscious | 0.85 |
| 1 | Average | 1.00 |
| 2 | Well-funded | 1.25 |
| 3 | Corporate | 1.60 |

**R — Relationship Discount ($)**
Applied as a percentage of the post-multiplier subtotal. A negative `r` produces a surcharge (R < 0, which increases T).

$$R = \Big(B + C_p + A + P + D + S_u + S_c\Big) \times W \times r$$

| Tier | Label | r (%) | Net effect |
|------|-------|-------|------------|
| -1 | Known issues — messy, obnoxious, non-compliant | −75% | +75% surcharge |
| 0 | Strangers — unknown group, no prior history | 0% | no adjustment |
| 1 | Acquaintance — met before, limited history | 5% | −5% discount |
| 2 | Friend of friend — vouched for | 12% | −12% discount |
| 3 | Good friend — well known, trusted | 22% | −22% discount |
| 4 | Close family / inner circle | 35% | −35% discount |

---

## Fully Expanded

$$
T = \max\Bigg(\Big(B + \max(n - n_0, 0) \cdot c + A + P + D + S_u + S_c\Big) \times W \times (1 - r),\;\; 0\Bigg)
$$

---

## Tweakable Constants Summary

| Constant | Description | Default |
|----------|-------------|---------|
| `B` | Base rate | $200 |
| `n_0` | Included guests | 20 |
| `c` | Per-guest rate | $2.50 |
| `A[0..2]` | Alcohol tiers | 0, 75, 175 |
| `P[0..3]` | Protection tiers | 0, 50, 150, 300 |
| `D[0..3]` | Date tiers | 0, 50, 125, 250 |
| `S_u[0..2]` | Setup tiers | 0, 75, 200 |
| `S_c[0..2]` | Cleanup tiers | 0, 75, 200 |
| `W[0..3]` | Wealth multipliers | 0.85, 1.0, 1.25, 1.6 |
| `r[-1..4]` | Relationship tiers | −75% (surcharge), 0%, 5%, 12%, 22%, 35% |

Change any of these constants in the code below to calibrate to your market.
