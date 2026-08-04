# PhD_PiCode — Battery Degradation Test System

## Project Overview

This is a PhD research system running on a Raspberry Pi that performs automated degradation testing on a **Ultracell UL18-12 lead-acid battery** (12 V, 18 Ah VRLA). It controls a bidirectional power supply (BDPS) over TCP/SCPI, reads sensors via an Arduino over serial, and presents a live Panel web dashboard for monitoring and control.

## Hardware

### Battery Under Test: Ultracell UL18-12
- **Nominal:** 12 V, 18 Ah (20-hour rate)
- **Charge (CC/CV):** max initial current 5.4 A, target voltage 14.4–15.0 V at 25 °C
- **CV taper cutoff:** ~0.3 A (current tapers to this in CV mode when full)
- **Discharge cutoffs:** 10.8 V (1.8 V/cell) for standard tests, down to 9.6 V (1.6 V/cell) for high-rate tests
- **Max discharge:** 270 A for 5 s; internal resistance ≈ 16 mΩ
- **Temperature range:** discharge −15 to 50 °C, charge 0 to 40 °C
- **Design life:** 5 years at 20 °C float

### BDPS — Bidirectional Power Supply (SM500-CP-90)
- **IP:** `192.168.2.130`, port `8462` (TCP)
- **Protocol:** SCPI text commands, newline-terminated
- **Max:** 90 A, 15 V, 1500 W; can both source (charge) and sink (discharge)
- **Key commands:**
  - `SOUR:FUNC VOLT` / `SOUR:FUNC CURR` — switch between voltage and current control
  - `SOUR:VOLT <v>` / `SOUR:CURR <a>` — set setpoint (negative current = sink/discharge)
  - `SOUR:CURR:POS <a>` / `SOUR:CURR:NEG <a>` — asymmetric current limits
  - `SOUR:POW: <w>` / `SOUR:POW:NEG <w>` — power limits (note the space in `SOUR:POW:`)
  - `OUTP ON` / `OUTP OFF` — enable/disable output
  - `MEAS:VOLT?` / `MEAS:CURR?` — read terminal voltage and current
  - `SYST:SENS:REM OFF` — use local sensing (always set this before outputOn)
- `sendCommand()` for write-only commands; `sendAndReceiveCommand()` for queries (must end with `?`)

### Arduino Sensor Board
- **Port:** `/dev/ttyACM0`, 115200 baud
- **Sampling rate:** 20 Hz (50 ms loop delay)
- **Sensors:**
  - **ADS1115** (I2C, 0x48, GAIN_ONE): A0 = current sense (HASS 50-S hall effect, 12.5 mV/A, zero offset 2.523 V); A3 = battery voltage (÷5 voltage divider, scale 5.0×)
  - **SHT20** (I2C): temperature + humidity, updated at 1 Hz
- **Serial output format:** `voltage,current,temperature,humidity\n` (all floats, 2 decimal places)

## Software Architecture

### Entry Point
```
LabSetup/main_dashboard.py
```
Run from `LabSetup/` directory:
```bash
cd /home/pi/PhD_PiCode/LabSetup
python main_dashboard.py
```
Serves Panel dashboard on **port 39517**. WebSocket origins configured for `localhost`, `batterytestpi`, and `127.0.0.1`.

### Key Modules

| File | Purpose |
|------|---------|
| `LabSetup/main_dashboard.py` | Main Panel dashboard, `BatteryTestDashboard` class |
| `LabSetup/profile_runner.py` | `ProfileRunner` class — custom current profile upload and execution |
| `LabSetup/Classes/functions_class.py` | `bdps_control` class — all BDPS TCP/SCPI communication |
| `LabSetup/Classes/sensor_reader.py` | `SensorReader` class — Arduino serial reader thread (20 Hz) |
| `LabSetup/detailed_battery_test_plan.csv` | 198-cycle test plan (charge/discharge/soh_check steps) |
| `Arduino_Code/sensors.ino` | Arduino firmware for the sensor board |

