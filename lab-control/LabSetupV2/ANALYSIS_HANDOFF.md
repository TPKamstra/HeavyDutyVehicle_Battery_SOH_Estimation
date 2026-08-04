# Test-day profile v2 → feature extraction: what the analysis side needs to know

This is the companion note to the Pi-side implementation (`backend.py`,
`Classes/functions_class.py`, see `CLAUDE.md` → "Test-Day Profile Format
(v2)"). It's written for whoever (human or Claude Code) builds the
analysis script / dashboard that turns the logged runs into ML-ready SOH/SoC
features. It assumes no memory of the Pi-side conversation — everything you
need to know about the logs themselves is below.

## The research question, concretely

The real-world use case (Boxer vehicles) only ever observes **OCV off the
CAN bus at rest** — never true Ah capacity (SOH) and never true Coulomb-
counted SoC. The lab rig can't measure true SOH either in general (it's a
research target, not an input), but it *can* measure one honest ground
truth: the **C/5 discharge capacity in Ah**, run once per block right before
each SoC sweep (`_run_discharge_c5_block` in `backend.py`, logged as
`last_capacity_ah`, filename `Block_<nn>_SOH_C5_bdps_<ts>.csv`). Two
different modelling tasks fall out of this, don't conflate them:

1. **Per-run label = starting OCV** (parsed from the run's filename, see
   below). This is what you'd use to build a "given a profile snippet, what
   was OCV/SoC at the start" model — mirrors exactly what the vehicle could
   in principle learn from repeated field trips.
2. **Per-block label = measured C/5 Ah capacity**. This is the actual SOH
   ground truth, but it only changes once per block (10 degradation cycles),
   not once per profile run — all 10 SoC-sweep runs in a block share the same
   block-level SOH label. Use this for a degradation-trend model (Ah vs.
   block number), and to check whether any within-block feature (e.g. crank
   sag) correlates with the *next* block's measured capacity.

Already known from prior testing (see `[[project_phd_picode]]` memory): the
Ultracell UL18-12 under test tonight delivered only **~8 Ah** on a full C/5
discharge against its 18 Ah nameplate rating — it's heavily degraded. Expect
degenerate runs (early cutoff-voltage hits) from the 4th SoC step-down
onward. Don't treat those as bad data — the cutoff-hit itself, and the OCV
at which it occurred, is a signal.

## Battery ID — read this before pooling any data

Every log now lives under a **per-battery subfolder**,
`LOG_ROOT/<battery_id>/...` (`_path()` in `backend.py`, creates the folder
on first use), and every logged BDPS row also carries the same value in a
`Battery_ID` column, so it's disambiguated at both the directory and row
level. Tonight's already-completed run used `BATTERY_ID = "old_ul18_12"`
and — because that folder scheme didn't exist yet when it ran — its files
were moved by hand into `LOG_ROOT/old_ul18_12/` afterward (they predate the
`Battery_ID` column entirely; identify them by folder only). **Before
running against a different physical battery, `BATTERY_ID` in `backend.py`
must be changed** — if that's not done, the new battery's runs land in the
old battery's folder and get tagged with its `Battery_ID`, corrupting any
degradation-trend analysis across batteries. Always treat the top-level
subfolder (or the `Battery_ID` column) as the first grouping key in any
aggregation across more than one run — never assume date range is enough.

## File naming (LOG_ROOT = `/media/pi/LOGBATTEST`)

```
old_ul18_12/                                      # one subfolder per battery_id
    SoCsweep_sweep_charge_bdps_<ts>.csv            # standalone SoC sweep's own charge-to-100%
    SoCsweep_sweep_charge_sensor_<ts>.csv
    SoCsweep_OCV<v>_testday_bdps_<ts>.csv          # one per SoC sweep point, e.g. OCV12p70V
    SoCsweep_OCV<v>_testday_sensor_<ts>.csv
    SoCsweep_OCV<v>_stepdown_bdps_<ts>.csv         # CC pulses + OCV rest rows between points
    SoCsweep_OCV<v>_stepdown_sensor_<ts>.csv

    Block_<nn>_charge_full_bdps_<ts>.csv           # full-plan: once per block, before the SOH test
    Block_<nn>_SOH_C5_bdps_<ts>.csv                # the real Ah ground truth, once per block
    Block_<nn>_sweep_charge_bdps_<ts>.csv          # SoC sweep's own recharge, after SOH test drained it
    Block_<nn>_OCV<v>_testday_bdps_<ts>.csv
    Block_<nn>_OCV<v>_stepdown_bdps_<ts>.csv
    Block_<nn>_Degr_<cycle>_discharge_bdps_<ts>.csv   # 10x per block, not test-day related
    Block_<nn>_Degr_<cycle>_charge_bdps_<ts>.csv

    profile_single_<ts>_bdps.csv                   # ad hoc single profile run (Profile tab)
ul18_12_unit2/                                     # next battery gets its own subfolder
    ...
```

