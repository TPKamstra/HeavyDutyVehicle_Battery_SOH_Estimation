# Handoff for Claude Code on the Pi — ul18_12_unit2 test status + logging-rate regression

Written from the Windows analysis side on 2026-07-16. Copy this to the Pi
and hand it to Claude Code there — it has terminal/filesystem access this
side doesn't.

## Where the test stands (per the last data synced to the Windows analysis side)

- **Block 5, degradation cycle 7 of 10** is the newest complete cycle
  (`Block_05_Degr_07_charge_bdps_2026-07-15_07-29-25.csv`).
- Block 5 already has: SOH C/5 measurement, all 10 SoC-sweep test-day runs,
  all 9 step-downs, and Degr cycles 1-7. Cycles 8-10 are missing.
- **No new files have appeared in the synced folder since 2026-07-15
  07:29:25 — as of this note (2026-07-16 15:05), that's ~31 hours with zero
  new data.** That's a bigger deal than the logging-rate issue below and
  should be checked first.

## Step 1 — confirm the test loop is actually still alive (do this first)

1. Is the backend.py process still running (systemd service / tmux / screen
   / plain nohup)? `ps aux | grep -i backend`, `systemctl status <name>`,
   or check for the session it normally runs in.
2. Does the **Pi's own local log folder** have files newer than
   `Block_05_Degr_07_charge_bdps_2026-07-15_07-29-25.csv`? If yes, this is
   just a sync lag on the Windows side (check the rsync/cron/transfer job),
   not a real stall — nothing to fix on the Pi itself.
3. If the Pi's local folder is **also** stuck at Degr_07 with no growth,
   the test loop has hung or crashed. Check whatever backend.py logs to
   (stdout, a log file, journal) for the last thing it was doing, then
   resume at **Block 5, Degr cycle 8** (discharge first, then charge) —
   check for a partial/in-flight cycle-8 file first so you don't duplicate
   or corrupt an in-progress write.

## Step 2 — the logging-rate regression (separate, already-confirmed issue)

USB drive space has been ruled out (confirmed not full). Starting mid-Block 4
(`Block_04_Degr_03`, 2026-07-11 21:15) the BDPS logging interval degraded
from a healthy ~1 Hz (Degr/SOH files) / ~20 Hz (testday files) down to a
median gap of tens-to-hundreds of seconds by Block 5. It got progressively
worse rather than stepping suddenly, which points more toward a resource
leak/throttle than a one-off misconfiguration. Suggested checks, roughly in
priority order:

1. **Thermal/power throttling** (Raspberry Pi): `vcgencmd get_throttled` —
   a nonzero result, especially bits for current or historical
   under-voltage/throttling, points at power or heat as the cause.
   `vcgencmd measure_temp` for current temperature.
2. **CPU/process load**: `top` / `htop` while backend.py runs — anything
   else competing for CPU (a stray process, a leaked previous test run)?
3. **USB bus/drive health** (speed, not space): `dmesg | grep -i usb` for
   reconnects, errors, or renegotiation to a slower USB speed. A raw
   write-speed check: `dd if=/dev/zero of=/media/pi/LOGBATTEST/testfile
   bs=1M count=100 oflag=direct` (delete `testfile` after) to see if writes
   have genuinely slowed at the hardware level.
4. **Memory growth in backend.py's logging loop**: `ps aux --sort=-%mem |
   head` — has its resident memory grown a lot since Block 1 started
   (~13 days ago)? A slow leak would explain a gradually-worsening (not
   sudden) slowdown.
5. **Serial/Arduino sensor link**: the sensor log (20 Hz) is a separate
   stream from the BDPS log — check whether its sample rate degraded too.
   If only BDPS degraded and the sensor log stayed fine, the bug is specific
   to the BDPS logging path in `backend.py`/`functions_class.py`, not
   general system load.

## What's already handled on the analysis side — no action needed there

- SOH C/5 and Degr **discharge** Ah values for Block 5 have already been
  corrected in the dashboard code (`duration × mean current`, valid since
  those segments are confirmed constant-current throughout) — past data
  doesn't need to be re-collected for this reason.
- Degr **charge** cycles (CC/CV — current tapers) and event-level test-day
  features (crank R_int, wakeup sag, etc.) for any coarse-sampled Block 5
  run **cannot** be corrected after the fact. If those specific
  cycles/runs matter later, they may need to be re-run once the rate issue
  is fixed, rather than trusting what's already recorded.

See `CLAUDE.md` → "⚠ Known Pi-side logging-rate regression" in the Windows
repo for the full write-up of how this was found and corrected.