### Dashboard Tabs
1. **Control Tests** — start/stop charge, discharge C/5, discharge C/2, 10-cycle degradation block, full automated plan
2. **Live Sensor Data** — real-time plot of V, I, T, RH from Arduino + BDPS (auto-refreshes every 2 s via `pn.state.add_periodic_callback`)
3. **History** — browse and plot any saved CSV from the log directory
4. **Custom Profile** — upload a `Time (s), Voltage (V), Current (A)` CSV and replay it on the BDPS at 10 Hz

### Threading Model
- `SensorReader` runs in a daemon thread at 20 Hz, appending to the active log CSV
- All test operations (`_run_*` methods) run in daemon threads so the Panel event loop stays responsive
- Stop coordination via `threading.Event` (`self.stop_event`), checked in every inner loop
- The full automated plan (`_run_full_plan`) blocks its thread sequentially: 10 degradation cycles → C/5 SOH discharge → custom test-day profile

## Data / Logging

### Log Location
**`/media/pi/LOGBATTEST`** — USB drive mounted at this path. The directory is created with `os.makedirs(..., exist_ok=True)` on startup but the USB must be physically present.

### Log File Naming Convention
```
Block_<block>_Degr_<cycle>_discharge_sensor_<timestamp>.csv   # Arduino sensor during degradation discharge
Block_<block>_Degr_<cycle>_discharge_bdps_<timestamp>.csv     # BDPS during degradation discharge
Block_<block>_Degr_<cycle>_charge_sensor_<timestamp>.csv
Block_<block>_Degr_<cycle>_charge_bdps_<timestamp>.csv
Block_<block>_SOH_C5_sensor_<timestamp>.csv
Block_<block>_SOH_C5_bdps_<timestamp>.csv
Block_<block>_TestDay_bdps_<timestamp>.csv
DegradationCycle_<cycle>_discharge_sensor_<timestamp>.csv     # standalone degradation run
DegradationCycle_<cycle>_charge_sensor_<timestamp>.csv
discharge_c5_<timestamp>_bdps.csv / _sensor.csv
discharge_c2_<timestamp>_bdps.csv / _sensor.csv
full_charge_<timestamp>_bdps.csv / _sensor.csv
testday_run_<timestamp>_bdps.csv
```

### CSV Formats

**Sensor log** (Arduino, 20 Hz):
```
timestamp,voltage,current,temperature,humidity
2024-01-01T12:00:00.000,12.34,3.56,25.1,48.2
```

**BDPS log** (1 Hz):
```
Timestamp,Elapsed_s,Voltage,Current,Mode
2024-01-01T12:00:00,0.0,12.34,-3.60,CC
```
Mode is `CC` or `CV`.

## Test Plan Structure

The `detailed_battery_test_plan.csv` defines 198 named cycles:
- **Cycles 1–10, 12–21, …**: discharge at 28 A to 10.8 V (20 min), then CC/CV charge to 14.4 V @ 5.4 A (3 h)
- **Cycles 11, 22, 33, …** (every 11th): SOH check — full discharge at 1.8 A (C/10) to 10.8 V

The **full automated plan** (`_run_full_plan`, 20 blocks × 10 degradation cycles = 200 cycles total) has a different structure per block:
1. 10× degradation cycles: discharge at **9.0 A** (C/2) to 10.8 V + CC/CV charge at 14.4 V / 5.4 A / 3 h
2. SOH discharge: **3.6 A** (C/5) to 10.8 V
3. Custom test-day profile (must be pre-loaded in the Custom Profile tab)

## Custom Profile Format

Upload a CSV to the Custom Profile tab with exactly these column names:
```
Time (s),Voltage (V),Current (A)
0.0,12.0,-5.0
0.1,12.0,-5.0
...
```
- Negative current = discharge (sink); positive = charge (source)
- Currents with |I| < 0.2 A are treated as 0 A (output off)
- Max current is capped at BDPS `MAX_CUR` (90 A)
- Executed at 10 Hz sample rate

## Important Configuration Constants

