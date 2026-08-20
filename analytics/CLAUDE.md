# BatPi Download — Battery Test Dashboard

Interactive dashboard for analysing pulse-test and full-discharge data from a 4-cell
LiFePO4 battery pack (nominal 12.8 V, ~18 Ah).

Run the dashboard:
```
panel serve battery_feature_dashboard.py --show
```

**Two batteries, two separate dashboards — never pooled:**

| Battery | Dashboard | Log folder |
|---|---|---|
| `old_ul18_12` (heavily degraded, ~8 Ah of 18 Ah nameplate, retired) | `battery_feature_dashboard.py` | `LOGBATTEST_Complete/` |
| `ul18_12_unit2` (fresh, started 2026-07-03, testing in progress) | `battery_feature_dashboard_unit2.py` | `ul18_12_unit2/` |

```
panel serve battery_feature_dashboard_unit2.py --show
```

`ul18_12_unit2/` uses the v2 event-scripted test-day profile from day one
(`Block_<nn>_...` filenames, `Battery_ID` column present) — see
"Test-Day Profile Format (v2)" below. Its dashboard has a **Rescan** button
since testing is ongoing (as of 2026-07-05: Block 1 only, 9/10 degradation
cycles logged) — click it to pick up new blocks/cycles without restarting.

## Running all four dashboards (+ the unified one) — ports

There are four independently-runnable dashboards plus one composed app that
puts all of them on a single URL. Every port below is a fixed,
non-colliding assignment (verified 2026-08-20 by grepping every
`pn.serve(...)`/`port=` call in `analytics/*.py`) — **don't reuse one of
these ports for anything else in this folder**, and if you add a fifth
dashboard, pick a free port outside this list.

| Port | Dashboard | Data | Run from |
|---|---|---|---|
| 5006 | `battery_feature_dashboard.py` | lab, `old_ul18_12` | needs `LOGBATTEST_Complete/` — see below |
| 5007 | `boxer_battery_dashboard.py` | field, old 41-start dataset | `analytics/` (git-tracked data) |
| 5008 | `battery_feature_dashboard_unit2.py` | lab, `ul18_12_unit2` | needs `ul18_12_unit2/` log folder — see below |
| 5009 | `boxer_can_dashboard.py` | field, 15 CAN taps / 4799 starts | `analytics/` (git-tracked data) |
| 5010 | `main_dashboard.py` (unified, all four above composed into one app) | all of the above | needs the lab log folders too — see below |

```
panel serve battery_feature_dashboard.py --show          # port 5006
panel serve boxer_battery_dashboard.py --show             # port 5007
panel serve battery_feature_dashboard_unit2.py --show     # port 5008
panel serve boxer_can_dashboard.py --show                 # port 5009
panel serve main_dashboard.py --show                      # port 5010
```

