# Boxer summary export — notes

## Chart images: FIXED, now included — the full story (2026-08-05)

`export_boxer_summary.py`'s figure exports (`01_start_profile`, `02_feature_trends`,
`03_group_comparison`, `04_soc_temp_effects`) initially could **not** be generated.

**Why:** `plotly.io.write_image()` needs a working headless Chrome, launched via the
bundled `kaleido` package. In this environment, that launch never completes —
confirmed for both PNG and SVG (same hang either way, since the format doesn't
matter until *after* Chrome starts).

**Updated diagnosis (2026-08-05, after a full machine reboot):** the first theory
(missing `mf.dll` / Windows Media Foundation) turned out to be wrong, or at least
not the real blocker. After reboot, `mf.dll`/`mfplat.dll` are confirmed present on
disk, and the `DXVAVDA … Could not load mf.dll` line disappeared from
`kaleido`'s debug log entirely — but the hang still happens, identically. What's
left in the log is just:

```
[headless_browser_main_parts.cc(83)] Cannot create Pref Service with no user data dir.
```

Also re-tried and ruled out, all with a fresh reboot and orphaned `kaleido.exe`
processes cleaned up between attempts (killing the parent Python process does
**not** kill its `kaleido.exe`/Chromium descendants — they leak and pile up
across repeated attempts, though this wasn't the root cause either): a short,
non-nested `--user-data-dir` (ruling out a Windows path-length issue),
`--no-sandbox`, `--disable-gpu`, `--single-process`. `kaleido.exe` (a bundled
Chromium build) does launch every time, spawns its usual sub-processes, and then
just sits there forever instead of completing startup or erroring.

At this point the leading theory was that the *tool* environment itself (not
Windows) was sandboxing some low-level operation headless Chrome needs — based
on a `Remove-Item` on an ordinary `C:\` path being refused with *"This path is
protected from removal,"* which isn't standard Windows/PowerShell behavior.
That theory was **wrong, or at least not the actual fix** — see below.

**Actual root cause and fix:** the real problem was a version mismatch I
introduced myself. The base conda environment had `plotly` 6.0.1; kaleido's own
warning message said to either upgrade `plotly` to ≥6.1.1 or downgrade `kaleido`
to 0.2.1 — I chose the downgrade, which meant every attempt used
`kaleido==0.2.1`'s **old, bundled-Chromium-binary architecture**. That specific
old architecture is what hangs on this machine (root cause of *that* still
unknown — a Windows/Chromium-build incompatibility, most likely). The `ddsm`
conda environment (`plotly` 6.7.0 + `kaleido` 1.3.0 — kaleido's **newer**
architecture, which doesn't rely on that bundled Chromium binary the same way)
works correctly, including run from inside this same tool environment — which
also disproves the sandbox theory above, since the identical tool successfully
launched it once the package versions were right.

**To regenerate:** run with the `ddsm` environment's interpreter, not base:
```
C:\Users\TPKam\miniconda3\envs\ddsm\python.exe export_boxer_summary.py
```
(or `conda activate ddsm` first, then `python export_boxer_summary.py`). No
code changes needed — it already calls `pio.write_image()` on the dashboard's
real figure objects.

## What *is* in this export

- `summary_stats.csv` / `.json` — flat, one row per metric (7 pack-level + 4 per-group
  R_int), reproducing `_build_summary()`'s markdown table exactly.
- `features_enriched.csv` — `FEAT` (41 engine starts) with the dashboard's derived
  `R_int_mohm` column added; not identical to the checked-in `features.csv`.
- `features_packs_enriched.csv` — `FEAT_PK` (164 rows = 41 starts × 4 pack groups)
  with derived `R_int_mohm` / `V_pre_V` / `V_min_V` added; not identical to the
  checked-in `features_packs.csv`.
- `01_start_profile.png`, `02_feature_trends.png`, `03_group_comparison.png`,
  `04_soc_temp_effects.png` — static renders of the dashboard's 4 real figures.
