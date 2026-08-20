# Handoff for Claude Desktop — Boxer CAN dashboard session (2026-08-06)

Written from a Claude Code session in this repo, for whoever (human or
Claude Desktop) picks this up next. `CLAUDE.md`'s new "Boxer Battery —
Motor-Start CAN Export Dashboard" section is the living technical
reference (file roles, constants, how to re-run things); this note is the
narrative/results summary of what actually happened this session, in
case some of it overlaps with work you already did.

## What was asked, in order, and what happened

1. **Dashboard width fix** (`boxer_can_dashboard.py`,
   `boxer_battery_dashboard.py`) — plots were rendering at half the
   browser width. Fixed with `sizing_mode="stretch_width"` throughout and
   `main_max_width="100%"` on both `FastListTemplate`s. Verified via live
   `panel serve` + HTTP 200 check (no browser available in this
   environment to eyeball it directly).

2. **New "Lab vs Field Comparison" tab** + `lab_field_feature_comparison.py`
   — matches crank internal resistance, post-crank recovery voltage, and
   post-crank voltage stability between the lab (`ul18_12_unit2`) and field
   (Boxer ArmaSafe) datasets. First version was explicitly rejected by the
   user ("I really don't like the comparison page... not clear at all") —
   redesigned to one figure per feature family, one panel per condition
   (not pooling lab cold-crank and hot-crank into a single box), with
   robust (Tukey k=3 IQR) axis capping instead of raw autoscaling so a few
   extreme points can't compress everything else into an unreadable
   sliver.

   Redesigning it surfaced a real finding, not just a chart-styling issue:
   the lab's logged crank current (`crank_cold/hot_I_peak_A`, median
   3.5-7.1 A) is ~2 orders of magnitude below a realistic engine-crank
   current (field `I_load_A`, median ~430 A) — already flagged in
   `testday_v2_features.py`'s own code comments as "likely a low-current
   bench/validation run". Since `R_int_apparent = ΔV / I_peak`, this makes
   `crank_hot_R_int_apparent_mohm` (mean ~214 mΩ) vs. `crank_cold` (mean
   ~1.7 mΩ) noise on a near-zero denominator, **not** a real physical
   cold/hot difference. Documented as a caveat directly on that panel
   (`dashboard_notes/lab_field_comparison_intro.md`), not swept under.

3. **New "Outlier Inspector" tab**, prompted by the user noticing
   `can_tapper31` and `can_tapper33` "look weird" in the per-tap charts.
   Investigated both by hand before building anything:
   - **`can_tapper31`**: current-sensing dropout, 2026-06-11 19:38 to
     2026-06-12 21:52 (start_index 388-404, 17 starts, ~26h). `I_load_A`
     collapses to single digits instead of the normal ~400 A seen
     immediately before/after this exact window. **All 11 of the whole
     dataset's `R_int_mohm > 50 mΩ` outliers are from this one window on
     this one tap.** Voltage sampling in the same window looks normal, so
     it reads as a current-channel-specific fault, not a general logging
     dropout. Root cause not confirmed from the analysis side.
   - **`can_tapper33`**: a real electrical event, not an artifact —
     2025-12-16 11:13-11:58 (start_index 20-23, 4 starts, ~45 min).
     44.6-54.9% pack voltage drop (vs. ~9% dataset median), cold ambient
     (4.3-6.5°C), but *normal* crank current (380-500 A), all four pack
     groups sagging together — consistent with a cold, weak battery under
     load. One point (`PG1_est`, start 21) sags far more than its own
     siblings in the same start (83% vs. 44-46%) — `PG1_est` has no direct
     sensor (estimated, not measured), so that specific point is more
     suspect than the other three.

   Built a live IQR-fence outlier table (adjustable sensitivity slider) +
   timeline scatter + per-group breakdown table, so these two incidents
   (and any new ones) can be found again without re-deriving by hand each
   time.

4. **Zips-dir discovery.** Needed the raw zip source to regenerate/extend
   the dataset. Extensive filesystem search (Documents, Downloads,
   Desktop, the broader PhD OneDrive tree, including an unrelated
   `CANbusProject` folder with older fuel-usage data that came up as a
   false lead) failed to find it — had to ask the user directly. Answer:
   ```
   C:\Users\TPKam\OneDrive - University of Twente\Accu_dataset_Boxer_anonimised
   ```
   **If you already know this path from a prior session, it hasn't
   moved** — this would have saved a fair amount of searching.

5. **Raw-trace drill-down**: select a flagged row in the Outlier Inspector
   → see its actual -60s..+120s per-group voltage trace, not just the
   summary numbers. First implementation used "every start beyond a Tukey
   k=1.5 IQR fence" (matching the dashboard's loosest slider setting) and
   produced a **1 GB CSV** — `post_start_V_std_1_10V` alone flagged 653 of
   4785 starts (13.6%), because a *global* fence pooled across 15
   different vehicles mostly just detects normal vehicle-to-vehicle
   differences in baseline accessory-load behaviour, not real anomalies
   (the tail is fat by nature, not by fault). Caught before it was
   committed. Fixed to "top `OUTLIER_TRACE_TOP_N` most extreme starts per
   feature" (bounded regardless of tail shape) — started at N=40 (113
   starts, ~59 MB), bumped to N=60 (157 starts, ~82 MB) after the user hit
   a real near-miss (`can_tapper15` start_index 7, a genuine 24.2%
   voltage-drop event, ranked #48 — just outside the N=40 cutoff).

6. **Notes moved out of the dashboard code.** The long static prose blocks
   that were previously inline Python string literals in
   `boxer_can_dashboard.py` (the known-incidents write-up, the lab/field
   comparison framing, the dataset-summary notes) now live as their own
   files in `dashboard_notes/*.md`, loaded at import time — keeps the
   dashboard's `.py` file to code, and the notes editable/reviewable
   without touching Python.

## Current dataset state (as of this session, 2026-08-06)

- 15 CAN taps, 4799 starts total
  (`dataset_boxer_can/boxer_can_features_enriched.csv`) — up from the
  original 7-tap, 826-start export earlier in the session, after the user
  uploaded 8 more taps to the OneDrive zips folder mid-session.
- `outlier_raw_traces.csv` / `outlier_raw_events.csv`: 157 unique starts,
  ~82 MB — git-tracked alongside the other small tables in
  `dataset_boxer_can/`, regenerate with `--extract-outlier-traces` (see
  `CLAUDE.md` for the exact command and the "don't use a fixed IQR fence"
  design note).

## Open / not done

- No raw CAN dataset from the actual target vehicle fleet has been
  delivered yet — distinct from this already-processed Boxer per-start
  dataset. See root `CLAUDE.md`'s "5.3 Lab-vs-field comparison" section for
  what that would need (event-boundary inference from RPM/ignition, a
  current signal for R_int-style features).
- If more taps/zips get uploaded again, re-run
  `build_boxer_can_features.py --zips-dir <path>` (add
  `--extract-outlier-traces` too if outlier-trace coverage should be
  refreshed against the new data).
- `OUTLIER_TRACE_TOP_N` (currently 60 in `build_boxer_can_features.py`) is
  a tunable trade-off between raw-trace coverage and file size — bump it
  if more near-misses like the `can_tapper15`/start 7 case keep coming up,
  but sanity-check the resulting file size first (rough estimate:
  n_unique_starts × 9001 rows × ~45 bytes/row) before treating it as done.

See `CLAUDE.md` → "Boxer Battery — Motor-Start CAN Export Dashboard" for
the full technical write-up.
