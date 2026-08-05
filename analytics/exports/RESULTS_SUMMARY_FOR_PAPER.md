# Results summary — for drafting main.tex §Results

Written for whoever (Claude Desktop or otherwise) fills in `main.tex`'s
Results/Discussion `\todo` placeholders. Every number here is pulled from the
git-tracked exports in `analytics/exports/*_summary_export/` — cite those
files, not this document, as the source of truth (this is a guided tour, not
the data itself). Each export's own `NOTES.md` has more detail and caveats
than repeated here.

## 1. Impedance/R_int outliers — what was wrong, and the fix

Before trusting any DCIR/R_int comparison across the datasets, it's worth
knowing they had a real, physically-explainable outlier problem, now fixed
by flagging (not silently deleting — every export still has the full data,
just with an extra boolean column marking which rows are trustworthy).

**Root cause, in one line:** DCIR/R_int = ΔV / I, so a pulse with a small
peak current turns ordinary voltage-measurement noise into a wildly inflated
"resistance." This is not a vague statistical outlier — it shows up as a
strong, specific correlation.

- **`old_ul18_12`** (`soc_sweep.csv`): DCIR_dis correlates **r = -0.85** with
  peak pulse current. Splitting at 15 A gives a clean bimodal picture: pulses
  below it average **1208 mΩ** (up to 1802.8 mΩ); pulses at/above it average
  **166 mΩ** (max 571 mΩ) — the latter is the physically credible range.
  `summary_stats.csv` now has both the unfiltered and a `[filtered: |I_peak|
  >= 15A]` row for `DCIR_dis [mΩ]` in both `degradation_trends` and
  `soc_sweep`; the per-row CSVs carry a `DCIR_dis [mΩ]_low_current_flag`
  column. **Use the filtered numbers for any chart/comparison.**
