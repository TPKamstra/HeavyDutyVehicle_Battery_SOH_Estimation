# Boxer CAN summary export — notes

Companion to `RESULTS_SUMMARY_FOR_PAPER.md` §1, written 2026-08-20 for the same
purpose but covering the newer, much larger field dataset
(`dataset_boxer_can/boxer_can_features_enriched.csv`, 15 CAN taps, 4799 starts) —
**not** the old 41-start `boxer` export that §1 already covers. That section's
"boxer (field data) does not need this kind of filter" line was written when the
only field dataset had no per-row current column at all; it doesn't apply here.

## 1. R_int low-current/sign-flip diagnostic — same shape as the lab finding, worse in one new way

Correlating `I_load_A` against all three R_int columns (Spearman, since the
relationship is monotonic but not linear):

| Column | Spearman r vs. I_load_A |
|---|---|
| `R_internal_est_ohm_pack` | -0.543 |
| `R_internal_est_ohm_min_voltage_method_pack` | -0.587 |
| `R_internal_est_ohm_from_ocv_pack` | -0.521 |

Same physical mechanism as §1's lab finding: R_int = ΔV / I, so a small I turns
ordinary voltage-measurement noise into a wildly inflated "resistance." **200 A**
is where all three columns cross from a bounded, physically plausible range into
implausible territory:

| Column | Filtered (I≥200A) median / max (mΩ) | Unfiltered max (mΩ) |
|---|---|---|
| `R_internal_est_ohm_pack` | 8.8 / 58.5 | 5800.0 |
| `R_internal_est_ohm_min_voltage_method_pack` | 12.1 / 100.4 | 8200.0 |
| `R_internal_est_ohm_from_ocv_pack` | 9.8 / 65.0 | 6100.0 |

All three columns divide by the same `I_load_A`, so **one shared flag** applies to
all three (checked before assuming this — the three columns' own NaN patterns
differ slightly for unrelated reasons, e.g. `from_ocv` fails more often when no
usable OCV window exists, but whether `I_load_A` itself is trustworthy for a row
is identical across all three).

**306 of 4799 starts (6.4%) are flagged `invalid_R_int`** (`I_load_A < 200` or
missing) — baked into `boxer_can_features_enriched.csv` by
`build_boxer_can_features.py`'s `add_r_int_validity_flags()`, not just filtered
downstream. Two sub-flags, both fully contained within the 306, cover starts with
a *known* pattern:

- **`R_int_current_sensor_dropout`** (17 starts) — the documented `can_tapper31`
  current-sensing dropout, 2026-06-11 19:38 to 2026-06-12 21:52 (start_index
  388-404). See `dashboard_notes/outlier_known_incidents.md`.
- **`R_int_negative_current`** (50 starts, of which 14 overlap the dropout
  window above and 36 don't) — `I_load_A` is negative (min -499 A) for real
  crank events with a genuine, large voltage drop: a sign-convention artifact,
  not low current. 31 of the 36 non-dropout-window negative-current starts are
  on `can_tapper14` (see below); the rest are single scattered occurrences on
  `can_tapper13`/`15`/`17`/`18`.

### `can_tapper14`: disproportionate rate, root cause unresolved

| Tap | n starts | n invalid_R_int | % |
|---|---|---|---|
| **can_tapper14** | **758** | **136** | **17.9%** |
| can_tapper30 | 56 | 7 | 12.5 |
| can_tapper31 | 440 | 31 | 7.0 |
| can_tapper12 | 376 | 22 | 5.9 |
| can_tapper17 | 347 | 19 | 5.5 |
| can_tapper18 | 407 | 17 | 4.2 |
| can_tapper13 | 571 | 22 | 3.9 |
| can_tapper16 | 419 | 15 | 3.6 |
| can_tapper20 | 400 | 14 | 3.5 |
| can_tapper33 | 120 | 4 | 3.3 |
| can_tapper32 | 76 | 2 | 2.6 |
| can_tapper15 | 695 | 16 | 2.3 |
| can_tapper35 | 72 | 1 | 1.4 |
| can_tapper11 | 41 | 0 | 0.0 |
| can_tapper34 | 21 | 0 | 0.0 |

`can_tapper14` sits at 17.9% against a 0-12.5% range for every other tap —
disproportionate enough to be its own issue, not an instance of the general
pattern. Checked for a clean root cause the tapper31 dropout had: **not found.**
The 136 flagged starts span 2022-09-15 to 2024-10-11 (40 different dates, ~2.5
years) — not a single contiguous outage — though 2023-04-18 to 2023-04-26 and
2023-07-17 to 2023-07-19 are notably denser than the rest. The negative-current
sub-pattern on this tap shows the same shape (8 months, one single-day spike of
14 on 2023-07-19). Flagged as unresolved in
`dashboard_notes/outlier_known_incidents.md` rather than guessed at further.

## 2. Other field-side stats, recomputed on the full 4799 starts

For reference against the old 41-start dataset's cited numbers (main.tex
§Results, pre-this-pass): mean voltage drop 10.05% (range -0.20 to 70.10%), mean
SoC at start 78.63% (range 40-100%), mean ambient temperature 15.69°C (range
-5.65 to 42.67°C), mean recovery time 0.98s (range 0.02-8.26s). None of these
have had the same per-feature validity diagnostic run on them yet (out of scope
for this pass, which covered R_int only per the confirmed brief) — **do not
assume they're equally clean**; the voltage-drop range in particular (a slightly
negative minimum, a 70% maximum) looks like it could use the same treatment R_int
just got, but that's future work, not done here.

**Correlation caveat**: the old 41-start dataset's R_int-vs-imbalance (r=0.80)
and R_int-vs-elapsed-time (r=0.26) correlations were computed on a *single pack*
observed over several months, making "correlates with elapsed time" a meaningful
aging-trend check. Recomputed the same way on the (filtered, n=4478) CAN
dataset: R_int vs. `max_abs_imbalance_pct_pack` r=0.289, R_int vs. elapsed time
r=-0.032. **These are not directly comparable to the old numbers** — this
dataset pools 15 different taps/vehicles across ~6 years, so "correlates with
elapsed time" here conflates real aging with which vehicle a start happened to
be on, rather than tracking one pack's degradation. Report with that caveat if
cited, or compute per-tapper if a real aging-trend claim is wanted.

## Source of truth

`summary_stats.csv` (raw stat rows, `source`/`metric`/`min`/`mean`/`max`/`n`,
same schema as the other `*_summary_export/` folders) and
`invalid_R_int_by_tapper.csv` (the per-tap breakdown table above) — cite those
files, not this document.