**Important**: within a full-plan block there are now *two* charge-full-style
files — `Block_<nn>_charge_full_bdps_*.csv` (the block-level top-off before
the SOH test) and `Block_<nn>_sweep_charge_bdps_*.csv` (the SoC sweep's own
recharge after the SOH test drained the battery again). They're logically
different events (pre-SOH top-off vs. post-SOH recharge) — don't average or
conflate them if you're using charge behaviour as a feature.

`OCV<v>` decodes as: `.` → `p`, so `OCV12p70V` = 12.70 V starting OCV for
that run. This is the per-run label described above (task 1) — parse it with
`float(re.search(r"OCV(\d+p\d+)V", name).group(1).replace("p", "."))` or
equivalent. `<nn>` in `Block_<nn>` is the block index (1..20 in a full plan);
that's your grouping key for the block-level SOH label (task 2).

**Per-block order changed**: it's now full charge → SOH C/5 → SoC sweep → 10
degradation cycles (previously degradation cycles came first). This matters
for the block-level SOH label — it's measured right after a guaranteed
100%-charge, before that block's SoC sweep and degradation cycles run, so it
reflects the battery's capacity *entering* the block, not exiting it.

**Timestamps are per-test, not per-block/per-sweep**: each `<ts>` is
generated immediately before that specific test's files are created, so a
block's 10 degradation discharge/charge pairs, and a sweep's charge + 10
test-day runs + step-downs, each carry their own start time rather than one
timestamp shared across everything in that block/sweep (which could
otherwise span many hours).

If a full plan run halts partway (hardware fault), the console log will
contain a `FULL PLAN HALTED — Block X, stage: Y` line — cross-reference
that against the last complete file for battery `X`'s folder to know exactly
which run's data is partial/missing.

## CSV schemas

**BDPS log for a test-day profile run** (`*_testday_bdps_*.csv`,
`_run_profile_sync` in `backend.py`):
```
Timestamp, Elapsed_s, Voltage, Current, Mode, Event_Type, Event_Index, Profile_Hash, Battery_ID
```
- `Timestamp`: wall-clock ISO, `Elapsed_s`: seconds since this run started —
  use `Elapsed_s`, not `Timestamp`, to align against the profile CSV's own
  `Time (s)` since they're both zero-based per run.
- `Voltage`, `Current`: **measured**, from `readVoltage()`/`readCurrent()` —
  this is the real observation, unlike the profile CSV's
  `V_expected_sim (V)` column which is never logged here at all.
- `Event_Type` / `Event_Index`: copied straight from the profile CSV row
  that was active at that sample — use this to group rows into events
  without re-deriving boundaries from current changes.
- `Profile_Hash`: sha256 of the exact profile CSV file used for this run
  (computed once at load time from the file bytes — there was no JSON
  sidecar delivered with this profile, so this is the only disambiguator; if
  a future profile drop *does* ship a `.json` sidecar with
  `content_hash_sha256`, cross-check it matches this column). Every row in
  a run has the same hash — treat a run with more than one distinct hash
  value as corrupted (e.g. the file was reloaded mid-run).
- `Battery_ID`: constant per run, see above.

**BDPS log for charge / discharge / step-down runs** (everything else —
`functions_class.py`'s `charge_battery_cc_cv`, `discharge_cc_until_voltage`,
`discharge_cc_fixed_ah`, `discharge_fixed_time`, `discharge_cc_to_ocv_target`):
```
Timestamp, Elapsed_s, Voltage, Current, Mode, Battery_ID
```
No `Event_Type`/`Event_Index`/`Profile_Hash` — these functions don't replay
the test-day profile, so there's no event structure. `Mode` is `CC`, `CV`,
or `OCV` (the resting-measurement rows in the step-down log, currentA
logged as `0.0` by convention, not a real reading).

**Sensor log** (Arduino, 20 Hz, all run types):
```
timestamp, voltage, current, temperature, humidity
```
No `Event_Type` or `Battery_ID` column here — the filename carries the
battery ID (see naming above) but individual rows don't. If you need
event-level granularity on sensor data (e.g. temperature during the crank
pulse), you must **join against the paired BDPS log by nearest timestamp**
(`pandas.merge_asof`, sorted, tolerance ~0.1 s) — do not assume row-for-row
correspondence between the sensor and BDPS logs even though both run at
20 Hz; they're two independent loops with their own jitter, not
synchronized samples.

## Feature extraction, event by event

Group profile-run BDPS rows by `Event_Index` (not `Event_Type` alone — two
crank pulses in one run share the type but not the index). Suggested
features per event type — starting point, not exhaustive:

