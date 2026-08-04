# Handoff for Claude Code on the Pi — ul18_12_unit2 test status

Written from the Windows analysis side. Copy this to the Pi and hand it to
Claude Code there — it has terminal/filesystem access this side doesn't.

## Status update (2026-07-17): the logging-rate regression is fixed — thank you

Whatever was done between `Block_05_Degr_07` (2026-07-15, still coarse) and
`Block_05_Degr_08` (2026-07-16 19:45, back to normal ~1 Hz) worked — sampling
has stayed healthy through Block 5 cycles 8-10 and the new Block 6 SOH C/5
file. Also noticed and appreciated: `_checkpoint.json` (resume tracking) and
`_incomplete_attempts/` (archived retry files) are new since the last check —
nice additions. Block 5 Degr cycle 7 was also re-run cleanly
(`Block_05_Degr_07_discharge_bdps_2026-07-16_15-33-00.csv` superseding the
2026-07-15 06:36 attempt) — the analysis-side code now always prefers the
newest file when duplicates like this exist, so no further action needed
there. If the root cause was ever identified (SD card / thermal / a
backend.py performance fix), it'd be useful to note it in this file for the
record, but it's not urgent.

## Non-issue, resolved: Block 6's SOH C/5 file just looked odd because it was live

For the record — the analysis side briefly flagged
`Block_06_SOH_C5_bdps_2026-07-17_08-36-04.csv` as suspicious (it read 12.07 V
after only 5625 s, while every completed block's SOH discharge ends around
10.78-10.79 V). Confirmed this was simply because the C/5 test was **still
running** on the Pi at the moment this file synced over — not an aborted or
broken test. No action needed. The analysis-side dashboard now treats any
SOH file that hasn't reached the ~11 V cutoff as "not final yet" (excluded
from trend/health numbers, shown as a distinct marker) rather than either
crashing or silently presenting a misleadingly low in-progress reading as
if it were the real answer — it'll just pick up the final number
automatically on the next rescan once the test completes.

## Where the test stands

- Block 1-5 are fully complete (10/10 Degr cycles each, valid SOH, 10
  SoC-sweep test-day runs each — Block 5's SoC-sweep test-day runs are
  still flagged unreliable in the dashboard since they were logged during
  the coarse-sampling window; consider re-running Block 5's SoC sweep if its
  crank/wakeup features matter for later analysis).
- Block 6: initial full charge done, SOH C/5 in progress as of last sync,
  no Degr cycles or SoC-sweep test-day runs yet.

See `CLAUDE.md` → "⚠ Known Pi-side logging-rate regression" and "Live-updating
SOH C/5 files" in the Windows repo for the full analysis-side write-up.
