# ul18_12_unit2 summary export — notes

## Data-quality history worth knowing before trusting this export

Unlike `old_ul18_12`, this battery and its test ran as intended — the full
20-block plan completed cleanly 2026-08-02 (`block_c5_capacity_ah` in
`soh_history.csv` runs **18.16 → 0.94 Ah** across blocks 1–20, a normal gradual
decline). But three real issues surfaced *during* the test and were fixed in the
analysis code before this export was generated — worth knowing since they'd
otherwise make parts of the data look wrong when it isn't (or vice versa):

1. **Pi-side logging-rate regression, Block 4–5 (2026-07-11 to 2026-07-16,
   resolved).** The Pi's BDPS logging interval degraded from ~1 Hz down to a
   median gap of tens-to-hundreds of seconds, which silently undercounts Ah
   integration. Corrected in code for constant-current segments (SOH C/5,
   Degr discharge) via `duration × mean current`; Degr *charge* cycles and
   event-level test-day features for the affected window remain flagged
   unreliable (`degradation_cycles.csv`'s `*_coarse_sampling` columns,
   `testday_features.csv`'s `cutoff_hit` column).
2. **Block 6 SOH "abort" — false alarm.** A block's SOH C/5 file can be
   mid-write when synced; `soh_history.csv`'s `reached_cutoff` column
   distinguishes a genuinely final reading from a live snapshot. All 20 rows
   in this export have `reached_cutoff=True` (the test is complete), but if
   this export is regenerated mid-test, expect some `False` rows — that's
   normal, not corruption.
3. **Two absolute-threshold bugs, found while verifying 20-block completeness
   (fixed 2026-08-03/04).** A degradation-cycle completeness check compared
   each cycle against a *global* median across all 20 blocks instead of its
   own block's median, wrongly flagging 100% of late-block (heavily degraded,
   ~1 Ah) cycles as incomplete; and the SOH capacity calc rejected any reading
   below a hardcoded 1.0 Ah floor, silently dropping Block 20's real, valid
   0.94 Ah measurement. Both fixed — `degradation_cycles.csv` correctly shows
   180/200 cycles complete, and Block 20 has a real SOH row.

**Bottom line for this specific export:** it was generated *after* all of the
above fixes, against the completed 20-block dataset, so it should not exhibit
any of these issues itself — they're listed here as context for interpreting
the numbers, not as caveats about this export's own correctness.

**Also, not a bug:** `testday_features.csv` run counts per block shrink sharply
from block ~8 onward (down to as few as 2 SoC-sweep points per block by the
end) as the battery's usable OCV range narrows with capacity — confirmed
physically consistent (Block 20 sweeps only 12.37–13.50 V with <1 Ah total
capacity), not missing data.

4. **Crank/wakeup R_int outliers, flagged 2026-08-06.** Crank currents logged
   across the *entire* campaign are low (~0.3–10 A, vs. the ~55–75 A the v2
   profile spec describes) — likely the crank simulator applying a
   bench/validation-scale load rather than a real starter-motor-scale one.
   `crank_cold_R_int_apparent_mohm`/`crank_hot_R_int_apparent_mohm` now carry
   `_invalid` flag columns (negative reading, or |I_peak| < 1 A — both
   physically/numerically indefensible); `summary_stats.csv` has matching
   `[filtered: ...]` rows. Even filtered, hot-crank R_int keeps a wide tail
   (up to 808 mΩ, concentrated in blocks 11–12) that survives because those
   readings have nonzero current and a large real voltage drop — possibly a
   genuine "severely degraded cell collapses even under a small load" finding
   rather than noise; treat as a discussion point, not a solved problem.
   Separately, **`wakeup_load_2_R_int_mohm` is negative in 132 of 133 rows** —
   don't cite it, it's not usable as currently computed (probably a
   reference-voltage bug in `testday_v2_features.py`, not fixed here).
   `driving_aux_load_1_R_int_est_mohm`/`ramp_like_load_1_R_int_est_mohm` have
   no per-row current to filter on the same way (slope-fit estimates, not
   discrete pulses) — use their median, not mean/max, if citing. Full writeup:
   `../RESULTS_SUMMARY_FOR_PAPER.md` §1.

## Chart images: fixed, now included (see boxer NOTES.md for the saga)

Earlier attempts to render PNGs hung indefinitely — traced (after a lot of dead
ends, see `../boxer_summary_export/NOTES.md`) to using `kaleido==0.2.1`'s old
bundled-Chromium binary paired with an older `plotly`. The `ddsm` conda
environment (`plotly` 6.7.0 + `kaleido` 1.3.0) works fine — run this script with
`C:\Users\TPKam\miniconda3\envs\ddsm\python.exe`, not the base environment.

## What *is* in this export

- `summary_stats.csv` / `.json` — flat, one row per metric, across
  `testday_features`, `degradation_cycles`, and `soh_history`.
- `testday_features.csv` — 140 SoC-sweep test-day runs, full event-derived
  feature set (crank R_int, wakeup sag, alternator charge ratio, etc.), plus
  `*_invalid` flag columns on the R_int-type features (see note 4 above).
- `degradation_cycles.csv` — 200 rows (20 blocks × 10 cycles), Ah + coulombic
  efficiency + completeness flags.
- `soh_history.csv` — 20 rows, one per block, measured C/5 capacity.
- `soh_predictors_this_next_block.csv` — Spearman r of each feature against
  this/next block's measured capacity (the "does a within-block feature predict
  SOH" analysis).
- `soc_robust_soh_indicators.csv` — same, ranked by predictive value *net of*
  SoC-dependence (r_soc), i.e. features that track degradation without just
  being a proxy for state of charge.
- `correlation_matrix_clean_runs.csv` — full pairwise Spearman correlation
  matrix across all features, coarse-sampled/cutoff runs excluded (the
  dashboard's default heatmap view).
- `soh_history.png`, `degradation_cycles.png`, `correlation_heatmap_clean_runs.png`
  — one representative chart per tab.
