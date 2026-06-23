# OrbitWatch — Acronym & Term Glossary

Quick reference for the orbital-mechanics / SSA terms used across the project.

---

## Data formats & sources
- **TLE** — *Two-Line Element set.* The classic NORAD orbital-data format: two
  69-character lines encoding a satellite's orbit. Legacy (5-digit catalog cap ~2026).
- **OMM** — *Orbit Mean-elements Message.* The modern CCSDS-standard form of the
  same information, as structured JSON/XML. **What OrbitWatch actually fetches** from
  CelesTrak (future-proofs against the TLE catalog-number limit).
- **GP** — *General Perturbations.* CelesTrak's umbrella term for TLE/OMM data; their
  endpoint is `gp.php`.
- **CelesTrak** — public source we fetch GP data from (no auth).
- **Space-Track** — official US SSA portal (login required); source of CDMs.
- **SOCRATES** — *Satellite Orbital Conjunction Reports Assessing Threatening
  Encounters in Space.* CelesTrak's open conjunction service we validate against in
  Phase 8 (SGP4-based, same method as us).

## Propagation & frames
- **SGP4** — *Simplified General Perturbations 4.* The analytic model that turns
  TLE/OMM mean elements into position/velocity over time. Our C++ engine.
- **SDP4** — the deep-space companion to SGP4 (period ≥ 225 min); modern libraries
  merge both under "SGP4."
- **epoch** — the timestamp stamped on a satellite's orbital data: where it was and
  how it was moving at that instant. SGP4 propagates *relative to the epoch*; error
  grows with time-from-epoch (~1 km at epoch → ~5–10 km/day). See **epoch-matching**.
- **tsince** — minutes since a satellite's epoch (SGP4's time input).
- **TEME** — *True Equator, Mean Equinox.* The (inertial-ish) frame SGP4 outputs in.
- **ECEF** — *Earth-Centered, Earth-Fixed.* The rotating frame used for maps/globe.
- **GMST** — *Greenwich Mean Sidereal Time.* Earth's rotation angle; one Z-rotation
  converts TEME → ECEF.
- **SPICE** — NASA/NAIF toolkit; we use it only for ECEF → geodetic (lat/lon/alt).

## Conjunction screening
- **conjunction** — a close approach between two objects (not necessarily a collision).
- **TCA** — *Time of Closest Approach.* The instant the two objects are nearest.
- **miss distance** — their separation at TCA.
- **RTN** — *Radial / Transverse (in-track) / Normal (cross-track).* The local orbital
  frame conjunction reports use; screening volumes are tight radially, loose along-track.
  (Also written **RSW** in Vallado.)
- **screening volume** — the per-orbit-regime box (in RTN) within which a pair is
  flagged. Asymmetric (e.g. LEO-1: R 0.4 km, T 44 km, N 51 km). Phase 7.2.
- **epoch-matching** — using GP data from the *same epoch* a reference (SOCRATES) used,
  so a comparison reflects method differences, not input drift. Phase 8 prerequisite.

## Risk metrics (deliberately out of scope for OrbitWatch)
- **Pc** — *Probability of Collision.* The industry's headline metric; needs covariance
  we don't have access to → de-scoped. We report geometry (TCA, miss, RTN), not Pc.
- **CDM** — *Conjunction Data Message.* The CCSDS record operators receive; carries Pc,
  covariance, RTN positions. `cdm_public` on Space-Track is an optional Phase 8 cross-check.
- **HBR** — *Hard Body Radius.* Combined object size used in Pc (not used here).

## Catalog / identity
- **NORAD ID** — catalog number identifying an object (also "satnum").
- **International designator** — launch-based id (`YYYY-NNNP`); shared by co-deployed objects.
- **RSO** — *Resident Space Object.* Any tracked object (payload / rocket body / debris).
- **19 SDS / 18 SDS** — US Space Force squadrons that run conjunction assessment (19) and
  maintain the catalog (18); authors of the SFS Handbook our screening volumes come from.
