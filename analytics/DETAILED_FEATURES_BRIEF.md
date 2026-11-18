# Brief: "Detailed Features" tab + dashboard clean-up (ul18_12_unit3)

> **Status 2026-11-18: implemented** (A1–A3, A5, B1–B3, B5; the graph audit). Not done:
> A6 sample entropy and B4 full IC/DV, see `FEATURE_OVERVIEW.md` §1b for why and for the results.

Written 2026-11-18 for a Claude Code session. Read `analytics/CLAUDE.md`
(the `ul18_12_unit3` section) and `FEATURE_OVERVIEW.md` first. The campaign is
**complete** (11 blocks, 100 → 46.6 % SOH, bench stopped at the 50 % criterion),
so the dataset is final: no more files will land.

## Goal

The current test-day features are simple: levels, minima, and one ΔV/ΔI
resistance per event. The literature uses more of the curve shape. Build a
**separate tab, "Detailed Features"**, that extracts and evaluates
curve-based features from the voltage and current signals. At the same time,
make the dashboard paper-oriented: **every graph must answer one question
that a sentence, table or figure in the PHMIC 2027 paper can use.** Graphs
that only exist for debugging move to one Diagnostics tab or are removed.

## Ground rules (from the paper so far: do not re-learn these)

1. **Compare at equal charge removed, not equal relative SoC.** SoC levels are
   relative to each block's capacity, so voltage-level features correlate
   with SOH as an artefact. Each new feature is shown against `removed_ah`
   (and, for the field view, against rest OCV), never only against SoC %.
2. **Per-feature, per-run validity.** Each new feature gets rules in
   `FEATURE_CATALOG` + `feature_validity()` (stored as
   `<feature>__invalid_reason`). Known causes: the 10.2 V sink floor at 10 %
   SoC, the −60 A cap, the 0.1 s current rise of the hot crank, Block 1
   (frozen sink current, excluded).
3. **Evaluation = the paper's method.** Score each feature as in
   `tab:features`: R²_OCV (variance explained by rest OCV), ρ (rank correlation
   with SOH after removing the OCV dependence), and the leave-one-block-out
   (LOBO) RMSE of `SOH ~ OCV + OCV² + feature`, block-median prediction,
   blocks 2–11. Baselines to beat: mean SOH **18.8**, OCV only **17.1**, best
   current feature (cold-crank R_int deltaI) **10.1** SOH %-points. The LOBO
   code currently lives only in a scratchpad script. Port it into a
   tested module (`analytics/soh_eval.py`, no Panel/Plotly, like
   `can_stats.py`) so the paper table can be reproduced from the repo. The
   numbers for the existing features must come out the same.
4. **Resolution limits.** Voltage ADC step ≈ 8 mV. Test days are 20 Hz; C/5,
   step-down and degradation files 1 Hz; post-step-down rest files 0.1 Hz.
   Any derivative feature (dV/dt, dQ/dV) needs fitting or smoothing, and the
   parameters must be documented. Reject a feature whose campaign shift is
   below a few ADC steps (`feature_stats` already reports this).
5. **Label each feature's availability:**
   - `field`: computable from vehicle-observable events (test-day loads, rest
     OCV). These are candidate health indicators.
   - `lab`: needs a C/5, a full CC/CV charge or a controlled rest. These are
     mechanism or label-side evidence, for example for the sulphation
     hypothesis in the Discussion.

   Show the label in the catalogue and in the tab.

## Data sources per block (all in `LOGBATTEST/ul18_12_unit3/`)

| File | Rate | Use |
|---|---|---|
| `Block_nn_SoCxxx_*_testday_bdps_*` | 20 Hz, ~2050 s, event-labelled | field features (below) |
| `Block_nn_SoCxxx_*_stepdown_bdps_*` | 1 Hz, CC −3.6 A | short discharge segments at known Ah removed |
| `Block_nn_SoCxxx_*_rest_bdps_*` | 0.1 Hz, 1800 s | OCV relaxation after each step-down (~240 mV at 50 % in Block 6) |
| `Block_nn_SOH_C5_bdps_*` | 1 Hz, −3.6 A, full discharge after 1 h rest from full | coup de fouet, IC/DV curves, end-of-discharge knee |
| `Block_nn_Degr_kk_charge_bdps_*` | 1 Hz, CC/CV | charge-curve features (5 per block) |
| `_sweep_points.csv` | — | `removed_ah_before_run`, SoC, OCV per point |

