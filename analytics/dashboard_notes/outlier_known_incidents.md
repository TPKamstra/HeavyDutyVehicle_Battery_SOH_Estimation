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

**`can_tapper14` — disproportionate low-current/R_int-invalid rate, unresolved (found 2026-08-20).**
136 of 758 starts (17.9%) are flagged `invalid_R_int` (`I_load_A` below 200 A or missing) — every
other tap sits between 0% and 12.5%, so this is its own issue, not just an instance of the general
low-current pattern. Checked for a clean root cause the way the tapper31 dropout had one: **not
found.** The flagged starts span ~2.5 years (2022-09-15 to 2024-10-11) across 40 different dates —
not a single contiguous outage — though two short windows are notably denser than the rest
(2023-04-18 to 2023-04-26: 4-8 flagged starts/day; 2023-07-17 to 2023-07-19: 10, 3, then 14 flagged
starts). The `R_int_negative_current` sign-flip pattern (see `invalid_R_int`'s sub-flags in
`boxer_can_features_enriched.csv`) is also concentrated on this tap (31 of 36 non-dropout-window
negative-current starts) and shows the same shape: spread across 8 months (2022-11 to 2023-07) with
a same single-day spike on 2023-07-19 (14 of 31). Whether this is one intermittent fault recurring
over years, several unrelated faults, or something about how this particular tap/vehicle is wired or
used differently from the other 14, is **not determined** — flagging as unresolved rather than
guessing.

The controls below re-derive outlier flags live from the current data (not a hardcoded list of the
starts above) — use them to check whether these incidents are still present after a re-export, or to
look for new ones.
