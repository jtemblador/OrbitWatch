# Critical Questions for Weeks 6-8 — What We Need to Answer Early

**Context:** We're about to build the conjunction detection pipeline (Week 6), ML risk classifier (Week 7), and final integration (Week 8). These are the "ask the right questions early" questions — the ones that, if ignored, will cause architectural rework later (like the orbit trail ECEF drift issue from Week 5).

---

## The Big Picture Gap

Our Week 6 plan says: "detect close approaches by scanning all pairs with C++ coarse+medium filter, refine with scipy, cross-validate with Orekit."

The industry (19 SDS) says: "compute Probability of Collision (Pc) using covariance matrices projected onto the collision plane, in RTN coordinates, with asymmetric screening volumes per orbit regime."

**These are fundamentally different approaches.** Our plan finds pairs that come close. The industry computes the probability they actually collide. The difference is covariance — quantifying how UNCERTAIN we are about where each satellite actually is.

---

## Question 1: Where Does Covariance Fit In Our Pipeline?

**Why it matters:** Pc is the single number the entire industry uses to make decisions. CDMs report it. Operators maneuver based on it. Space-Track's public conjunction table shows it. Without Pc, our conjunction detection is a distance calculator — useful, but not what employers at SpaceX or Aerospace Corp expect from a conjunction assessment system.

**Our options:**

| Option | Effort | Portfolio Impact |
|--------|--------|----------------|
| **A. Skip Pc entirely** — just report miss distance, relative velocity | Low | Shows coding skills but misses domain understanding |
| **B. Estimate covariance from TLE age** — rough covariance proportional to epoch staleness, compute approximate Pc | Medium | Shows awareness of uncertainty, approximation is defensible |
| **C. Use CDM covariance from Space-Track** — fetch real CDMs, use their covariance to compute Pc for the same events | Medium | Shows we can work with real industry data, validate against official Pc |
| **D. Full covariance propagation** — propagate covariance forward from epoch alongside state vector | High | Impressive but likely out of scope for 3 remaining weeks |

**Recommended: B + C combined.** Estimate rough covariance for our own detections AND fetch real CDMs from Space-Track to validate against / train ML on. This gives us both independent detection and industry-grade data.

**Decision needed:** Which option are we going for? This shapes the entire Week 6/7 architecture.

---

## Question 2: What Exactly Is Our ML Model Predicting?

**Why it matters:** The roadmap says "ML Risk Classifier — LOW / MEDIUM / HIGH / CRITICAL." But classified by what? If it's just thresholds on miss distance, that's an if-else statement, not ML.

**The hard question:** What does our ML model know that a simple threshold on Pc doesn't?

**Where ML actually adds value:**
1. **When covariance is missing or unreliable** — many objects have "DEFAULT" covariance in CDMs, meaning it's assumed, not calculated. ML could learn to predict risk from features that correlate with true risk even when covariance is bad.
2. **CDM evolution patterns** — a conjunction that's been getting closer over successive CDM updates is more concerning than one that's been stable. Time-series features from CDM history.
3. **Object-type risk profiles** — a maneuverable payload has different risk than a dead rocket body or debris. ML can learn these priors.
4. **TLE quality indicators** — epoch age, number of observations, residual RMS. These tell you how much to trust the miss distance prediction.

**Decision needed:** What features will we train on? This determines what data we need to fetch (CDMs from Space-Track) and when (Week 6, not Week 7).

**Proposed features for ML (from CDM data):**
- `MISS_DISTANCE` (meters)
- `RELATIVE_SPEED` (m/s)
- `RELATIVE_POSITION_R`, `RELATIVE_POSITION_T`, `RELATIVE_POSITION_N`
- Both objects' `OBJECT_TYPE` (PAYLOAD, DEBRIS, ROCKET BODY)
- Both objects' `AREA_PC`
- Both objects' `MANEUVERABLE`
- `COVARIANCE_METHOD` (CALCULATED vs DEFAULT — quality indicator)
- Covariance diagonal elements: `CR_R`, `CT_T`, `CN_N`
- `OBS_USED`, `WEIGHTED_RMS`, `RESIDUALS_ACCEPTED`
- Epoch age (computed from `TIME_LASTOB_END` vs TCA)
- **Label:** `COLLISION_PROBABILITY` (real Pc from 19 SDS) — or thresholded into risk categories

---

## Question 3: Should We Use RTN Coordinates?

**Why it matters:** The industry expresses everything in RTN (Radial, Transverse, Normal) — not Cartesian XYZ. Screening volumes are defined in RTN. CDMs report relative position in RTN. Pc is computed by projecting covariance onto the collision plane (derived from relative velocity in RTN-like frame).

**What we currently have:** TEME positions and ECEF positions. No RTN.

**What RTN means:**
- **Radial (R):** Points from Earth center through the satellite (altitude direction)
- **Transverse/In-Track (T):** Along the satellite's velocity direction in the orbital plane
- **Normal/Cross-Track (N):** Perpendicular to the orbital plane