| Constant | Value | Location |
|----------|-------|----------|
| `bdps_ip` | `192.168.2.130` | `main_dashboard.py:26` |
| `LOG_ROOT` | `/media/pi/LOGBATTEST` | `main_dashboard.py:22` |
| `sensor port` | `/dev/ttyACM0` | `main_dashboard.py:37` |
| `dashboard port` | `39517` | `main_dashboard.py:1213` |
| `simulate` | `False` (production) | `main_dashboard.py:1212` |
| Battery charge voltage | 14.4 V (cycle use) | `functions_class.py` |
| Battery cutoff voltage | 10.8 V (1.8 V/cell) | `functions_class.py` |
| C/5 current | 3.6 A | `main_dashboard.py` |
| C/2 current | 9.0 A | `main_dashboard.py` |
| Degradation discharge | 9.0 A (C/2) or 13.5 A (C/1.5) | `main_dashboard.py` |

## Simulation Mode

Pass `simulate=True` to `BatteryTestDashboard` to run without hardware. The `SensorReader` will generate synthetic V/I/T/RH data and the BDPS will be `None` (all BDPS buttons will be disabled or log warnings).

## Known Issues / Quirks

- **Reopening the browser tab breaks the dashboard.** `pn.state.add_periodic_callback` is scoped to the Panel WebSocket session. When the tab is closed the session dies and the 2-second sensor refresh callback dies with it. The guard `if not hasattr(self, "_sensor_callback")` in `panel()` then prevents the callback from being re-registered on reconnect, so the live plot freezes permanently. The background test threads and sensor reader keep running correctly — only the frontend stops updating. This must be solved in the future persistent setup.
- The `SOUR:POW:` command for positive power has a trailing space in the SCPI string (`"SOUR:POW: {pos_power}"`) — this matches what the SM500 firmware expects.
- `discharge_cc_until_voltage` sets the BDPS voltage to `target_voltage` as a lower limit for the sink; this is the SM500's sink cutoff behaviour.
- `charge_battery_cc_cv` uses `SOUR:FUNC VOLT` mode (constant voltage with current limit), not a true separate CC phase — the PSU itself handles the CC→CV transition.
- In the profile runner, positive current (charging) requires switching to `SOUR:FUNC VOLT` implicitly by setting voltage to 15.0 V; the explicit mode switch is commented out — this may need revisiting for bi-directional profiles.
- The `live_sensor_output.csv` file lives in `LabSetup/` (relative path) during idle periods between tests.

---

## LabSetupV2 — Persistent Dashboard (Production)

`LabSetupV2/` is the current production setup. It supersedes `LabSetup/` and fixes all known issues. Run from the `LabSetupV2/` directory via tmux over SSH so it survives closing the laptop:

```bash
tmux new-session -s battery
cd /home/pi/PhD_PiCode/LabSetupV2
/home/pi/battery-test-env/bin/python3 main_dashboard.py
# detach: tmux detach -s battery   (or close the VS Code terminal tab)
# reattach: tmux attach -t battery
```

Open in browser: `http://100.115.72.118:39517` (Tailscale IP).

### Architecture — Why LabSetupV2 is Different

The original LabSetup used a single `BatteryTestDashboard` class instance shared across all Panel sessions. Closing the browser tab killed the `pn.state.add_periodic_callback` for that session, and a `hasattr` guard in `panel()` prevented it re-registering on reconnect, so the live plot froze permanently.

LabSetupV2 solves this with two architectural choices:

1. **Singleton backend** (`backend.py`) — all hardware state, test threads, and a 2000-line ring-buffer console log live in `BatteryBackend`. It is instantiated once at server startup and never destroyed.
2. **Factory function** — `pn.serve(create_dashboard, ...)` receives a *function*, not an object. Panel calls `create_dashboard()` fresh for every new browser connection. Each session gets its own widgets and registers its own `pn.state.add_periodic_callback` unconditionally (no `hasattr` guard). The callback reads from the shared backend singleton, so all sessions see the same live state. Closing and reopening the tab just calls `create_dashboard()` again — the test keeps running.

### Key Modules (LabSetupV2)

