# HeavyDutyVehicle_Battery_SOH_Estimation

PhD project: SOH (state-of-health) estimation for heavy-duty vehicle starter
batteries, using a lab test bench to develop candidate features and field data
from operational vehicles to check whether those features transfer.

## Repo structure

| Path | What it is |
|---|---|
| `main.tex`, `references.bib`, `zotero-library.bib` | The paper (Overleaf-synced — pulling from this repo, don't hand-edit conflicting sections without checking git history for Overleaf commits first). |
| `lab-control/` | Pi-side code that actually runs the lab test bench (`backend.py`, `Classes/`, Arduino sensor code). Has its own `CLAUDE.md`. |
| `analytics/` | Dashboards + analysis code that turn the lab/field logs into features. Has its own `CLAUDE.md` with full technical detail (event schema, known data-quality incidents, per-battery specifics). |
| `analytics/exports/*_summary_export/` | **Small, static, git-tracked result bundles** — CSV/JSON summary stats + PNG charts, safe to reference directly when writing the paper. No raw dataset in git (gitignored — see `analytics/CLAUDE.md` and root `.gitignore`). |

## For filling in the paper's Results section (`\label{sec:results}`)

**Start with `analytics/exports/RESULTS_SUMMARY_FOR_PAPER.md`** — a guided
tour of every export with headline numbers ready to cite, an explanation of
an impedance/R_int outlier-filtering pass (2026-08-06: DCIR/R_int values were
blowing up on near-zero-current pulses in both lab datasets — now flagged,
not silently dropped — plus one feature, `wakeup_load_2_R_int_mohm`, found to
be unusable as currently computed), and a concrete breakdown of what's needed
to compute the full lab feature set on field data (short version: a measured
current sensor on the field battery, and an event-segmentation pipeline to
replace the lab's explicit scripted-event log — neither exists yet).

As of the last main.tex sync, Results/Discussion/Conclusion are `\todo{}`
placeholders. The suggested structure (5.1 lab, 5.2 field, 5.3 lab-vs-field
comparison) maps directly onto what's already in `analytics/exports/`:

**5.1 Laboratory results** — two *separate* battery datasets exist; the paper
currently has an open `\todo` in §Battery Under Test asking whether to use
one, the other, or both, pooled or separate. **Answer: never pool them** —
different starting condition, different degradation state, tracked under
distinct `Battery_ID`s throughout the lab code for exactly this reason.
Report them separately (or pick one as primary and the other as a
consistency check):
- `analytics/exports/old_ul18_12_summary_export/` — the already-aged unit
  (retired, delivered only ~8 Ah of 18 Ah nameplate at the *start* of
  logging). `summary_stats.csv` + `NOTES.md` (data-quality caveats: a
  partial/early logging-schema rollout, an anomalous >100% SOH reading worth
  mentioning as a limitation) + 3 representative charts.
- `analytics/exports/ul18_12_unit2_summary_export/` — the fresh unit, full
  degradation history captured from new. Its 20-block campaign **completed**
  2026-08-02: measured C/5 capacity declined **18.16 → 0.94 Ah**. Includes
  `soh_predictors_this_next_block.csv` and `soc_robust_soh_indicators.csv` —
  i.e. *which candidate features actually predict SOH ahead of time, net of
  SoC-dependence* — this is probably the single most citable table for §5.1's
  feature-behavior-vs-SoC-and-vs-reference-SOH discussion. Read its
  `NOTES.md` first — three real data-quality incidents occurred during this
  campaign (a Pi logging-rate regression, a false-alarm "aborted" test, two
  since-fixed analysis bugs) and are documented there; the exported numbers
  already have all three corrected, but the paper's limitations discussion
  may want to mention the regression happened at all.

**5.2 Field results** — `analytics/exports/boxer_summary_export/`. This is
very likely what §Field Data Acquisition is already describing (EnerSys
ArmaSafe Plus 12FV120, 24 V 2S2P pack, 41 real engine-start events, per-start
features: pre-crank voltage, V_min, %drop, SoC estimate, temperature,
R_int, recovery time, cross-group voltage imbalance) — the feature list in
main.tex's §Field Data Acquisition paragraph matches this dataset's columns
almost one-to-one. **Two things worth resolving before citing it**:
1. main.tex has an open `\todo` asking whether this field data is read
   directly off CAN bus or from separate DAQ hardware — not something the
   analytics code can answer definitively; check `boxer_battery_dashboard.py`'s
   column names (`FM-Total Voltage`, `FM-Center Voltage PG2`, etc. — "FM"
   likely a fleet-management/telematics system) or ask whoever instrumented
   the vehicles.
2. **Not yet flagged as a `\todo` in the paper, but worth adding one**: the
   field battery (EnerSys ArmaSafe 12FV120, 24 V) is a *different make/model*
   from the lab battery (Ultracell UL18-12, 12 V). §5.3's "lab-vs-field
   transferability" claim should be explicit that this is cross-model
   transfer, not same-battery validation — affects how strong a claim the
   paper can honestly make.
3. Also note: `PG3_est`'s R_int values are byte-for-byte identical to
   `PG4`'s across all 41 starts in the raw data, while `PG1_est` is *not*
   identical to `PG2` — an asymmetry worth a sentence in a limitations
   paragraph about the estimated (non-directly-sensed) pack groups, since it
   suggests one estimation channel may not be independent of its measured
   counterpart. Documented in `boxer_summary_export/NOTES.md`.

**5.3 Lab-vs-field comparison** — no CAN-bus dataset from the actual
target vehicle fleet has been delivered to the analytics side yet (as
distinct from the boxer field dataset above, which is already-processed
per-start features, not raw CAN logs). If/when raw CAN data arrives, it needs
a new feature-extraction module (event boundaries would have to be inferred
from RPM/ignition rather than an explicit script, and R_int-style features
need a current signal, which isn't guaranteed on stock CAN) — this is a
distinct, larger piece of work from what's in `analytics/exports/` today.

## Regenerating any export

Each `analytics/exports/*_summary_export/` folder was produced by the
correspondingly-named `analytics/export_*_summary.py` script. **Must be run
with the `ddsm` conda environment**, not `base` — chart image export
(`pio.write_image` via kaleido) hangs indefinitely on `base`'s package
versions here; see `boxer_summary_export/NOTES.md` for the full diagnostic
story if this regresses:
```
C:\Users\TPKam\miniconda3\envs\ddsm\python.exe analytics\export_boxer_summary.py
```
The `old_ul18_12`/`ul18_12_unit2` scripts additionally need the sibling
`BatPi_Download` working copy present (that's where the real, gitignored
dataset lives) — see the docstring at the top of each script.