**TEME -> RTN transform:** Given a satellite's TEME position `r` and velocity `v`:
- R_hat = r / |r|
- N_hat = (r x v) / |r x v|
- T_hat = N_hat x R_hat

This is a rotation matrix applied to the relative position vector between two satellites at TCA. Not hard to implement, but needs to be designed in from the start.

**Decision needed:** Do we implement RTN in Week 6, or add it later? If we output CDM-like records, RTN is required. If we just output Cartesian miss distance, it's optional but makes our output less industry-comparable.

**Recommendation:** Implement RTN in Week 6. It's a 3x3 rotation — maybe 20 lines of Python. The payoff is that our conjunction output looks like a real CDM, which impresses employers.

---

## Question 4: Are Our Screening Thresholds Correct?

**Why it matters:** Our Week 6 plan says "threshold_km" as a single number. The industry uses asymmetric volumes that vary by orbit regime.

**19 SDS screening volumes for LEO (our Phase 1 stations):**
- Most stations are LEO 1 (Perigee <= 500 km): **Radial 0.4 km, In-Track 44 km, Cross-Track 51 km**
- Some may be LEO 2/3

**What a single 50 km threshold means:**
- WAY too tight in the along-track direction (should be 44-51 km, so ~ok actually)
- WAY too loose in the radial direction (should be 0.4 km, not 50 km)
- We'll flag things that are 30 km apart radially as "conjunctions" when the industry would ignore them
- We'll also catch real conjunctions, but with massive false positive rates

**Options:**
- **Simple:** Keep single Euclidean threshold for now, note the limitation
- **Better:** Use orbit-regime-aware asymmetric RTN screening volumes
- **Best:** Use 19 SDS-matching volumes with RTN coordinates

**Decision needed:** At minimum, we should know that our thresholds differ from industry. Ideally, we match them.

---

## Question 5: How Do We Validate Our Results?

**Why it matters:** "Verify against known ISS close approaches" is vague. Known by whom? Where?

**Gold standard validation:** Space-Track has a **public CDM table** (`cdm_public` API endpoint) showing real conjunction events with Pc >= 1e-4. We can:

1. Fetch public CDMs for our time window
2. See which satellite pairs 19 SDS flagged
3. Run our detector on the same satellites and time window
4. Compare: did we find the same events? Are our miss distances in the right ballpark?

**This is far more rigorous than Orekit cross-validation alone.** Orekit verifies our math is correct. CDM comparison verifies our results match the operational system that the entire industry relies on.

**Also available:**
- Space-Track `cdm` endpoint (full CDMs for registered operators — we'd need our Space-Track account's CDM access)
- CelesTrak sometimes publishes close approach data

**Decision needed:** Do we set up CDM fetching from Space-Track in Week 6? It would serve triple duty: validation, ML training data (Week 7), and showing employers we know the real data ecosystem.

---

## Question 6: What Training Data Do We Need for ML, and When?

**Why it matters:** If we wait until Week 7 to think about ML data, we lose a week. CDM data from Space-Track is both our validation data (Week 6) and our training data (Week 7).

**Available from Space-Track:**
- `cdm_public`: Public CDMs with Pc >= 1e-4 (emergency-level events). Limited set but fully public.
- `cdm`: Full CDM data (requires SSA sharing agreement or operator account). Contains the complete Pc range.

**Feature engineering considerations:**
- CDMs contain ~80 fields per event (see Annex C). Many are directly useful as ML features.
- CDMs are generated **repeatedly** as TCA approaches — a single conjunction may have 5-20 CDMs over several days. This time-series aspect is valuable (is the situation getting worse or better?).
- We need to decide: are we predicting **per-CDM risk** or **per-conjunction risk**?

**Decision needed:** Fetch CDM data in Week 6, even if we don't train until Week 7. Understanding the data early avoids "oh, this field is always null" surprises later.

---

## Question 7: SGP4 Uncertainty — How Good Are Our Predictions Really?

**Why it matters:** 19 SDS uses SP (Special Perturbations) — numerical integration with high-fidelity force models (gravity harmonics, drag, solar radiation pressure, third-body effects). We use SGP4 — analytical, simplified perturbations.

**Known SGP4 accuracy (from our key_information.md):**
- ~1 km at epoch
- ~5-10 km at 1 day
- ~50-100 km at 7 days

**Implication for conjunction detection:**
- At 1-day prediction: our position uncertainty is ~5-10 km per satellite
- Combined uncertainty for a pair: sqrt(5^2 + 5^2) ~ 7 km
- If two satellites have a predicted miss distance of 10 km, the actual miss could be anywhere from 0 to 20+ km
- This means our "miss distance" is not a hard number — it's a rough estimate with ~10 km uncertainty

