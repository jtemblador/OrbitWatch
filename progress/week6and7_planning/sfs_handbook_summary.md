# SFS Handbook Summary — Key Findings for OrbitWatch

**Source:** 18 & 19 SDS Spaceflight Safety Handbook for Satellite Operators, V1.7 (April 2023)

---

## Who Does What

- **18 SDS** (Vandenberg SFB): Space surveillance, maintains the space catalog, orbit determination, tracking sensor tasking, anomaly/deorbit/reentry support
- **19 SDS** (Dahlgren Naval Base): On-orbit conjunction assessment (CA), collision avoidance (COLA), launch conjunction assessment (LCA), generates CDMs
- **Space-Track.org**: Public website for sharing SSA data — CDMs, TLEs/GPs, satellite catalog, ephemeris upload/download

---

## How 19 SDS Screens for Conjunctions

### The Process (Figure 1 in handbook)

1. Update orbital data (Differential Corrections from sensor observations)
2. Propagate ephemeris forward
3. Screen all RSO trajectories against each other
4. Identify conjunction candidates within screening volumes
5. Place close ones on a "Concern List" for refined screening
6. Generate CDMs (Conjunction Data Messages)
7. Notify satellite operators
8. Request more sensor observations if needed
9. **Repeat** — this is a continuous refinement cycle, not a one-shot scan

### Three Types of Screening

