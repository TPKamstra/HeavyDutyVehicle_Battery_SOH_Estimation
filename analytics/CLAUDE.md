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