**The honest answer we should give employers:** "Our system identifies conjunctions using SGP4 propagation. SGP4 provides sufficient accuracy for screening (~1 km at epoch) but not for operational collision avoidance. For operational use, SP-quality ephemeris with proper covariance is required."

**Decision needed:** Should we quantify SGP4 uncertainty explicitly in our output? (e.g., "miss distance: 8.3 km +/- ~7 km based on TLE epoch age"). This shows domain maturity.

---

## Question 8: What Does Our Pipeline Look Like End-to-End?

Before building, we should draw the complete data flow:

```
CelesTrak OMM data (30-6000 satellites)
        |
        v
[C++ Coarse Filter] — altitude band overlap check
        |  (~435 pairs → ~100 surviving for LEO stations)
        v
[C++ Medium Filter] — propagate surviving pairs every 60s over 24h
        |  in TEME frame, compute Euclidean distance
        |  (~100 pairs × 1440 steps = 144,000 SGP4 calls)
        v
[Python Fine Filter] — scipy.optimize.minimize_scalar on each flagged window
        |  find exact TCA and minimum miss distance
        v
[RTN Transform] — convert Cartesian miss vector to Radial/In-Track/Cross-Track
        |
        v
[Pc Estimation] — approximate probability of collision
        |  (using estimated covariance from TLE age + object sizes)
        v
[ML Risk Classifier] — features: miss_dist, rel_velocity, RTN components,
        |  object types, epoch ages, estimated Pc, covariance quality indicators
        |  → output: LOW / MEDIUM / HIGH / CRITICAL
        v
[API Response] — /api/conjunctions endpoint
        |  CDM-like format: TCA, miss_distance, relative_speed,
        |  RTN components, Pc, risk_level, both satellites' metadata
        v
[Frontend] — alert table, globe visualization, camera fly-to
```

**Decision needed:** Does this pipeline make sense? Are there missing steps? Is the ordering right?

---

## Question 9: How Do We Handle Object Size?

**Why it matters for Pc:** Pc is the probability that two objects come within a combined Hard Body Radius (HBR) of each other. Without object size, you can't compute Pc.

**Available size data:**
- **RCS_SIZE** in SATCAT: categorical — SMALL (< 0.1 m^2), MEDIUM (0.1-1.0 m^2), LARGE (> 1.0 m^2)
- **RCSVALUE** in SATCAT: numeric RCS in m^2 (when available)
- **AREA_PC** in CDMs: the actual area used in 19 SDS's Pc calculation (m^2)

**RCS to physical radius is approximate:**
- RCS is radar reflectivity, not physical cross-section
- For a sphere: RCS = pi * r^2, so radius = sqrt(RCS/pi)
- For non-spherical objects, this is very rough
- The industry often uses a default HBR of ~5 meters when actual size is unknown

**Decision needed:** Do we use RCS-derived size, a default radius, or skip Pc entirely?

---

## Question 10: What Would Make Employers Actually Impressed?

**Beyond "it works":**

1. **CDM-format output** — shows you know the CCSDS standard (CDM 508.0-B-1)
2. **RTN coordinates** — shows you understand orbital mechanics, not just coding
3. **Screening volumes matching 19 SDS** — shows you've read the operational documentation
4. **Pc computation** (even approximate) — the industry's universal metric
5. **CDM data integration** — fetching and using Space-Track CDMs shows real-world data pipeline skills
6. **Honest uncertainty quantification** — "SGP4 accuracy limits our predictions to..." shows engineering maturity
7. **ML that adds value beyond thresholds** — predicting risk when data quality is poor, not just when the answer is obvious

**The differentiator:** Anyone can compute distance between two points. Showing you understand WHY covariance matters, HOW the industry actually does this, and WHERE your approximations are valid vs. where they break down — that's what stands out.

---

## Recommended Decision Timeline

| Decision | When | Why |
|----------|------|-----|
| Pc approach (Option A/B/C/D) | **Now, before Week 6 starts** | Shapes the entire architecture |
| CDM data fetching from Space-Track | **Week 6 (not 7)** | Needed for validation AND ML training |
| RTN coordinate transform | **Week 6, task 3 (fine filter)** | Small code, big portfolio impact |
| ML feature set | **End of Week 6** | Need to know what data to fetch/compute |
| Object size handling | **Week 6, task 3** | Required if computing Pc |
| Screening volume strategy | **Week 6, task 2** | Affects medium filter thresholds |

---
## Summary: The Three Things We'd Regret Not Deciding Early

1. **Covariance strategy** — Without it, we can't compute Pc, and without Pc, we're not speaking the industry's language. Even approximate Pc (Option B) is infinitely better than none.

2. **CDM data integration** — Space-Track CDMs are simultaneously our validation ground truth, our ML training data, and our proof that we understand the real ecosystem. Fetch early.

3. **RTN coordinate frame** — 20 lines of code, but it transforms our output from "XYZ distance between points" to "industry-standard conjunction geometry." Every CDM field the industry uses is in RTN.
