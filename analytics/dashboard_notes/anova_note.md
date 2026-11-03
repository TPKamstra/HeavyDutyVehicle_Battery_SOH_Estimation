**One-way ANOVA across a grouping factor.** Pick a factor and read the table
top-down — it is sorted by **η² (effect size), not by p**. At n ≈ 4 800 starts
almost anything reaches p < 0.001, so p alone tells you nothing useful here;
η² / ω² (share of feature variance the factor explains) is the column that
answers "does this actually matter?".

Three tests are reported side by side because their assumptions differ:

| Test | Assumes | Use it when |
|---|---|---|
| **Classic F** (`f_oneway`) | equal variances, roughly normal | the textbook default |
| **Welch F** | *not* equal variances | Levene p < 0.05 — which is nearly always, here |
| **Kruskal–Wallis** | neither normality nor equal variance | distributions are skewed / outlier-heavy |

**Levene p < 0.05 means the classic F row is the unreliable one** — trust Welch
and Kruskal–Wallis. `Crank duration (s)` across CAN tappers is the live example:
classic F says p = 0.056 ("no effect"), Welch says p = 7 × 10⁻⁹. The per-tapper
group sizes range from 21 to 758 starts, which is exactly the unbalanced +
heteroscedastic case classic F handles worst.

The **post-hoc matrix** is Games–Howell (Welch-corrected pairwise t against a
studentized-range critical value), chosen over Tukey HSD for the same reason.
Only pairs at p < 0.05 are coloured.

`R_int_mohm` statistics always exclude `invalid_R_int` starts — same rule as
every other tab. Levels with fewer than 5 usable starts are dropped rather than
destabilising the variance estimates.

> **`start_color` is a null control, not a measurement.** It is the upstream
> export's plotting palette, assigned as `start_index % 9` (verified: every tap
> cycles the same 9 ColorBrewer colours in start-index order). It carries no
> physical information, so a correct analysis must find *nothing* there — every
> feature should come back p ≫ 0.05 with η² ≈ 0. It is offered as a factor
> precisely so the ANOVA machinery can be seen failing to find an effect that
> does not exist.