**`main_dashboard.py` can only run somewhere both the lab raw logs AND the
field git-tracked data are present together** — in this git checkout alone
that's not true: `battery_feature_dashboard.py`/`_unit2.py` need
`LOGBATTEST_Complete/`/`ul18_12_unit2/`, which are intentionally gitignored
and only exist in the separate `C:\Users\TPKam\Documents\BatPi_Download\`
working copy (see root `CLAUDE.md`'s note on this). As of 2026-08-20,
`main_dashboard.py`, `boxer_can_dashboard.py`, `lab_field_feature_comparison.py`,
`dashboard_notes/`, `dataset_boxer_can/`, and the two lab summary-export
folders (`exports/old_ul18_12_summary_export/`, `exports/ul18_12_unit2_summary_export/`)
were copied into `BatPi_Download/` specifically so `main_dashboard.py` could
be verified end-to-end there — **that copy is not automatic or git-tracked**;
if any of those files change in this repo, re-copy them into
`BatPi_Download/` before relying on the unified dashboard there again.

`main_dashboard.py` composes the other four's already-built Panel objects
into one `pn.Tabs` (see its own module docstring for exactly how, and why
`boxer_battery_dashboard.py` is deliberately NOT one of the sections).

---

## Hardware & battery

**Ultracell UL18-12** — 12V 18Ah sealed VRLA/AGM (valve-regulated lead-acid)

| Property | Value | Source |
|---|---|---|
| Chemistry | VRLA/AGM lead-acid, 6 cells × 2V | Datasheet |
| Part number | UL18-12 | Datasheet |
| Nominal voltage | 12V | Datasheet |
| Nominal capacity (20HR) | 18.0 Ah @ 0.90A to 1.80V/cell | Datasheet |
| Rated capacity (5HR / C/5) | 15.3 Ah @ 3.06A to 1.75V/cell | Datasheet |
| Internal resistance (new) | ~16 mΩ | Datasheet |
| Max discharge current | 270A (5 s) | Datasheet |
| Cycle charge voltage | 14.4–15.0V @ 25°C | Datasheet |
| Standby / float voltage | 13.5–13.8V @ 25°C | Datasheet |
| Operating temp (discharge) | −15 to +50°C | Datasheet |
| Design float life | 5 years @ 20°C | Datasheet |

The C/5 discharge tests in the log files use ~3.6A (= 18Ah ÷ 5).
Measured DCIR during pulse tests (45–130 mΩ) is higher than the datasheet 16 mΩ
because the datasheet value is AC internal resistance at new condition, while DCIR
includes connector resistance, wiring, and ageing effects.

---

## Data directory

All log files live in `LOGBATTEST_Complete/`.  Two file types are used by the dashboard:

### 1. `testday_run_YYYY-MM-DD_HH-MM-SS_bdps.csv` — Pulse test

These are the primary files for degradation tracking.  Each file is one ~20-minute
pulse test session recorded at **~10 Hz** (~12 000 rows).

**Columns**

| Column | Type | Description |
|---|---|---|
| `Timestamp` | ISO-8601 datetime | Wall-clock time of the sample |
| `Elapsed_s` | float | Seconds since start of file |
| `Voltage` | float [V] | Pack terminal voltage (10.19 – 13.0 V typical) |
| `Current` | float [A] | Positive = charge, negative = discharge |
| `Mode` | string | Always `CC` (constant current) |

**Pulse structure** (state sequence within each file)

```
Rest → Discharge → Rest → Discharge → … → Charge → …
```

- **Rest** (`|I| < 0.2 A`): battery sits open-circuit
- **Discharge pulse** (`I < −1 A`): ~25–35 A, lasts 30–120 s
- **Charge pulse** (`I > +1 A`): ~2–3 A, lasts several minutes

A typical file contains **7–8 discharge pulses** and **2 charge pulses**.
Files with fewer than ~5 pulses (e.g. aborted tests) are treated as outliers in the
Degradation Trends tab.

**Date range used by dashboard**: ≥ 2026-01-27 (19 files after cutoff, 14 unique dates).

---

### 2. `discharge_c5_YYYY-MM-DD_HH-MM-SS_bdps.csv` — Full discharge at C/5

These are used exclusively for **State of Health (SOH)** calculation.
Recorded at **~1 Hz** (~4 000 rows for a valid full discharge).

**Columns** — identical schema to testday_run:

| Column | Type | Description |
|---|---|---|
| `Timestamp` | ISO-8601 datetime | Wall-clock time |
| `Elapsed_s` | float | Seconds since start |
| `Voltage` | float [V] | Pack voltage (falls from ~13.5 V to ~10.5 V during discharge) |
| `Current` | float [A] | Negative throughout (~−3.6 A = C/5 rate) |
| `Mode` | string | Always `CC` |

**Quality filter**: files < 20 000 bytes are fragments (incomplete discharges) and are
ignored.  14 valid files remain after filtering.

---

## Features calculated

### From pulse-test files (testday_run)

All features are computed by `session_summary()` in `battery_feature_dashboard.py`.

#### DCIR — DC Internal Resistance [mΩ]

```
DCIR = |V_OCV − V_pulse| / |I_pulse| × 1000
```

- **V_OCV**: mean voltage during the **2 s rest window immediately before** the pulse
  (`|I| < 0.2 A`).  This is the open-circuit voltage estimate.
- **V_pulse**: mean voltage during the **first 3 s of the pulse**.
- **I_pulse**: mean current during the same 3 s window.

Calculated separately for discharge pulses (`DCIR_dis`) and charge pulses (`DCIR_chg`).
The session value is the mean across all pulses of that type in the file.

**IQR outlier filtering** is applied when plotting the trend chart — points beyond
`Q3 + 1.5 × IQR` or below `Q1 − 1.5 × IQR` are shown as faded grey ✕ markers and
excluded from the linear trend fit.

| Feature | Unit | Typical range |
|---|---|---|
| `DCIR_dis [mΩ]` | mΩ | 45–130 (rising with degradation) |
| `DCIR_chg [mΩ]` | mΩ | similar |

#### V_OCV — Open-Circuit Voltage [V]

Mean voltage of the first 20 rest samples at the start of each file.
Used for the whole-session SoC estimate.

#### SoC_start [%] — State of Charge at session start

Estimated from `V_OCV` via a piecewise-linear lookup table for the **Ultracell UL18-12
VRLA/AGM** at 25°C.  The battery must be at rest (no current) for the OCV to be
meaningful — the 2 s rest window before each pulse is used.

| V_OCV (V) | SoC (%) |
|---|---|
| 10.50 | 0 |
| 11.51 | 10 |
| 11.66 | 20 |
| 11.81 | 30 |
| 11.96 | 40 |
| 12.10 | 50 |
| 12.20 | 60 |
| 12.32 | 70 |
| 12.42 | 80 |
| 12.50 | 90 |
| 12.70 | 100 |

Note: VRLA/AGM OCV is temperature-dependent (approx −3 mV/°C per cell for float).
All values above are at 25°C nominal.

#### Charge throughput

```
Q_dis [Ah] = Σ ∫|I(t)| dt / 3600   (over all discharge pulses)
Q_chg [Ah] = Σ ∫ I(t)  dt / 3600   (over all charge pulses)
```

Time steps clipped to [0, 10] s to reject logging gaps.

#### Charge acceptance — CA_dVdt [mV/s]

Rate of voltage rise during constant-current charge pulses:

```
CA_dVdt = (V_eop_chg − V_pulse_chg) / duration × 1000   [mV/s]
```

- `V_eop_chg` = mean voltage in the last 2 s of the charge pulse
- `V_pulse_chg` = mean voltage in the first 3 s of the charge pulse (DCIR window)

**Interpretation**: a healthy VRLA/AGM accepts charge easily → voltage rises slowly (low
CA_dVdt). As the battery degrades (sulphation, capacity loss), charge acceptance drops →
voltage rises faster → CA_dVdt increases. This is one of the earliest and most sensitive
indicators of VRLA/AGM degradation.

Typical range: ~1.5–4 mV/s at these pulse durations (~5 min, 2–3 A).

#### V_chg_peak [V]

Maximum voltage reached during each charge pulse. Rises as charge acceptance decreases.
Reported as the mean across all charge pulses in the session.

#### V_eop_dis [V] — End-of-discharge-pulse voltage

Mean voltage in the **last 2 s** of each discharge pulse. Reflects how far the terminal
voltage has sagged under sustained load — lower V_eop means more voltage sag, indicating
higher impedance and/or lower available capacity.

#### V_recover_dis [V] — Post-discharge recovery voltage

Mean terminal voltage in the **2 s rest window immediately after** each discharge pulse.
Shows the kinetic (diffusion) recovery of the battery: after the ohmic drop disappears,
the voltage relaxes upward. A healthy battery recovers faster and to a higher level.

#### Eta_c [%] — Coulombic efficiency (per test session)

```
Eta_c = Q_chg / Q_dis × 100
```

Ratio of total charge returned (via charge pulses) to total charge drawn (via discharge
pulses) within one test session. Values > 100 % are normal here because the charge pulses
run longer than the discharge pulses — this metric is useful for tracking trends over
sessions rather than as an absolute efficiency.

#### Other session features

| Feature | Description |
|---|---|
| `n_dis_pulses` | Number of discharge pulses detected |
| `n_chg_pulses` | Number of charge pulses detected |
| `I_dis_peak [A]` | Mean peak current of discharge pulses (negative) |
| `dur_dis_mean [s]` | Mean duration of discharge pulses |

---

### From full-discharge files (discharge_c5)

#### Capacity [Ah]

```
capacity_ah = ∫|I(t)| dt / 3600
```

Computed over all samples where `|I| > 0.5 A`.  Only accepted if result ≥ 5 Ah.

#### SOH [%] — State of Health

```
SOH = (capacity_ah / baseline_ah) × 100
```

Two baseline options (selectable in the dashboard):
- **First valid file** — first discharge_c5 file with Q ≥ 5 Ah (≈12.82 Ah → 100%)
- **Nominal 18 Ah** — manufacturer rated capacity

**SOH linkage**: each discharge file is linked to the **2 nearest testday_run files that
come after it** (using all available testday_run files, no date cutoff).  The DCIR from
those linked sessions is plotted alongside SOH to show how impedance tracks health.

---

## Cycle mapping

```
cycle = (rank of unique date among post-cutoff testday_run dates) × 10
```

14 unique dates after 2026-01-27 → cycles 0, 10, 20, …, 130.
Multiple runs on the same day (e.g. two sessions on 2026-02-17) share the same cycle
number.  The x-axis of trend and SOH plots can be toggled between **Date** and
**Cycle count** via radio buttons in each tab.

---

## Test-Day Profile Format (v2) — analysis side

A new "realistic test day" profile replaces the old rest/discharge/charge
pulse pattern with an explicit **event script**, logged per-row via
`Event_Type` / `Event_Index` columns (plus `Profile_Hash` and `Battery_ID`)
instead of being re-derived from current thresholds:

```
ocv_window → wakeup_load → glow_plug_like_load → crank_pulse (cold) → recovery_rest
→ glow_plug_like_load → crank_pulse (hot) → recovery_rest → alternator_charge
→ ramp_like_load / driving_aux_load
```

Rollout status (as of 2026-07-05): `ul18_12_unit2/` has this schema on every
testday_bdps file, including a populated `Battery_ID` column. `old_ul18_12`'s
files in `LOGBATTEST_Complete/` are a partial/earlier rollout — they have
`Event_Type`/`Event_Index`/`Profile_Hash` but **no `Battery_ID` column**, so
those rows resolve to `battery_id="unknown"`. `testday_v2_features.py`'s
`load_run()` only requires the three event columns (not `Battery_ID`) for
exactly this reason, and prefers a logged `Battery_ID` value over the
filename-parsed one when present.

Two independent labels come out of this data — don't conflate them:
1. **Per-run label** = starting OCV, parsed from the filename (`OCV<v>V`).
2. **Per-block label** = measured C/5 Ah capacity, from `Block_<nn>_SOH_C5_bdps_*.csv`,
   shared by every run in that block (10 degradation cycles).

`Battery_ID` is load-bearing: never aggregate across battery IDs implicitly.
`ul18_12_unit2`'s files carry no battery_id filename prefix — the containing
folder (`ul18_12_unit2/`) is what scopes them to that battery, not the
filename. **Never point one battery's dashboard at the other's log folder.**

Crank cold/hot classification and cutoff-hit detection are **not** based on
the fixed thresholds the original spec implies — real logged current
magnitudes didn't reliably distinguish cold vs hot, and a fixed voltage floor
false-positives on normal runs that legitimately dip during `driving_aux_load`.
`testday_v2_features.py` classifies cranks by chronological occurrence
(1st = cold, 2nd = hot, since the event script order is fixed) and flags
cutoff-hit by comparing a run's event count against the norm for that
battery_id. The same pattern (compare against the norm, not a fixed number)
is used for degradation-cycle completeness in
`battery_feature_dashboard_unit2.py::find_degr_cycles` — a cycle can pass a
file-size check yet still be mid-write (e.g. a charge logged partway
through), so Ah values under half the block's own median are flagged
in-progress too.

### ⚠ Known Pi-side logging-rate regression (`ul18_12_unit2`, Block 4-5, RESOLVED 2026-07-16)

Starting mid-Block 4 (`Block_04_Degr_03`, 2026-07-11 21:15) the Pi's BDPS
logging interval degraded badly — from the normal ~1 Hz (Degr/SOH files) or
~20 Hz (testday files) down to a median gap of tens to hundreds of seconds
by Block 5. **This was a logging problem, not battery degradation.**
Confirmed back to normal ~1 Hz as of `Block_05_Degr_08` (2026-07-16 19:45) —
root cause on the Pi side not confirmed from the analysis side; USB drive
space was ruled out.

Impact: the standard Ah calc (`Σ|I|·dt`, dt clipped to 10 s to reject rare
logging gaps) silently *undercounts* badly once the median gap exceeds that
10 s clip — e.g. Block 5's naive SOH C/5 capacity came out as ~4.9 Ah against
a real duration-implied capacity of ~12.1 Ah. Both `_block_c5_capacity_ah()`
(testday_v2_features.py) and `_cycle_ah()` (battery_feature_dashboard_unit2.py)
detect a coarse median gap (`SAMPLE_GAP_COARSE_S = 5.0`) and, for genuinely
constant-current segments (SOH C/5 discharge, Degr discharge — confirmed
steady current start to finish), substitute `duration × mean|I|`, which is
immune to sample sparsity. **Degr charge cycles cannot be corrected this
way** — they're CC/CV (current tapers from ~5 A to <1 A near the end), so a
coarse-sampled charge file has no reliable reconstruction; it's flagged
`coarse_sampling`/incomplete instead of guessing a number. Some affected
cycles (e.g. Block 5 Degr cycle 7) were re-run cleanly after the fix,
producing duplicate files for the same (block, cycle)/(block, OCV) identity
— `find_v2_testday_runs()` and `find_degr_cycles()` both dedupe on this by
keeping the file with the latest timestamp (`ts` sorts lexicographically the
same as chronologically), so a retry always wins over the original.

### Live-updating SOH C/5 files (`ul18_12_unit2`, not a bug)

A block's SOH C/5 file can be mid-write on the Pi at the moment this repo
syncs it — e.g. `Block_06_SOH_C5_bdps_2026-07-17_08-36-04.csv` read 12.07 V
after only 5625 s when first checked (2026-07-17), while every *completed*
discharge (Blocks 1-5) ends consistently at ~10.78-10.79 V. That gap is just
the test still running, not an interrupted/failed test — confirmed by the
Pi side. Don't read a not-yet-final SOH point as a real capacity drop.

`find_block_soh()` / `find_block_soh_df()` (testday_v2_features.py) return a
`reached_cutoff` flag (`SOH_CUTOFF_V = 11.0`) alongside the Ah value —
`battery_feature_dashboard_unit2.py`'s SOH History chart plots a
cutoff-not-reached point as a separate, disconnected series (amber ✕)
labelled "not final yet" rather than joining it into the trend, and the
block-level SOH predictor table excludes it entirely from both "this block"
and "next block" correlation targets. Just rescan once the test completes —
no Pi-side action needed.

### Test complete (`ul18_12_unit2`, 20/20 blocks, 2026-08-02) — two Ah-floor bugs fixed

The full 20-block plan finished cleanly: `Block_20_Degr_10_charge` (the last
file, 2026-08-02 17:40) is preceded by a complete per-block sequence
(`charge_full → SOH_C5 → sweep_charge → SoC-sweep testday/stepdown → 10×
Degr cycles`), no `Block_21_*` files exist, and every bdps file has its
sensor pair. Checking this surfaced two real bugs, both from hardcoded
absolute thresholds that made sense when the battery was healthy but
silently broke as capacity fell well below their assumptions over 20 blocks
of degradation (18.16 Ah → 0.94 Ah):

1. **`find_degr_cycles()`'s "below half the block's own median" completeness
   check was computing one median across *all* blocks combined**, not
   per-block (missing `.groupby("block")`). Early blocks' ~8-12 Ah cycles
   dominated that global median, so *every* cycle in the later, genuinely
   ~1 Ah blocks fell under half of it and got wrongly flagged incomplete —
   100% of cycles in blocks 14-20 were misflagged this way before the fix.
   Fixed with a proper per-block `.transform("median")`.
2. **`_block_c5_capacity_ah()` (testday_v2_features.py) rejected any Ah
   value below a hardcoded 1.0 Ah floor**, meant to catch obviously-bogus
   readings. Block 20's SOH discharge is real, healthy-sampled, and ends at
   a genuine ~10.75 V cutoff — it's just legitimately 0.94 Ah. The floor
   silently dropped it (not even as a "not final yet" row — just absent).
   Floor removed; `dis.empty` is the only "no real reading" guard needed.

Both are the same lesson repeated: **don't hardcode absolute thresholds
against a quantity that trends toward zero over the test's own lifetime** —
compare against the run's own recent scale instead. Test-day (SoC-sweep) run
counts *legitimately* shrink per block from block ~8 onward (down to 2-9,
vs. a steady 9-10 in blocks 1-7) as the narrowing usable OCV range yields
fewer step-down points before cutoff — confirmed physically consistent
(e.g. Block 20 sweeps only 12.37-13.50 V with <1 Ah total capacity), not
missing data.

---

## Dashboard tabs

| Tab | Purpose |
|---|---|
| **Run Inspector** | Annotated V/I signal for one testday_run; DCIR per pulse shown as red/green labels; SoC shown in title |
| **Compare Runs** | Overlay voltage and current for multiple testday_run files on a shared elapsed-time axis |
| **SOH History** | SOH over time from discharge_c5 files; lower panel shows DCIR from the 2 linked testday_run files |
| **Degradation Trends** | Any session feature plotted vs date or cycle count; linear trend fit on non-outlier points |
| **SoC Sweep** | Old-schema testday profile run at multiple SoC levels per block; feature vs OCV/SoC |
| **Test-Day v2 (Beta)** | Event-driven feature extraction for the new v2 test-day profile (see above); crank R_int trend + block-level C/5 SOH; empty until backend.py logs the v2 columns |
| **Feature Correlations** | Spearman correlation heatmap + pairwise scatter across computed feature sets |
| **File Inventory** | One row per known log file / session, for auditing what's on disk |
| **Test Plan** | Reconstructs test blocks (degradation cycles + test days + SOH test) from filenames |
| **Export** | Save Excel (3 sheets: Run Summary, SOH History, Cycle Map) + PNG/HTML images |

---

## Key files

| File | Role |
|---|---|
| `battery_feature_dashboard.py` | `old_ul18_12` dashboard — all feature extraction, plotting, and UI |
| `battery_feature_dashboard_unit2.py` | `ul18_12_unit2` dashboard — reuses testday_v2_features.py; has its own Run Inspector/Compare/SoC Sweep/Degradation Trends/Feature Correlations/SOH/Degradation Cycles/File Inventory/Export tabs sized for a fresh, still-in-progress dataset |
| `testday_v2_features.py` | Event-driven feature extraction for the v2 test-day profile (see above); shared by both dashboards |
| `calculate_soh_history.py` | Standalone SOH calculator (reference, not used by dashboard) |
| `combine_files.py` | One-off script that merged two partial discharge log files |
| `LOGBATTEST_Complete/` | All raw CSV log files for `old_ul18_12` |
| `ul18_12_unit2/` | All raw CSV log files for `ul18_12_unit2` (own folder per battery — no filename prefix needed since the folder itself scopes it) |
| `exports/` | Output folder for Excel and PNG exports |

---

## Key constants (battery_feature_dashboard.py)

```python
DCIR_WIN_S     = 3.0    # seconds at pulse start averaged for DCIR
OCV_WIN_S      = 2.0    # seconds of prior rest averaged for V_OCV
PULSE_THRESH_A = 1.0    # |I| > this → pulse active
REST_THRESH_A  = 0.2    # |I| < this → rest
TESTDAY_CUTOFF = "2026-01-27"   # display cutoff for trend/compare tabs
SOH_MIN_BYTES  = 20_000         # minimum file size for a valid discharge_c5
SOH_MIN_AH     = 5.0            # minimum discharge capacity to count as SOH measurement
```

---

# Boxer Battery — Motor-Start CAN Export Dashboard

Field-side counterpart to the BatPi lab dashboards above: real engine-start
events tapped off the CAN bus of Boxer armoured vehicles (EnerSys ArmaSafe
Plus 12FV120, 24V 2S2P pack), used to check whether lab-derived SOH features
transfer to the field (main.tex's "Lab-to-Field Validation Procedure").

See `HANDOFF_boxer_can_dashboard.md` for the narrative/results summary of
the 2026-08-06 session that built this section (what was tried, what was
rejected and why, concrete numbers) — this section itself is the living
technical reference (file roles, constants, how to re-run things).

Run the dashboard:
```
panel serve boxer_can_dashboard.py --show
```
**Not `boxer_battery_dashboard.py`** — that's a separate, older, much
smaller dashboard covering only the original 41-start EnerSys dataset in
`dataset_boxer/start_data_battery/my_start_csv/`, and predates everything
below. `boxer_can_dashboard.py` is the current one: 15 taps, 4799 starts,
7 tabs including Outlier Inspector and Lab vs Field Comparison.

## Data pipeline

Raw export lives OUTSIDE this repo (multi-hundred-MB per tap, never
git-tracked), currently at:
```
C:\Users\TPKam\OneDrive - University of Twente\Accu_dataset_Boxer_anonimised\
    motor_starts_can_tapperNN_tb60_ta120_<timestamp>.zip
