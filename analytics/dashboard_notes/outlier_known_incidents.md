### Known data-quality incidents (found while reviewing this tab)

**`can_tapper31` — current-sensing dropout, 2026-06-11 19:38 to 2026-06-12 21:52
(start_index 388-404, 17 starts, ~26h window).** `I_load_A` collapses to single digits (-7 to 9 A)
instead of the normal ~400 A crank current seen immediately before (start 387: 500 A) and after
(start 405: 444 A) this window. `R_int_mohm` is computed as `ΔV / I_load`, so this tiny denominator
produces meaningless values (up to 3200 mΩ) — **all 11 of the whole dataset's `R_int_mohm > 50 mΩ`
outliers are from this one window on this one tap.** Voltage sample counts in the same window look
normal (2158-2160 samples/start, matching elsewhere), so this looks like a temporary fault specific
to the current channel rather than a general logging dropout. Root cause not confirmed from the
analysis side.

**`can_tapper33` — severe voltage-drop cluster, 2025-12-16 11:13-11:58
(start_index 20-23, 4 starts, ~45 min window).** Pack-level voltage drop 44.6-54.9% (vs. a
dataset-wide median of ~9%), during cold ambient temperature (4.3-6.5°C) with a *normal* crank
current (380-500 A) — unlike the tapper31 incident, this looks like a real electrical event (all
four pack groups sag together, not just one channel), consistent with a cold, weak battery under
load rather than a sensor artifact. One point within it (start_index 21, group `PG1_est`) sags far
more than its own siblings in the same start (83% vs. 44-46% for PG2/PG3_est/PG4) — `PG1_est` has
no direct sensor (estimated from the pack-level signal, see the per-group note on the Feature Trends
tab), so that specific extreme point should be treated with more suspicion than the other three.

The controls below re-derive outlier flags live from the current data (not a hardcoded list of the
starts above) — use them to check whether these incidents are still present after a re-export, or to
look for new ones.
