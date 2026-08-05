# old_ul18_12 summary export — notes

## What was wrong with this battery / dataset

**The battery itself is heavily degraded — expected, not a data bug.** `old_ul18_12`
is a retired UL18-12 (12V/18Ah nameplate). Its measured C/5 discharge capacity
(`soh_history.csv`, `capacity_ah`) ranges **5.60–14.08 Ah** across the 13 valid
discharge files — nowhere near the 18 Ah nameplate even at its best reading.
`soh_pct` (relative to the *first* valid discharge, not nameplate) ranges
43.7–109.9%; the >100% reading means a *later* discharge measured more capacity
than the first one did, which is unusual for a monotonically-aging cell — could be
temperature-driven (lead-acid capacity is temperature sensitive; see the `temp_c`
column alongside it) or measurement noise around a baseline that wasn't itself
fully representative. Not investigated further here — flagged for whoever
consumes this next.

**Expect early cutoff-voltage hits in the SoC sweep at low SoC.** `soc_sweep.csv`'s
`DCIR_dis [mΩ]` ranges up to **1802.8 mΩ** (vs. a healthy cell's low tens of mΩ) —
this is the known, expected signature of testing a heavily degraded cell down to
low SoC, not an error. See project memory `project_phd_picode` for the original
finding (~8 Ah measured on a full C/5 discharge against the 18 Ah nameplate).

**The v2 event-scripted test-day profile only partially rolled out to this
battery.** `testday_v2_beta_partial.csv` has only **8 rows**, all from
2026-07-02/03, and **no `Battery_ID` column existed yet** when these were logged
(unlike `ul18_12_unit2`, which had it from day one) — every row here resolves to
`battery_id="unknown"` in the source data, not `old_ul18_12`, purely because the
column didn't exist yet, not because of ambiguity about which battery it was.
`cutoff_hit` is 0 for all 8 rows in this small sample — the early-cutoff behavior
noted above is a SoC-sweep (old-profile) finding, not something confirmed in the
tiny v2 sample.

## Chart images: fixed, now included (see boxer NOTES.md for the saga)

Earlier attempts to render PNGs hung indefinitely — traced (after a lot of dead
ends, see `../boxer_summary_export/NOTES.md`) to using `kaleido==0.2.1`'s old
bundled-Chromium binary paired with an older `plotly`. The `ddsm` conda
environment (`plotly` 6.7.0 + `kaleido` 1.3.0, its newer non-bundled-Chromium
architecture) works fine — run this script with
`C:\Users\TPKam\miniconda3\envs\ddsm\python.exe`, not the base environment.

`soh_history.png` and `degradation_trend_DCIR_dis.png` use `x_mode="Cycle count"`
rather than `"Date"` — kaleido 1.x's static-image pipeline can't JSON-serialize
raw pandas `Timestamp` x-axis values (interactive Plotly rendering handles them
fine; static export doesn't), so these two use the cycle-count x-axis instead.

## What *is* in this export

- `summary_stats.csv` / `.json` — flat, one row per metric, across all four of the
  dashboard's computed result tables (tagged by `source`).
- `degradation_trends.csv` — one row per `testday_run` session (19 sessions,
  post-2026-01-27 cutoff), pulse-detected DCIR/SoC/temp/etc.
- `soh_history.csv` — one row per valid `discharge_c5` file (13 files), capacity/SOH%.
- `soc_sweep.csv` — one row per SoC-sweep run (47 runs across all blocks).
- `testday_v2_beta_partial.csv` — the 8-row partial v2 rollout, see above.
- `soh_history.png`, `degradation_trend_DCIR_dis.png`, `soc_sweep_DCIR_dis.png` —
  one representative chart per tab (not every feature × every tab — this
  dashboard's tabs are mostly feature-selector-driven rather than one fixed
  figure each, unlike boxer's 4 static tabs).
