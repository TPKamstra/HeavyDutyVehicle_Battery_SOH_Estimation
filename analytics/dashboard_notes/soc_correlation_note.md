**Why this tab exists.** The *SoC & Temp Effects* tab plots all 4 799 starts as
raw points coloured by a continuous temperature scale. That encoding fails on
this dataset: there are only ~204 distinct SoC values, so the markers stack into
a solid block, and a continuous colour ramp spread over thousands of overlapping
points cannot be read at all. Any trend that is present is hidden by the
overplotting rather than shown by it.

This tab replaces the point cloud with three encodings that survive it:

1. **Density (2-D histogram).** Where the starts actually are — one sequential
   hue, light → dark, so cell darkness means count and nothing else.
2. **Binned median + IQR band.** The trend itself, robust to both the
   overplotting and the R_int outliers. Bands are the 25–75 % spread within each
   5 %-SoC bin; the marker is the median, not the mean.
3. **Split by temperature tercile.** Three equal-count temperature bands as
   three separate trend lines, instead of a per-point colour. This is what
   answers "is the SoC relationship just temperature in disguise?" — if the
   three lines sit on top of each other, temperature is not driving it.

**Reading the correlation table.** *Spearman ρ* is the headline (monotone,
outlier-robust). *ρ | control* is the partial correlation with battery
temperature held constant. **`Var. explained (ρ²)` is the honest number**: at
this n every ρ above ~0.03 is "statistically significant", so significance is
not evidence of a usable relationship — ρ² is.

⚠️ **`Pre-crank voltage` vs SoC is circular.** `soc_pct_est_pack` is itself
derived from open-circuit voltage upstream of this repo (Spearman ρ = 0.97
against pre-crank voltage confirms it). Its near-perfect correlation, and its
η² = 0.47 in the SoC-binned ANOVA, are definitional — not a physical finding.
Every other feature in the table is a genuine measurement against SoC.

`R_int` rows always exclude `invalid_R_int` starts, same rule as every other tab.