## Candidate features (start with A1–A3 and B1–B2; add the rest if time allows)

### A. Field-observable (test-day events, 20 Hz)

- **A1. Ohmic vs polarisation split per load step.** For crank, glow, wakeup,
  ramp and driving_aux steps:
  - R0 = ΔV over the first sample(s) after the current step / ΔI;
  - R_pol = (V at end of event − V just after the step) / ΔI;
  - ratio R_pol/R0.

  Sulphation and electrolyte effects should move the parts differently.
  [Khodadadi Sadabadi 2021, Zotero MQXNC9JB: step response of ~7 s; Kerley 2015;
  Grube 2008.]
- **A2. Equivalent-circuit fit per step.** Fit a 1-RC (and 2-RC if it
  improves the fit) model to the voltage response of the longer steps
  (glow ~45 s, wakeup, ramp 20 s). Features: R0, R1, τ1, fit RMSE. Use a fit
  RMSE threshold as a validity rule.
- **A3. Relaxation after the cranks** (`recovery_rest`, long at 20 Hz). Fit
  V(t) = V∞ − a1·e^(−t/τ1) − a2·e^(−t/τ2), or the slope against log(t).
  Features: τ1, τ2, a1, a2, V∞ − V_pre (net polarisation left by the event).
  Compare with the existing `V_plus30s`. [Relaxation-based SOH, Zhu 2022,
  Zotero NIPTQKEJ, is Li-ion; lead-acid relaxation for SoC is in the
  ResearchGate paper "Comprehensive study of relaxation behaviour of
  lead-acid batteries…".]
- **A4. Crank waveform shape.**
  - V at fixed times after onset (50/100/200/500 ms);
  - initial dV/dt;
  - time to V_min;
  - second-minimum vs first-minimum (Grube/Kerley), only if the replayed
    profile has two phases. Check this first.
- **A5. Charge acceptance during `alternator_charge`.**
  - charge-side R0 (voltage jump at the charge step / ΔI);
  - dV/dt in the first minutes;
  - V at the end;
  - ratio of charge R to discharge R of the preceding crank.

  Charge acceptance is the classic sulphation symptom.
- **A6. Regularity of V during `driving_aux_load`:** sample entropy
  [Sample entropy, Energy Conv. Manag. 2009]. Check first against the ADC
  step and the profile's own noise. Drop it if it only measures
  quantisation.

### B. Lab-only (mechanism / label-side)

- **B1. Coup de fouet at the start of each C/5** (always from full after 1 h
  rest, so comparable across blocks): trough voltage, plateau voltage, depth,
  and **time from trough to plateau**. That time is reported as the feature
  most linked to sulphation. [Coup-de-fouet SOH: IEEE 884309; "Study of the
  coup de fouet of lead-acid cells as a function of SOC and SOH"; J. Power
  Technol. 2021.] Also check the 100 % SoC test days and the first
  step-down.
- **B2. Charge-curve features from `Degr_kk_charge`:**
  - CC duration;
  - voltage at equal-time points in the CC phase (cf. "five equal-time
    voltage differences", Electronics 12(21) 4552, 2023);
  - CV current-taper time constant (exponential fit);
  - CV/CC time ratio;
  - charge factor (Ah in / Ah previously out).

  Five per block, so the within-block spread is known.
- **B3. Relaxation in the rest files** (0.1 Hz, 30 min after each step-down):
  amplitude and τ against `removed_ah` and SOH. This is the cleanest
  equal-charge comparison the data offer.
- **B4. IC/DV analysis of the C/5 discharge:**
  - dQ/dV and dV/dQ against Ah, with smoothing per Dubarry & Anseán 2022
    (Zotero DHIQFSEJ);
  - peak positions and heights;
  - position of the end-of-discharge knee ("steep drop" segment, cf. the GPR
    capacity-trajectory paper).

  IC on lead-acid is recent (positive-active-mass decay, J. Energy Storage,
  S2352152X23034473).
- **B5. Coulombic efficiency / charge factor per degradation cycle.**
  These already exist on the Degradation Cycles tab. Move them here as
  evidence for the undercharge/sulphation hypothesis.

Every new feature needs: a catalogue entry (definition, window, unit,
`field`/`lab`, literature key), validity rules, a test in `analytics/tests/`
(synthetic signal with a known answer + one real run), and a cache-tag bump.

## The "Detailed Features" tab

For a selected feature:

1. **Definition card:** catalogue text, availability label, literature key.
2. **Raw-signal panel:** one event or curve for all blocks at one equal
   charge removed, with the feature's window shaded and, for fits, the fitted
   curve overlaid.

   *Question it answers:* is the feature measured where we think, and is the
   fit adequate? *Paper use:* method figure.
3. **Feature vs Ah removed, one line per block.**

   *Question it answers:* does the feature separate blocks at equal charge?
   *Paper use:* the Fig. 2c-type panel.
4. **Feature vs SOH at a fixed charge removed** (and, for `field` features,
   after OCV correction).

   *Question it answers:* is it a health indicator? *Paper use:* Results.
5. **Ranking table:** all features (existing + new) with R²_OCV, ρ, LOBO
   RMSE, n valid, availability, sorted on LOBO RMSE, baselines on top. This
   is `tab:features` regenerated. Add a **"Export for paper"** button that
   writes this table as CSV + LaTeX rows and the figures of items 2–4 as
   PDF/PNG at single-column (3.5 in) and double-column (7.16 in) width, to
   `analytics/exports/unit3_paper/`.

Load it lazily like the Feature Explorer (`_lazy_loaded`). Keep the heavy
work in cached functions (`cached_call`), since raw C/5 and charge files are
large.

## Graph audit of the existing tabs

The rule: each graph states its question in its title or subtitle, shows n,
marks invalid values, and maps to a paper element. If it doesn't, move it to
**Diagnostics** or delete it. Proposed outcome (my reading of the dashboard on
2026-11-18; confirm with the user before deleting anything):

| Tab / graph | Verdict | Reason |
|---|---|---|
| Campaign: SOH per block | keep | Fig. 2a |
| Setpoint Check overview | keep | Fig. 3 |
| Sweep Quality | keep | the SoC-accuracy sentence (≤ 0.85 %-point) |
| Feature Explorer | keep, add x = Ah removed as default | basis of Fig. 2b/c |
| Degradation Cycles | keep, frame as charge-return evidence | Discussion (sulphation) |
| Run Inspector, Compare Runs | move to Diagnostics | debugging, no paper element |
| SOH History | merge into Campaign | duplicates SOH per block |
| Degradation Trends (feature vs block/SOH at relative SoC) | remove or replace by item 4 above | relative-SoC view is the artefact the paper warns about |
| Correlation heatmap (all features, all runs, Spearman) | remove | pooled over SoC and blocks; dominated by charge level; not citable |
| Pairwise scatter explorer | Diagnostics; default must not be block vs block | the current default plots block against itself (r = 1) |
| Block-level predictor table (this/next block) | replace by the LOBO ranking table | n = 10 block means; superseded by the paper's method |
| SoC-robust indicator table | merge into the ranking table (`r_charge_matched` as a column) | one table instead of three |
| File Inventory, Export | keep (utility, not graphs) | — |

## Constraints

- Analytics only. Nothing in `lab-control/` and nothing on the Pi.
- Never pool batteries; unit3 only. If shared functions in
  `testday_v2_features.py` change, check the unit2 dashboard still loads.
- Run with `C:\Users\TPKam\miniconda3\envs\ddsm\python.exe`. Run the tests
  with `-m pytest analytics/tests -q`.
- `panel serve` keeps old code: restart port 5011 after changes, and stop it
  when done.
- Commit only after the user approves; don't push.
- Update `analytics/CLAUDE.md` (unit3 section) and `FEATURE_OVERVIEW.md`
  with the new features, their validity rules and results.

## Done when

- `soh_eval.py` reproduces `tab:features` for the existing features (18.8 /
  17.1 / 10.1 …), with a test.
- At least A1–A3 and B1–B2 are implemented, validated and in the ranking
  table. Report honestly which ones beat 10.1, which beat only the OCV
  baseline (17.1), and which add nothing.
- The Detailed Features tab works, and the paper export writes the table and
  figures.
- The graph audit is applied after the user has confirmed the deletions.
  Every remaining graph has a question-style title or subtitle, and a short
  note in `CLAUDE.md` maps it to its paper element.
- A short results summary for the user: the best new features, their LOBO
  RMSE, their availability (field/lab), and a suggested sentence for the
  paper.