- **`ul18_12_unit2`** (`testday_features.csv`): a different, more severe
  version of the same problem — crank currents are low (~0.3–10 A) across
  the *entire* 20-block campaign, not just a subset (this looks like the
  crank-simulator load is bench/validation-scale, not the ~55–75 A the
  original test-profile spec describes — worth flagging as a limitation of
  the rig, not a data-processing issue). Two things are unambiguous
  regardless: **R_int can never be negative** (a negative reading means
  voltage rose during a "discharge" window — pure noise), and near-zero-current
  readings are the least trustworthy even within this already-low range.
  `crank_cold_R_int_apparent_mohm` and `crank_hot_R_int_apparent_mohm` now
  carry `_invalid` flag columns (negative OR |I_peak| < 1 A); filtering drops
  cold-crank from a 131-row/(-7.3 to 22.8 mΩ) range to a 101-row/(0 to
  4.4 mΩ) range. **Even after filtering, hot-crank R_int still has a wide
  tail (32–808 mΩ)** — the highest values (blocks 11–12, ~733–808 mΩ) have a
  small-but-nonzero current (2.9–3.2 A) and a real, large voltage drop, so
  they survive the filter. This could be a genuine finding (a severely
  degraded cell's voltage collapsing even under a small load) rather than an
  artifact — worth a sentence in Discussion rather than silently trimming
  further.
- **One feature is essentially broken as computed**: `wakeup_load_2_R_int_mohm`
  is negative in **132 of 133 rows** (median -350 mΩ) — don't cite it. Likely
  a reference-voltage bug in the feature-extraction code (the two-stage
  wakeup load's second-stage reference voltage doesn't behave like a rested
  baseline the way the crank event's does) rather than noisy-but-real data;
  flagged here as a known issue, not fixed in this pass.
- **`driving_aux_load_1_R_int_est_mohm`** (16–5550 mΩ) and
  **`ramp_like_load_1_R_int_est_mohm`** (35–1262 mΩ) are slope-based estimates
  (linear fit over a variable-current segment, not a discrete pulse), so
  there's no per-row peak-current column to filter on the same way. Both are
  all-positive (no negative-value issue) but very wide. **Use the median, not
  mean/max, if citing these**: driving_aux median = 319 mΩ, ramp_like median
  = 115 mΩ — both far below their means, confirming a heavy right tail.
- **`boxer` (field data) does *not* need this kind of filter** — but for a
  different reason, itself worth stating in the paper: there is **no per-row
  current column in the field dataset at all**. `R_int_mohm` is derived from
  voltage drop and an *estimated*, not measured, crank current (confirmed:
  `R_int_mohm` correlates **r = 0.95** with `V_drop_pct`, and their ratio is
  nearly constant per pack group, ~0.28–0.29 with low spread) — so field
  R_int is not an independent measurement, it's a near-linear rescaling of
  V_drop. Report it as such rather than as directly comparable to the lab's
  genuinely current-derived R_int.

## 2. Feature parity: what the lab computes vs. what the field computes, and what closing the gap needs

**Lab side** (`ul18_12_unit2`/`old_ul18_12`, `testday_v2_features.py`) computes,
per engine-start-like event: `ocv_window_Vpre`, two-stage `wakeup_load`
sag/R_int, `glow_plug_like_load` reference voltage, **separately labeled cold
and hot crank** (`V_pre`/`V_min`/`I_peak`/`V_sustained`/`R_int_apparent` for
each), `recovery_rest` voltage at +30 s, `alternator_charge` Ah and
charge-return ratio, and two variable-current segments
(`ramp_like_load`/`driving_aux_load`) each giving a slope-fit R_int as a
cross-check. That's possible because the lab rig plays an explicit scripted
event sequence and logs which event is active on every row
(`Event_Type`/`Event_Index` columns) — see `analytics/CLAUDE.md`.

**Field side** (`boxer`) computes, per real engine start: pre-crank voltage,
minimum voltage, %drop, an SoC estimate, temperature, one R_int figure (see
§1's caveat), a recovery *time* (not a voltage-at-fixed-offset, a genuinely
different definition from the lab's), and a multi-battery-pack voltage
imbalance metric (no lab equivalent — lab units are single batteries).
There's no cold/hot distinction, no wakeup-load phase, no alternator-charge
phase, no variable-current segment.

**Update (2026-08-06) — largely closed.** A second, much richer field dataset
has since landed: `analytics/dataset_boxer_can/` + `build_boxer_can_features.py`
+ `boxer_can_dashboard.py` (7 CAN taps, 826 starts, not reviewed in detail by
this analysis pass — added after this document was first written). Its
feature table already has what §2 originally said was missing:
- **Real measured current** (`I_pre_A`, `I_load_A`, `delta_I_A`) — not an
  assumed constant like the old 41-start `boxer` dataset, so its R_int
  columns (it has three: `R_internal_est_ohm_pack`,
  `..._min_voltage_method_pack`, `..._from_ocv_pack`) are genuinely
  independent measurements, not a rescaling of V_drop. **Given §1's finding
  that current-derived R_int is exactly the metric prone to low-current
  blowup, apply the same diagnostic (correlate R_int against `I_load_A`,
  check for a bimodal split) to this dataset before citing its R_int range —
  don't assume it's clean just because current is now measured.**
- **Explicit event timing from CAN commands** (`t_starting_cmd_s`,
  `t_glow_plug_s`, `t_running_cmd_s`, `crank_duration_s`, `glow_lead_s`) —
  not the lab's row-level `Event_Type`/`Event_Index`, but genuine
  event-boundary timestamps, which is the hard part of what item 1 below
  used to ask for.
- **`post_start_V_mean/std`** — the commit message for this dataset
  explicitly calls this out as "the field analogue of the lab's
  `driving_aux_load_1_V_std`, the strongest SoC-robust lab feature" (§3
  above) — i.e. the single most important feature for a lab-to-field
  comparison is now computable on both sides.
- **`t_since_previous_start_s`** — enables a cold/hot crank distinction
  (time since the engine was last running), the one thing the old field
  dataset couldn't do at all.

What (probably) still doesn't carry over: the lab's two-stage `wakeup_load`
phase and the `alternator_charge` return-ratio — check whether any of
`t_before_s`/`t_after_s`/`n_*_samples` in the new feature table map onto
those, but nothing in the column list above obviously does.

**Original gap analysis (kept for context on what was true before this
dataset arrived):**
1. **Event segmentation** — now largely provided by the CAN-command
   timestamps above, rather than needing to be inferred from RPM/ignition as
   originally guessed.
2. **A measured current signal** — now present (`I_pre_A`/`I_load_A`).
3. **A rawer data source** — this *is* that rawer source; the old 41-start
   `boxer` dataset was the pre-extracted one being described as insufficient.

## 3. Headline numbers, ready to cite

**Lab — `old_ul18_12`** (heavily degraded before campaign start):
measured C/5 capacity **5.60–14.08 Ah** across 13 discharges (12.82 Ah on
2025-12-16 → 5.60 Ah on 2026-06-23); `soh_pct` (relative to first reading)
43.7–109.9% (the >100% point is a real anomaly, not an error — see
`old_ul18_12_summary_export/NOTES.md`).

**Lab — `ul18_12_unit2`** (fresh battery, full history captured): measured
C/5 capacity **18.16 → 0.94 Ah monotonically across all 20 blocks**
(2026-07-03 to 2026-08-02) — the cleanest degradation-trend result available
in this project. The strongest within-block features for predicting the
*next* block's capacity (net of SoC-dependence, i.e. not just a SoC proxy —
`soc_robust_soh_indicators.csv`): `driving_aux_load_1_V_std` (r=-0.905, n=18),
`wakeup_load_1_sag_V` (r=-0.664), `alternator_charge_after_cold_Ah`
(r=0.895). These three are the paper's strongest candidates for "a feature
measurable at any SoC that predicts SOH ahead of time."

**Field — `boxer`** (EnerSys ArmaSafe Plus 12FV120, 24V 2S2P, real vehicle
engine starts): 41 starts. Pack-level voltage drop during crank 4.8–36.5%
(mean 11.1%); R_int (with the assumed-current caveat above) 6.5–44.5 mΩ pack
/ 1.4–11.5 mΩ per group; SoC at start 40–100%; temperature 2.1–21.2°C.
Also worth a limitations sentence: `PG3_est`'s R_int is byte-for-byte
identical to `PG4`'s across all 41 starts, while `PG1_est` is *not* identical
to `PG2` — an asymmetry in how the two ungauged (estimated) pack positions
are derived, suggesting one estimation channel may not be independent of its
measured counterpart.

## 4. File map

| Claim | File |
|---|---|
| old_ul18_12 SOH trend | `old_ul18_12_summary_export/soh_history.csv`, `.png` |
| old_ul18_12 DCIR (filtered) | `old_ul18_12_summary_export/summary_stats.csv` (rows with `[filtered: ...]` in `source`) |
| ul18_12_unit2 SOH trend | `ul18_12_unit2_summary_export/soh_history.csv`, `.png` |
| ul18_12_unit2 degradation-cycle detail | `ul18_12_unit2_summary_export/degradation_cycles.csv`, `.png` |
| ul18_12_unit2 SOH-predictive features | `ul18_12_unit2_summary_export/soc_robust_soh_indicators.csv`, `soh_predictors_this_next_block.csv` |
| ul18_12_unit2 full correlation structure | `ul18_12_unit2_summary_export/correlation_matrix_clean_runs.csv`, `.png` |
| boxer field results | `boxer_summary_export/summary_stats.csv`, `features_enriched.csv`, `features_packs_enriched.csv`, 4× `.png` |