1. **HAC vs HAC** — SP catalog data screened against itself (what we're essentially doing with SGP4)
2. **O/O Ephemeris vs HAC** — operator-provided ephemeris screened against the catalog
3. **O/O Ephemeris vs O/O Ephemeris** — operator ephemeris screened against other operators' ephemeris

### Screening Schedule

- Full catalog (HAC vs HAC) screened once every 24 hours
- Concern List screenings every 8 hours
- O/O Ephemeris screenings: Special (high-interest) within 4 hours, Operational within 8-12 hours

---

## Screening Volumes (CRITICAL for our thresholds)

19 SDS uses **different screening volumes for different orbit regimes**. Not a single distance threshold — an asymmetric box in Radial / In-Track / Cross-Track dimensions:

### HAC Screening Volumes (Table 3)

| Regime | Definition | Propagation | Radial | In-Track | Cross-Track |
|--------|-----------|-------------|--------|----------|-------------|
| **LEO 1** | Perigee <= 500 km | 5 days | **0.4 km** | **44 km** | **51 km** |
| **LEO 2** | 500 < Perigee <= 750 km | 5 days | **0.4 km** | **25 km** | **25 km** |
| **LEO 3** | 750 < Perigee <= 1200 km | 5 days | **0.4 km** | **12 km** | **12 km** |
| **LEO 4** | 1200 < Perigee <= 2000 km | 5 days | **0.4 km** | **2 km** | **2 km** |
| **Deep Space** | Period > 1300 min, e < 0.25, inc < 35 | 10 days | **10 km** | **10 km** | **10 km** |

### O/O Ephemeris Screening Volumes (Table 4)

| Regime | Propagation | Radial | In-Track | Cross-Track |
|--------|-------------|--------|----------|-------------|
| Deep Space (Period > 225 min) | 10 days | 20 km | 20 km | 20 km |
| Near Earth (Period < 225 min) | 7 days | 2 km | 25 km | 25 km |

### Why the asymmetry matters

Radial position is very well-determined (~0.4 km screening threshold for LEO). Along-track timing is the dominant uncertainty — hence the much wider 25-51 km thresholds in In-Track and Cross-Track. **This means a simple Euclidean distance threshold is NOT what the industry uses.** They use an asymmetric box in RTN coordinates.

---

## Reporting Criteria (When operators get notified)

### Basic Reporting (Table 5)

| Regime | CDM Criteria | Emergency Criteria |
|--------|-------------|-------------------|
| Near Earth HAC | TCA <= 3 days, miss <= 1 km, **Pc >= 1e-7** | TCA <= 3 days, miss <= 1 km, **Pc >= 1e-4** |
| Near Earth O/O Eph | TCA <= 3 days, miss <= 1 km, **Pc >= 1e-7** | TCA <= 3 days, miss <= 1 km, **Pc >= 1e-4** |
| Deep Space HAC | TCA <= 10 days, miss <= 5 km | TCA <= 3 days, miss <= 5 km |
| Deep Space O/O Eph | TCA <= 10 days, miss <= 5 km | TCA <= 3 days, miss <= 5 km |

### Key thresholds

- **Pc >= 1e-7**: Generate CDM and post to Space-Track
- **Pc >= 1e-4**: Emergency — CDM + Close Approach Notification (CAN) email
- **Public CDMs on Space-Track**: Only events with Pc >= 1e-4

---

## Probability of Collision (Pc) — How It's Computed (Annex A)

### Required Inputs at TCA

1. **Object sizes** (both primary and secondary) — AREA_PC in m^2 or Exclusion Volume Radius in meters
2. **Position and velocity vectors** — inertial (typically ITRF), km and km/s
3. **3x3 position covariance** — for BOTH objects, in RTN frame (Radial, Transverse, Normal)

### The Math

1. Compute relative velocity vector at TCA
2. Define the **collision plane** — perpendicular to the relative velocity vector
3. Project the combined 3x3 covariance (sum of both objects' covariances in same frame) onto the 2D collision plane -> yields a 2x2 covariance matrix C
4. Combined object size d = sum of both exclusion volume radii
5. Compute the 2D Gaussian integral:

```
Pc = 1/(2*pi*|Det(C)|^0.5) * integral over (x^2+y^2 <= d^2) of exp(-0.5*(r-r_s/p)^T * C^-1 * (r-r_s/p)) dx dy
```

Where r_s/p is the position of the secondary relative to the primary in the collision plane.

### Implementation Notes

- 19 SDS uses error functions (ERF) for computing the double integral
- They integrate over a **square circumscribing the circle** of radius d (slight overestimate, conservative)
- The "hyperkinetic" assumption means relative motion is rectilinear (straight-line) during the encounter — valid for LEO where closing speeds are ~10-15 km/s
- Covariance assumed constant during the encounter

### Assumptions Built Into Pc

- Object sizes known or upper-bounded
- Gaussian position uncertainty
- Encounter is hyperkinetic (short duration, rectilinear)
- Covariance known and constant throughout encounter
- Primary and secondary errors are independent
- Covariance is neither "too large" nor "too small" (guards against unrealistic values)

### Covariance Realism Warning

> "Another concern is covariance realism: does the covariance used in the computation of Pc truly reflect the uncertainty in the state vectors at TCA? Because quantitative studies have shown that covariance is often underestimated, empirical techniques have been devised to scale or otherwise inflate the covariance."

---

## CDM Fields (Annex C) — What a Real Conjunction Record Contains

### Event-Level Fields
- `TCA` — Time of Closest Approach (UTC)
- `MISS_DISTANCE` — overall separation in meters at TCA
- `RELATIVE_SPEED` — closing speed in m/s
- `RELATIVE_POSITION_R/T/N` — separation in RTN frame (meters)
- `RELATIVE_VELOCITY_R/T/N` — relative velocity in RTN frame (m/s)
- `COLLISION_PROBABILITY` — Pc value (0.0 to 1.0)
- `COLLISION_PROBABILITY_METHOD` — typically "FOSTER-1992"

### Per-Object Fields (for both OBJECT 1 and OBJECT 2)
- `OBJECT_DESIGNATOR` — NORAD catalog ID
- `OBJECT_NAME` — common name
- `OBJECT_TYPE` — PAYLOAD, ROCKET BODY, DEBRIS, UNKNOWN, OTHER
- `X, Y, Z` — position vector in km
- `X_DOT, Y_DOT, Z_DOT` — velocity vector in km/s
- `AREA_PC` — area used in Pc calculation (m^2)
- `MANEUVERABLE` — YES, NO, N/A
- `REF_FRAME` — typically ITRF
- `COVARIANCE_METHOD` — CALCULATED or DEFAULT

### Per-Object Covariance (6x6 lower triangular)
- Position: `CR_R, CT_R, CT_T, CN_R, CN_T, CN_N` (in m^2)
- Position-Velocity cross: `CRDOT_R` through `CNDOT_N` (in m^2/s)
- Velocity: `CRDOT_RDOT` through `CNDOT_NDOT` (in m^2/s^2)
- Plus drag and SRP covariance rows

### Quality Indicators
- `TIME_LASTOB_START/END` — observation span for orbit determination
- `OBS_AVAILABLE`, `OBS_USED` — how many observations went into the OD
- `RESIDUALS_ACCEPTED` — percentage of residuals accepted
- `WEIGHTED_RMS` — fit quality
- `COVARIANCE_METHOD` — "CALCULATED" is real, "DEFAULT" means assumed values (less trustworthy)

---

## Key Takeaways for OrbitWatch

1. **Pc requires covariance** — miss distance alone is not the industry standard. The industry classifies risk by Pc, not by distance.

2. **Screening volumes are asymmetric** — tight in radial (0.4 km), wide in along-track (25-51 km). A single Euclidean distance threshold misses this.

3. **RTN coordinate frame is essential** — CDMs express everything in RTN. Pc is computed in the collision plane (derived from relative velocity). We need TEME -> RTN transformation.

4. **CDM data from Space-Track is our ground truth** — real conjunction events with real Pc values, covariance, screening options. This is both validation data AND ML training data.

5. **Object size matters for Pc** — RCS (Radar Cross Section) from SATCAT is available but it's radar reflectivity, not physical size. AREA_PC in CDMs is what's actually used.

6. **Covariance realism is a known hard problem** — even 19 SDS acknowledges covariance is often underestimated. Our SGP4-derived uncertainty will be even less reliable. ML could add value here.

7. **The screening is a CONTINUOUS REFINEMENT cycle** — not a one-shot scan. Updated observations refine the conjunction prediction over time. CDMs are generated repeatedly as TCA approaches.