- **`ocv_window`** (60 s, I=0, immediately before wake-up loads): this is
  the **authoritative rested V_pre** for the run. Feature: mean voltage over
  the last ~20 s of the window (skip the leading edge in case the previous
  event's transient hasn't fully settled). Do **not** substitute a "last N
  samples before current changes" heuristic elsewhere in the run for this —
  this event exists specifically so you don't have to.
- **`wakeup_load`**: initial small load step. Feature: sag from
  `ocv_window` V_pre at low current → a first-pass, low-current R_int
  estimate.
- **`glow_plug_like_load`**: runs directly into `crank_pulse` with **no
  rest between them** (that's deliberate — matches real key-on→crank
  behaviour). Feature: mean/last voltage of this event = **V_pre_crank**,
  the "warm" pre-crank voltage. Use *this*, not the earlier `ocv_window`
  V_pre, as the reference voltage for the crank internal-resistance
  calculation below — the glow-plug load has already pulled the battery off
  its rested OCV by the time crank starts.
- **`crank_pulse`** (appears twice per run — cold and hot; distinguish by
  peak `|Current|` magnitude, e.g. >65 A → cold (~75 A spec), otherwise hot
  (~55 A spec) — more robust than assuming file order):
  - `V_min`: minimum voltage in the first ~0.3–0.5 s (cold) / ~0.2–0.3 s
    (hot) of the event — the true inrush-sag floor. At 20 Hz this is ~6–10
    rows; don't average over the whole event or you'll wash out the spike.
  - `I_peak`: read the actual peak from the `Current` column, don't
    hardcode −75/−55 — the generator's parameters can change run to run.
  - `R_int_apparent = (V_pre_crank − V_min) / |I_peak|` — track this
    run-over-run and block-over-block; rising apparent R_int is a classic
    lead-acid degradation signature and is a strong SOH-trend candidate.
  - `V_sustained`: mean voltage over the last ~1 s of the event (the held
    −38 A / −30 A cranking plateau after the spike decays).
  - Optionally fit an exponential decay time constant from `V_min` back to
    `V_sustained` for a richer polarization-recovery feature.
- **`recovery_rest`** (I=0, right after crank): voltage-recovery
  trajectory. Feature: V at a fixed offset into the rest (e.g. +30 s) as a
  second, independent post-load polarization/R_int estimate, and/or a
  fitted recovery time constant.
- **`alternator_charge`** (now charge-neutral by design, returns
  `charge_return_fraction × Ah discharged so far` — default 1.0): integrate
  `Current × dt` over the event for `Ah_charged`, compare against the sum
  of `|Current| × dt` over all prior discharge events in the same run for
  `Ah_discharged_so_far`. The ratio should track the configured
  `charge_return_fraction` if the BDPS is tracking setpoint accurately —
  but a deviation here isn't necessarily a bug: reduced charge acceptance is
  itself a legitimate degradation signature in lead-acid cells, so this
  ratio is a candidate SOH feature in its own right, not just a QC check.
- **`ramp_like_load`** / **`driving_aux_load`**: sustained variable-current
  segments simulating driving. Feature: mean/std voltage under load; since
  current varies within the segment, you can fit a mini droop curve
  (voltage vs. current) across the segment's own current steps for another
  independent R_int estimate — useful as a cross-check against the crank-
  pulse-derived one.
- **Run-level aggregates** (one row per run, for the ML-ready table): total
  Ah discharged, total Ah charged, run duration, whether a cutoff-voltage
  event occurred mid-run (and at what elapsed time / OCV), plus the
  metadata columns below.

## Suggested script shape

```python
def load_run(bdps_path, sensor_path=None) -> pd.DataFrame:
    """Load one profile run's BDPS log; optionally merge_asof the paired
    sensor log by nearest timestamp to pull in temperature/humidity."""

def extract_features(bdps_df: pd.DataFrame) -> dict:
    """Group by Event_Index, dispatch per Event_Type, return one flat
    feature dict for the run (crank_R_int_cold, crank_R_int_hot,
    ocv_window_Vpre, alternator_charge_ratio, ...)."""

def build_dataset(log_root, battery_id=None) -> pd.DataFrame:
    """Glob `{battery_id or '*'}_*testday_bdps_*.csv` under log_root, parse
    OCV label + block index + battery id from each filename, verify
    Profile_Hash is constant within the run, call extract_features, and
    assemble one row per run — this is the ML-ready table. Join in the
    block-level C/5 Ah capacity (task 2 above) from the matching
    `Block_<nn>_SOH_C5_bdps_*.csv` for that block/battery_id."""
```

Keep `battery_id` as an explicit column all the way through — never merge
rows across battery IDs implicitly.