```
`build_boxer_can_features.py --zips-dir <that path>` reads each zip, derives
`crank_duration_s`/`glow_lead_s` (from CAN commando event timing) and the new
`post_start_V_std_1_10V` field feature (field analogue of the lab's
`driving_aux_load_V_std` — the strongest SoC-robust indicator from the lab
campaign), and writes only the small per-start tables into
`dataset_boxer_can/<tapper>/` (git-tracked). `can_tapper11` IS the original
41-start dataset, start-for-start identical.

Multi-part tappers (currently `can_tapper14`, `can_tapper15`): a single
export got too large and was split into two zips with non-overlapping date
ranges; the script merges them per-tapper with an incrementing start_index
offset applied *after* each part's own internal joins. Byte-identical
duplicate zips (e.g. a browser "file (1).zip" re-download, seen on
`can_tapper17`) are detected by content hash and skipped.

Re-run after any new zip upload:
```
python build_boxer_can_features.py --zips-dir "C:\Users\TPKam\OneDrive - University of Twente\Accu_dataset_Boxer_anonimised"
```

## `lab_field_feature_comparison.py` — Lab vs Field Comparison tab

Matches three feature families with a genuine counterpart on **both lab
batteries** (`old_ul18_12`, `exports/old_ul18_12_summary_export/testday_v2_beta_partial.csv`,
8 runs, partial v2-schema rollout; `ul18_12_unit2`,
`exports/ul18_12_unit2_summary_export/testday_features.csv`, 140 runs) and
the field (Boxer CAN) side: crank apparent internal resistance, post-crank
recovery voltage, post-crank voltage stability. Framed explicitly as a
*feature-family consistency check*, not a value-for-value validation —
different battery products, separate pipelines, no shared ground-truth SOH
on the field side. `old_ul18_12`'s 8-run sample is a coarse spot-check, not
statistically weighted equally against `unit2`'s 140 runs.

Each family gets its own figure with one box+strip panel *per
(battery, condition)* — lab old_ul18_12 cold-crank / old_ul18_12 hot-crank /
unit2 cold-crank / unit2 hot-crank / field, etc. — never pooled. Color
convention: blue = lab, orange = field (fixed); within lab, the light/dark
blue shade normally distinguishes cold vs. hot condition, but for the
single-condition V_std family (no cold/hot axis) it distinguishes battery
identity instead (old_ul18_12 vs. unit2) — battery/condition identity is
always in the panel's text label too, never carried by color alone. Panel
axes are capped to a robust (Tukey k=3 IQR) range so a few extreme points
can't compress the rest of the distribution into an unreadable sliver; each
panel's subtitle reports how many points were clipped and the most extreme
value, and the tab's bottom table always shows true, uncapped stats.

**Known caveat — lab crank current is not realistic.** Both lab batteries'
logged crank current (`crank_cold/hot_I_peak_A`, old_ul18_12 median
~2.6-3.1 A, unit2 median 3.5-7.1 A) is ~2 orders of magnitude below a real
engine-crank current (field `I_load_A`, median ~400 A) — documented in
`testday_v2_features.py` as "likely a low-current bench/validation run".
Since `R_int_apparent = ΔV / I_peak`, this makes lab-side crank-R_int
noise-dominated on both batteries (unit2: cold mean ~1.7 mΩ, hot mean
~214 mΩ; old_ul18_12: cold mean ~1.9 mΩ, hot mean ~685 mΩ — **not** a real
cold/hot physical difference on either battery, just noise on a tiny
denominator). That panel is a sanity check on the field pipeline's own
internal consistency, not a lab-field agreement claim.

**Known caveat — field R_int needed the same diagnostic, and wasn't clean
(found 2026-08-20, baked into the pipeline as of the same date).**
`exports/RESULTS_SUMMARY_FOR_PAPER.md` §1 explicitly flagged this as not yet
done: "`boxer` (field data) does *not* need this kind of filter" was true of
the *old* 41-start dataset (no per-row current at all), but the newer CAN
export (`boxer_can_features_enriched.csv`) does log `I_load_A`, and applying
the same correlate-R_int-against-current check used on the lab data
(Spearman r = -0.52 to -0.59 across all three R_int columns) found two
distinct problems, not one:
1. A **sign-flip cluster, mostly on `can_tapper14`**: `I_load_A` is
   large-magnitude but *negative* (~-400 to -500 A) for real crank events
   (10-17% voltage drop) — not a low-current issue, and R_int is still wrong
   there (up to 8200 mΩ) for a reason not fully diagnosed (the per-start
   R_int figure is computed upstream, inside the original zip export, not by
   `build_boxer_can_features.py`). 50 starts total have negative `I_load_A`;
   14 of those fall inside the tapper31 dropout window below and get that
   flag instead; of the remaining 36, 31 are on `can_tapper14`.
2. **Scattered low-current starts across nearly every tap**, not just the
   already-documented `can_tapper31` dropout window: `I_load_A < 200 A`
   (well below the ~300-500 A bulk) is where all three R_int columns cross
   from a physically plausible range (median 9-12 mΩ, max 58-100 mΩ) into
   implausible territory (max in the thousands of mΩ).

**This is now a permanent, pipeline-baked flag, not a downstream filter.**
`build_boxer_can_features.py`'s `add_r_int_validity_flags()` writes three
columns straight into `boxer_can_features_enriched.csv` on every
regeneration:
- `invalid_R_int` (bool) — `I_load_A < 200` or missing. One shared flag for
  all three R_int columns (they share the same current denominator; checked
  that this is actually true rather than assumed — the three columns' own
  NaN patterns differ slightly for unrelated reasons, but whether
  `I_load_A` itself is trustworthy for a row does not).
- `R_int_current_sensor_dropout` (bool) — the can_tapper31 window
  specifically (a subset of `invalid_R_int` with a known root cause).
- `R_int_negative_current` (bool) — `I_load_A < 0` (also a subset, also a
  distinct, separately-flagged pattern).

306 of 4799 starts (6.4%) are flagged. Every tab that plots R_int
(Feature Trends, Tap Comparison, SoC & Temp Effects, Outlier Inspector, and
the Lab vs Field Comparison tab via `lab_field_feature_comparison.py`) marks
these points with `FLAGGED_MARKER` (grey ✕, same convention across every
tab/dashboard) rather than excluding them — box/trend statistics are
computed on the unflagged subset, but the points themselves stay visible.
`can_tapper14`'s 17.9% flagged rate (vs. 0-12.5% on every other tap) has no
identified root cause — see `dashboard_notes/outlier_known_incidents.md`.
Full writeup with per-tapper breakdown:
`exports/boxer_can_summary_export/NOTES.md`.

## Outlier Inspector tab

Interactive: pick a feature (voltage drop %, R_int mΩ, post-start V_std) and
CAN tap, adjust an IQR-fence sensitivity slider (k, default 3.0 = Tukey "far
out"), get a timeline scatter + two live-filtered tables (flagged starts at
pack level, and the same starts' per-group breakdown), plus a raw-trace
panel for whichever row is selected (see below).

### Known data-quality incidents (found 2026-08-06)

- **`can_tapper31` — current-sensing dropout, 2026-06-11 19:38 to
  2026-06-12 21:52 (start_index 388-404, 17 starts, ~26h window).**
  `I_load_A` collapses to single digits instead of the normal ~400 A seen
  immediately before/after this window. `R_int_mohm = ΔV/I_load` blows up to
  nonsense values (up to 3200 mΩ) — **all 11 of the dataset's
  `R_int_mohm > 50 mΩ` outliers are from this one window on this one tap.**
  Voltage sampling in the same window looks normal, so this is a
  current-channel-specific fault, not a general logging dropout. Root cause
  not confirmed from the analysis side.
- **`can_tapper33` — severe voltage-drop cluster, 2025-12-16 11:13-11:58
  (start_index 20-23, 4 starts, ~45 min).** Pack voltage drop 44.6-54.9%
  (vs. ~9% dataset median), cold ambient (4.3-6.5°C), but *normal* crank
  current (380-500 A) and all four pack groups sagging together — a real
  electrical event (cold, weak battery under load), not a sensor artifact.
  One point (start_index 21, group `PG1_est`) sags far more than its own
  siblings (83% vs. 44-46%) — `PG1_est` has no direct sensor, so that one
  point is more suspect than the rest.

### Raw-trace drill-down (select a flagged row → see its actual voltage trace)

Selecting a row in the flagged-starts table shows the raw -60s..+120s
per-group voltage trace (+ CAN commando event markers) for that exact start,
via `outlier_raw_traces.csv` / `outlier_raw_events.csv`.

**These two files are NOT generated by the normal `build_boxer_can_features.py`
run** — they need the extra `--extract-outlier-traces` flag, which does a
second pass over the zips (grid CSVs can't be randomly row-accessed, so each
part's full grid is read once and filtered):
```
python build_boxer_can_features.py --zips-dir "<path>" --extract-outlier-traces
```

⚠ **Design note — do NOT change this to "every start beyond a fixed IQR
fence"**: that was tried first and produced a **1 GB file**.
`post_start_V_std_1_10V` alone flagged 653 of 4785 starts (13.6%) at the
dashboard's loosest fence (k=1.5), because a *global* fence pooled across 15
different vehicles mostly detects normal vehicle-to-vehicle differences in
baseline accessory-load behaviour, not real anomalies — the tail is fat by
nature, not by fault. The fix: `_outlier_keys()` in
`build_boxer_can_features.py` instead ranks every start by IQR-normalized
distance from the median and takes the top `OUTLIER_TRACE_TOP_N` (currently
60) *per tracked feature*, unioned and deduped — bounded to a predictable
size (157 unique starts, ~82 MB at N=60) no matter how fat any one feature's
tail is. Consequence: raw-trace coverage is "the most extreme N per
feature", not "everything the live sensitivity slider can ever flag" —
`boxer_can_dashboard.py` degrades gracefully ("not available") for a
selected start outside that set, which is expected, not a bug (confirmed via
`can_tapper15` start_index 7: a real 24.2% voltage drop, ranked #48, just
outside the then-N=40 cutoff; bumping to N=60 covered it). Bump
`OUTLIER_TRACE_TOP_N` if more coverage is wanted, re-run
`--extract-outlier-traces` afterward, and sanity-check the resulting file
size before treating it as done (rough size ≈ n_unique_starts × 9001 rows ×
~45 bytes/row).

## Key files

| File | Role |
|---|---|
| `main_dashboard.py` | Unified dashboard — composes all four below into one app on one URL/port (5010); see its own module docstring for exactly what it reuses vs. builds fresh |
| `boxer_can_dashboard.py` | The dashboard — Start Profile, Feature Trends, Tap Comparison, SoC & Temp Effects, Post-Start Stability, Outlier Inspector, Lab vs Field Comparison |
| `build_boxer_can_features.py` | Reads raw zips (`--zips-dir`), writes `dataset_boxer_can/` (including the `invalid_R_int`/`R_int_current_sensor_dropout`/`R_int_negative_current` flag columns via `add_r_int_validity_flags()`); `--extract-outlier-traces` additionally writes the raw-trace drill-down files |
| `exports/boxer_can_summary_export/` | R_int reliability diagnostic writeup + `summary_stats.csv`/`invalid_R_int_by_tapper.csv` for the CAN dataset — companion to `exports/RESULTS_SUMMARY_FOR_PAPER.md` §1 |
| `lab_field_feature_comparison.py` | Lab-vs-field feature-family comparison logic for BOTH lab batteries (shared by `boxer_can_dashboard.py`'s tab, `main_dashboard.py`'s top-level section, and runnable standalone) |
| `dataset_boxer_can/` | Git-tracked small per-start tables for all 15 taps + `outlier_raw_traces.csv`/`outlier_raw_events.csv` |
| `dataset_boxer/start_data_battery/my_start_csv/` | The original 41-start-only dataset used by the separate, older `boxer_battery_dashboard.py` (kept intentionally — see that file's own docstring) |