| File | Purpose |
|------|---------|
| `main_dashboard.py` | Panel UI factory function; `FastListTemplate` dark theme |
| `backend.py` | `BatteryBackend` singleton — all test logic and hardware state |
| `profile_runner_v2.py` | Profile preview tab UI (no logic) |
| `Classes/functions_class.py` | `bdps_control` — BDPS TCP/SCPI (extended from LabSetup) |
| `Classes/sensor_reader.py` | `SensorReader` — Arduino serial thread (extended from LabSetup) |
| `test_day_profile_new.csv` | Pre-loaded realistic test-day profile v2 (41304 points, 2065 s, 20 Hz; `test_day_profile.csv` kept as the old 10 Hz version for reference) |
| `battery-dashboard.service` | systemd unit for auto-start (alternative to tmux) |

### Dashboard Layout

`FastListTemplate` dark theme (Panel 1.7). Accent colour: `#2ea043` (dark green).

- **Sidebar** (300 px): System Status card (test state, last measured capacity, sensor mode) + Controls card (Stop / Full Charge / C5 / C2 / Degradation cycles / SoC Sweep / Full Plan) 
- **Main — Live Data tab**: active sensor & BDPS log file paths, live 8-subplot Plotly figure (sensor V/I/T/RH left, BDPS V/I/Mode right), refreshed every 2 s
- **Main — History tab**: browse and plot any CSV from `/media/pi/LOGBATTEST`
- **Main — Profile tab**: profile preview, reload button, single-run button
- **Main — Console Log card** (below tabs, full width): persistent ring-buffer of all backend log lines, pre-loaded on reconnect so full history is always visible

### Logging Rates

| Phase | Sensor (Arduino) | BDPS |
|-------|-----------------|------|
| Charge / discharge / degradation | 20 Hz | 1 Hz (with fsync, in `functions_class.py`) |
| Test-day profile execution | 20 Hz | **20 Hz** (no fsync; `_run_profile_sync` only) |
| Step-down / OCV rest | 20 Hz | 1 Hz (with fsync) |

### Test-Day Profile Format (v2)

