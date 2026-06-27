# SGP4 & Public-Element Uncertainty — what OrbitWatch can and cannot claim

OrbitWatch screens conjunctions by propagating **public SGP4 element sets** (CelesTrak
/ Space-Track GP data) and finding the time of closest approach. This document states,
plainly and with sources, *how accurate that is* — so the project claims exactly what it
can back: an honest **geometric screener**, not an operational collision-avoidance system.

The short version: the uncertainty is **kilometer-scale and dominated by element age**,
not by our code. We measured this directly in the [validation report](socrates_report.md).

---

## 1. The error budget — three distinct sources

| source | magnitude | who owns it |
|--------|-----------|-------------|
| **Our SGP4 implementation** | < 1 m vs. the reference | us — verified negligible (§2) |
| **SGP4 model + element accuracy *at epoch*** | ~1 km | the data / the model (§3) |
| **Growth with propagation age** | ~2–3 km **per day** | the data (§3) |
| **Epoch drift** (screening from a *stale* public element) | **1–3 km median, measured** | the free feed (§4) — the dominant term in practice |

These are routinely conflated. Separating them is the point: our code is not the
error; the element data is, and most of *that* is how old the element is.

---

## 2. Our SGP4 implementation is faithful — not the source of the kilometers

Our C++ SGP4 engine is cross-validated against the canonical reference implementation
(Brandon Rhodes' `python-sgp4`, which ports the Vallado et al. code) across the **33
official Vallado test cases plus real satellites**, agreeing to **< 1 m** in ECEF
position (`tests/test_sgp4_cpp.py`, `test_propagator.py`). The propagation math is
textbook and reproducible; the kilometers below come from the *elements*, not the code.

> Reference: Vallado, Crawford, Hujsak & Kelso, **"Revisiting Spacetrack Report #3"**,
> AIAA 2006-6753 — the modern SGP4 specification + reference code our engine matches.
> (Note: that paper establishes *implementation* fidelity, which is what we verify here;
> it is not a statement about how accurate the element *sets* are — that is §3.)

---

## 3. Public element sets are kilometer-accurate at epoch and degrade with age

A public TLE/GP element set carries **no published covariance** and is, by the widely
cited consensus, accurate to roughly **1 km at its epoch, growing ~2–3 km per day** of
propagation (error is largest **in-track**, and is sensitive to atmospheric drag for LEO
objects). It is a best-fit to past observations, not a guaranteed future ephemeris.

> Reference: Skyfield documentation (B. Rhodes, *Earth Satellites*) and CelesTrak
> (Dr. T.S. Kelso) both state the ~1 km-at-epoch, few-km-per-day figure. Public TLEs are
> a *mean* element representation for SGP4, not a covariance-bearing operational product.

---

## 4. We measured the dominant error — epoch drift (Phase 8.3)

The largest practical error is screening with an element whose epoch has already **rolled
past** the moment SOCRATES used. We quantified it directly. Running our screener on the
**current free feed** vs. CelesTrak SOCRATES, then on the **epoch-matched historical
element** (the elset SOCRATES actually used), across three slices:

| slice | current feed | epoch-matched | current median \|Δmiss\| |
|-------|-------------|---------------|--------------------------|
| ISS | 3/9 reproduced (33%) | **8/9 (89%)** | **1.13 km** |
| Top-25 closest | 8/25 (32%) | **25/25 (100%)** | **2.58 km** |
| Starlink-40 | 8/40 (20%) | **40/40 (100%)** | **2.51 km** |

Two facts fall out:

1. **The kilometers are epoch drift, not method error.** On the *same* element SOCRATES
   used, every reproduced conjunction agrees to **ΔTCA = 0.000 s, Δmiss = 0.000 km**
   (byte-level, same-method). The 1–3 km on the current feed is purely the element being
   days stale — exactly the §3 magnitude, measured on real data.
2. **Reproduction degrades with element age (`DSE`).** It is high for fresh elements and
   falls for stale ones (e.g. Starlink: 36% at 1–3 days → 14% at >3 days), visualized in
   the report's by-`DSE` figures. Age is the knob.

This is the honest headline: low current-feed reproduction is **a measurement of public-
element staleness**, not a flaw in the screener — and it is fully recoverable with the
right-epoch element.

---

## 5. Therefore: screening, not collision avoidance

OrbitWatch emits **geometry** — time of closest approach, miss distance, relative speed,
RTN components. It deliberately does **not** emit a probability of collision (`Pc`),
because a real `Pc` needs **position covariance** that public element sets do not carry.
A geometric "these two pass within X km" is a *screening* signal — useful for deciding
which pairs warrant a closer look — not an operational go/no-go for a maneuver.

This boundary is the standard one:

- **NASA** — *Spacecraft Conjunction Assessment and Collision Avoidance Best Practices
  Handbook* (NASA/SP-20205011318, Krage, 2020): operational collision avoidance relies on
  owner/operator and 18th Space Defense Squadron tracking and covariance, not public
  elements.
- **MathWorks** — the Aerospace Toolbox *Satellite Conjunction Finder* uses the **same
  method we do** (TLE/SGP4 propagation + root-finding on the inter-satellite distance
  derivative for TCA — our range-rate Newton solve) and carries the explicit disclaimer:
  *"Do not use publicly available TLEs for operational conjunction assessment prediction…
  Satellite operators should contact the 18th Space Defense Squadron."* Independent
  confirmation that our pipeline is textbook **and** that its honest use is screening.

**OrbitWatch's claim, stated exactly:** an honest geometric conjunction *screener* on
public SGP4 elements, whose method reproduces an established service (SOCRATES) to
machine precision on matched elements, and whose residual error is the well-understood,
measured uncertainty of public element data. It is a portfolio demonstration of the
screening pipeline — **not** an operational collision-avoidance tool.

---

## References

- Vallado, D., Crawford, P., Hujsak, R., Kelso, T.S. — *Revisiting Spacetrack Report #3*,
  AIAA 2006-6753. https://celestrak.org/publications/AIAA/2006-6753/
- Krage, F.J. — *Spacecraft Conjunction Assessment and Collision Avoidance Best Practices
  Handbook*, NASA/SP-20205011318, 2020. https://ntrs.nasa.gov/citations/20205011318
- MathWorks Aerospace Toolbox — *Satellite Conjunction Finder*.
  https://www.mathworks.com/help/aerotbx/ug/satellite-conjunction-finder.html
- Rhodes, B. — Skyfield documentation, *Earth Satellites* (TLE accuracy).
  https://rhodesmill.org/skyfield/earth-satellites.html
- CelesTrak (Kelso, T.S.) — GP/TLE data and format/accuracy notes. https://celestrak.org/
- OrbitWatch — measured epoch-drift data: [`socrates_report.md`](socrates_report.md),
  `progress/task_logs/task_8_3_validation_report.md`.