`test_day_profile_new.csv` columns: `Time (s), V_expected_sim (V), Current (A), Event Type, Event Index`.
Only `Time (s)` and `Current (A)` are replay inputs — `V_expected_sim (V)` is the lab-PC's
cosmetic simulation preview (never a setpoint or measurement) and is ignored for control,
though `_load_profile` still parses it into `Voltage_V` for the preview plot. `Event Type`
/ `Event Index` are optional but, when present, are logged verbatim alongside measured V/I
on every profile-run BDPS log row, plus a `Profile_Hash` column (sha256 of the CSV file,
computed once at load time in `backend.py`'s `_load_profile`) so any run log can be traced
back to the exact profile file that produced it. `_run_profile_sync` derives its sample
interval from the profile's own median `dt` rather than a hardcoded rate, and only
reconfigures `setVoltage`/`outputOn`/`outputOff` on sign changes (discharge ↔ charge ↔ rest)
— not on every row of a fast decay — so a 20 Hz crank-pulse inrush spike (multiple distinct
current values within ~0.3–1.5 s) is replayed at full resolution without redundant SCPI
chatter on every sample.

See `LabSetupV2/ANALYSIS_HANDOFF.md` for the feature-extraction handoff:
log schemas, event-by-event feature suggestions, the battery-ID convention,
and how per-run (OCV label) vs. per-block (measured C/5 Ah) ground truth
differ.

The sensor reader previously ran at ~10 Hz on real hardware because `readline()` (blocking ~50 ms at the Arduino's rate) was followed by an additional `time.sleep(0.05)`. Fixed by moving the sleep inside the `if self.simulate` branch only.

**Important:** `_run_profile_sync` sets `setPowerLimits(pos_power=1000.0, neg_power=1000.0)` and derives `setCurrentLimits` from the actual profile range (separate pos/neg max). An earlier version used `pos_power=0.0` and `pos_current=0.0`, which silently blocked all charge phases — the 2 A and 3 A charging blocks in the test-day profile never flowed, leaving only switching artefacts in the log.

The BDPS profile log runs at 20 Hz by removing `os.fsync()` from `_run_profile_sync`. `fsync` on the Pi SD card takes 10–50 ms per call, capping the loop at ~10 Hz. For other operations, `fsync` is kept in `functions_class.py` (1 Hz) since data integrity matters more than rate for long charge/discharge phases.

### Live Sensor File Rotation

`live_sensor_output.csv` is written continuously at 20 Hz and grows indefinitely. `SensorReader` auto-rotates it: when row count reaches `_ROTATE_AT = 12000` (~10 min of data), it rewrites the file keeping only the last `_KEEP_ROWS = 2000` rows. The row counter resets whenever `change_log_file()` is called (i.e., at the start of each test phase).

### SoC Sweep — OCV-Based Step-Down (Current Method)

**Rationale:** The real-world use case estimates SoC from OCV read off the CAN bus when the vehicle is at rest. True SoC (Coulomb counting) and true SOH (Ah capacity) are not known. Using a fixed Ah step-down (e.g. 1.8 Ah = 10% of nominal 18 Ah) produces meaningless SoC labels as the battery degrades — on a battery with only 8 Ah real capacity, 1.8 Ah is 22.5%, not 10%. Labelling by OCV matches the field observable and does not require SOH knowledge.

**Sweep sequence:**
1. Charge to 100% (CC/CV 14.4 V / 5.4 A, max 4 h)
2. Rest `OCV_REST_S = 120` s; read initial OCV; log it
3. Run test-day profile — log file named by starting OCV (e.g. `OCV12p68V_testday_bdps_...csv`)
4. For each subsequent target in `OCV_SOC_TABLE`:
   - Per iteration: disable output → rest `OCV_REST_S` s → read OCV → if OCV ≤ target stop; else discharge `OCV_PULSE_S` s at 9.0 A (C/2)
   - Rest-first order avoids unnecessary discharge if the battery already reached target after the preceding profile run
   - Key messages (OCV readings, discharge start/stop) routed through `log_callback` so they appear in the dashboard console
   - Stop loop when OCV ≤ target or terminal voltage hits 10.8 V cutoff mid-pulse
   - If OCV ≤ 11.1 V (≈ cutoff + 0.3 V margin): abort remaining steps
   - Run profile at achieved OCV

**OCV–SoC reference table** (Ultracell UL18-12, 12 V VRLA at 25 °C, resting terminal voltage):

| Nominal SoC | Target OCV (V) |
|:-----------:|:--------------:|
| 100% | 12.70 |
|  90% | 12.58 |
|  80% | 12.46 |
|  70% | 12.36 |
|  60% | 12.28 |
|  50% | 12.20 |
|  40% | 12.12 |
|  30% | 12.00 |
|  20% | 11.80 |
|  10% | 11.58 |

Values derived from standard 12 V VRLA (6-cell) electrochemistry at 25 °C, consistent with the datasheet's 1.80 V/cell (10.80 V) end-of-discharge reference and 14.4 V cycle-use charge voltage (2.40 V/cell fully charged). Datasheet (Ultracell UL18-12) does not include an explicit OCV-SoC table; these values match the discharge characteristic graphs.

### New BDPS Method: `discharge_cc_to_ocv_target`

Added to `LabSetupV2/Classes/functions_class.py` (existing methods untouched):

```python
def discharge_cc_to_ocv_target(self, current, target_ocv, cutoff_voltage=10.8,
                                rest_s=120, pulse_s=300,
                                log_path="stepdown_log.csv", stop_event=None,
                                log_callback=print)
    -> (total_ah: float, final_ocv: float)
```

Discharges at CC in `pulse_s`-second pulses using a **rest-first** loop: output off → rest `rest_s` s → read OCV → if OCV ≤ `target_ocv` stop; else discharge `pulse_s` s → repeat. This ensures no discharge occurs if the battery is already at or below target (common after a heavy profile run). `log_callback` (default `print`) receives key status messages; pass `self.log` from the backend to route them into the dashboard console. Mode column in the log CSV is `"CC"` during pulses and `"OCV"` for resting measurement rows.

### Log File Naming (LabSetupV2)

Every log file lives under a **per-battery subfolder**,
`LOG_ROOT/<battery_id>/...` (`_path()` in `backend.py` creates it on first
use). `battery_id` comes from the `BATTERY_ID` constant in `backend.py` —
**update it before swapping in a different physical battery**, or that
battery's runs will land in the wrong folder. Every BDPS log row also
carries a `Battery_ID` column as a second, row-level safeguard.

```
LOGBATTEST/
  old_ul18_12/                                  # everything for this battery_id
    SoCsweep_sweep_charge_bdps_<ts>.csv          # standalone SoC sweep's own charge-to-100%
    SoCsweep_sweep_charge_sensor_<ts>.csv
    SoCsweep_OCV<v>_testday_bdps_<ts>.csv        # e.g. OCV12p68V
    SoCsweep_OCV<v>_testday_sensor_<ts>.csv
    SoCsweep_OCV<v>_stepdown_bdps_<ts>.csv       # CC pulses + OCV rows
    SoCsweep_OCV<v>_stepdown_sensor_<ts>.csv

    # Full plan (same as above but prefixed Block_<nn>_ instead of SoCsweep_)
    Block_<nn>_charge_full_bdps_<ts>.csv         # once per block, before the SOH test
    Block_<nn>_SOH_C5_bdps_<ts>.csv              # the real Ah ground truth for that block
    Block_<nn>_sweep_charge_bdps_<ts>.csv        # SoC sweep's own recharge, after SOH test drained it
    Block_<nn>_OCV<v>_testday_bdps_<ts>.csv
    Block_<nn>_OCV<v>_stepdown_bdps_<ts>.csv
    Block_<nn>_Degr_<cycle>_discharge_bdps_<ts>.csv   # 10x per block
    Block_<nn>_Degr_<cycle>_charge_bdps_<ts>.csv

    # Standalone operations
    discharge_c5_<ts>_bdps.csv / _sensor.csv
    discharge_c2_<ts>_bdps.csv / _sensor.csv
    full_charge_<ts>_bdps.csv / _sensor.csv
    profile_single_<ts>_bdps.csv / _sensor.csv
  ul18_12_unit2/                                 # next battery's own subfolder
    ...
```

**Every individual test gets its own timestamp**, generated right before
that test's files are created — not one timestamp shared across an entire
block or sweep. A degradation block's 10 discharge/charge pairs, and a
SoC sweep's charge + 10 test-day runs + up to 9 step-downs, each get a
fresh `_ts()` call, so the filename tells you when *that specific test*
started, not just when its parent block/sweep began (which could be many
hours earlier for a later step in a long sweep).

Timestamp format: `YYYY-MM-DD_HH-MM-SS`

### Full Plan Order (LabSetupV2, `_run_full_plan` in `backend.py`)

Per block: **full charge → SOH C/5 discharge → SoC sweep → 10 degradation
cycles**, repeated for `FULL_PLAN_BLOCKS` (20) blocks = `TOTAL_DEGRADATION_CYCLES`
(200) degradation cycles total, numbered globally across blocks (e.g.
"cycle 47/200") in the console log. The full charge before SOH C/5 exists
so the capacity measurement always starts from a true 100% charge — relying
on the previous block's last degradation charge alone isn't guaranteed to
be a full top-off.

**On any unexpected failure** (exception in any stage), the full plan halts
immediately — it does not skip ahead or retry — and logs a highly visible
`FULL PLAN HALTED` banner naming the exact block and stage, so a human can
tell Claude Code (or just look at the log) where to resume. A user-initiated
Stop (the dashboard's Stop button) is logged as a plain "stop requested"
message instead — no alarming banner — since that's an intentional halt, not
a failure. `functions_class.py`'s charge/discharge/step-down methods wrap
their measurement loop in `try/finally` so `outputOff()` always fires even
if an exception (e.g. a socket error) propagates out of the loop — a halted
plan always leaves the BDPS output off.

### Full Plan Checkpoint / Resume

Every stage transition writes `LOG_ROOT/<battery_id>/_checkpoint.json`
(`{"block_index", "stage", "cycle", "updated_at"}`, `stage` is one of
`full_charge`/`soh_c5`/`soc_sweep`/`degradation_cycles`). Clicking **Full
Plan** always checks this file first: if present, it resumes from exactly
that block/stage/cycle instead of restarting at Block 1 — earlier stages in
the resume block are skipped, degradation cycles resume mid-block at the
saved cycle number. The checkpoint is deleted automatically when a full plan
completes all blocks cleanly. To force a restart from Block 1 (e.g. for a
genuinely new run), delete that battery's `_checkpoint.json`.

**Important**: the checkpoint is written at the *start* of a stage/cycle,
not on completion — so on resume, that stage/cycle is always redone in full
from its beginning (e.g. a degradation cycle resumes at its discharge phase,
even if only its charge phase was interrupted). This is deliberate: there's
no way to know how far into a stage a crash occurred, so redoing the whole
stage is the safe default. When manually resuming from an old handoff note
instead of the auto-saved checkpoint, always verify the stated "last
complete" cycle actually reached its natural stop condition (e.g. a charge's
last logged `Current` below the ~0.3 A CV-taper cutoff) before trusting it —
a cycle can look present in the file listing while its charge phase was
actually truncated mid-taper by a crash.

### ⚠ Known Pi-side memory leak (fixed 2026-07-16)

`SensorReader.data_log` (`Classes/sensor_reader.py`) was a plain `list` that
had one dict appended to it per sensor sample (20 Hz), for the entire
lifetime of the process, with nothing ever trimming it — and `get_latest()`,
its only consumer, is never actually called anywhere in `backend.py` or
`main_dashboard.py` (only in the file's own standalone `__main__` test
block). Over ~12 days of continuous running (2026-07-03 to 2026-07-15) this
grew to ~3.3 GB resident memory on a 3.7 GB Pi and got OOM-killed by the
kernel (`dmesg`: `Out of memory: Killed process ... (python3)`), silently
stalling whatever full-plan run was in progress with no crash banner (the
process was gone, not the Python code halting cleanly). Fixed by making
`data_log` a `deque(maxlen=200)`. If memory growth is ever suspected again,
`ps aux --sort=-%mem` and `dmesg -T | grep -i oom` are the fastest way to
confirm it — a gradually-worsening BDPS logging interval (rather than a
sudden step change) is consistent with memory pressure/swapping building up
before an eventual OOM kill, not a one-off misconfiguration.

### Configuration Constants (LabSetupV2 `backend.py`)

| Constant | Value | Meaning |
|----------|-------|---------|
| `LOG_ROOT` | `/media/pi/LOGBATTEST` | USB drive log directory |
| `BATTERY_ID` | `"old_ul18_12"` | Active battery's log subfolder + `Battery_ID` column value — update before swapping batteries |
| `PROFILE_FILE` | `LabSetupV2/test_day_profile_new.csv` | Auto-loaded at startup |
| `SOC_SWEEP_POINTS` | `10` | Profile runs per sweep |
| `SOC_STEP_CURRENT` | `9.0 A` | C/2 — step-down discharge current |
| `OCV_REST_S` | `120 s` | Rest time before each OCV reading |
| `OCV_PULSE_S` | `300 s` | Discharge pulse duration between OCV checks |
| `FULL_PLAN_BLOCKS` | `20` | Blocks in a full plan |
| `CYCLES_PER_BLOCK` | `10` | Degradation cycles per block |
| `TOTAL_DEGRADATION_CYCLES` | `200` | `FULL_PLAN_BLOCKS x CYCLES_PER_BLOCK` |

### Battery Degradation Observation

During testing, the Ultracell UL18-12 (nominally 18 Ah) showed an actual delivered capacity of **~8 Ah** on a full C/5 discharge to 10.8 V. From the 4th SoC step-down onward (70% → 60%), the battery hit the 10.8 V cutoff voltage almost immediately, confirming the battery is heavily degraded. The OCV-based sweep method handles this gracefully: it attempts each step-down, reads the actual achieved OCV, and aborts the sweep if OCV is at or near the cutoff rather than running subsequent profiles on an exhausted battery.

### WebSocket Origins

`main_dashboard.py` must list every hostname/IP that will open the dashboard:
```python
websocket_origin=[
    "localhost:39517",
    "127.0.0.1:39517",
    "batterytestpi:39517",
    "100.115.72.118:39517",   # Tailscale IP — update if it changes
]
```
Adding `"*"` to a list does **not** act as a wildcard in Bokeh/Panel — list only explicit entries.
