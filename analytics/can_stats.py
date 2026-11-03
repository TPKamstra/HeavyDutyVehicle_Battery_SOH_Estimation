#!/usr/bin/env python3
"""
Statistics helpers for the Boxer CAN motor-start dataset.

Pure pandas/numpy/scipy -- no Panel, no Plotly -- so the numbers behind the
`boxer_can_dashboard.py` "Group Effects (ANOVA)" and "Feature vs SoC" tabs can
be recomputed, unit-checked, or dropped into an export script without starting
a dashboard server.

Two things this module is deliberately opinionated about, because both bite
hard on this particular dataset (n = 4799 starts):

1. **Significance is not effect size.** At n~4800 a one-way ANOVA returns
   p < 1e-40 for group differences that explain ~5% of the variance. Every
   table here reports eta^2 / omega^2 next to p, and `effect_label()` grades
   the effect size, so a tiny-but-significant result can never be read off as
   a strong one.

2. **Equal variances cannot be assumed.** Levene's test rejects homoscedasticity
   for essentially every feature x can_tapper_id combination here, and the
   per-tapper group sizes are wildly unbalanced (21 .. 758 starts). Classic
   one-way F is therefore reported *alongside* Welch's ANOVA (which does not
   assume equal variances) and Kruskal-Wallis (which assumes neither variance
   equality nor normality). Where the three disagree, Welch/KW are the ones to
   trust -- `crank_duration_s` across tappers is the live example: classic
   F gives p=0.056 ("not significant"), Welch gives p=7e-09.

Post-hoc pairwise testing uses Games-Howell for the same reason (Welch-corrected
pairwise t with a studentized-range critical value) rather than Tukey HSD.
"""
import numpy as np
import pandas as pd
from scipy import stats

# -- Feature registry ---------------------------------------------------------
# Display label -> column in the enriched per-start table. `R_int_mohm`,
# `post_start_V_std_V` and `V_pre_group_avg_V` are the derived columns
# boxer_can_dashboard._load() adds; the rest are raw enriched columns.
FEATURE_LABELS = {
    "V_drop_total_pct":           "Voltage drop during crank (%)",
    "R_int_mohm":                 "Pack internal resistance (mOhm)",
    "crank_duration_s":           "Crank duration (s)",
    "post_start_V_std_V":         "Post-start voltage std. dev. (V)",
    "max_abs_imbalance_pct_pack": "Max cross-group imbalance (%)",
    "t_recovery_s_max":           "Voltage recovery time (s)",
    "glow_lead_s":                "Glow-plug lead time (s)",
    "V_pre_group_avg_V":          "Pre-crank voltage, group avg (V)",
}

# Features whose rows must be dropped when the physically-motivated
# `invalid_R_int` flag is set (see build_boxer_can_features.add_r_int_validity_flags):
# R_int = dV / I_load is meaningless when I_load is missing/near-zero/sign-flipped,
# so those starts are excluded from R_int statistics -- never from the others.
R_INT_DEPENDENT = {"R_int_mohm"}


def clean_for(df: pd.DataFrame, feature: str) -> pd.DataFrame:
    """Rows usable for `feature`: drops NaN, and drops invalid_R_int rows for
    R_int-derived features only. Mirrors the plotting tabs' clean/flagged split
    so the tables and the charts always describe the same subset."""
    d = df
    if feature in R_INT_DEPENDENT and "invalid_R_int" in df.columns:
        d = d[~d["invalid_R_int"].astype(bool)]
    return d.dropna(subset=[feature])


# -- Grouping factors ---------------------------------------------------------

# SoC edges follow the physically meaningful 40..100% span this fleet actually
# occupies. Fixed (not quantile-derived) so the bins mean the same thing
# between refreshes and between taps. Shared by add_factor_columns() (the
# ANOVA tab's soc_bin factor) and joint_grid() (the SoC x Temp heatmap), so
# every tab that bins SoC uses the identical grid.
SOC_BIN_EDGES = [40, 55, 65, 75, 85, 95, 100]
SOC_BIN_LABELS = ["40-55%", "55-65%", "65-75%", "75-85%", "85-95%", "95-100%"]

# Temp edges bracket freezing and typical engine-bay warm. Fixed-width, used
# by the ANOVA tab's temp_bin factor. NOT used for the SoC x Temp heatmap --
# verified on this dataset that fixed 10C-wide bins leave the <0C and >30C
# rows with single-digit counts (as low as n=1), too sparse to trust a cell
# mean; joint_grid() uses equal-COUNT temperature terciles instead (see its
# own docstring).
TEMP_BIN_EDGES = [-np.inf, 0, 10, 20, 30, np.inf]
TEMP_BIN_LABELS = ["<0 C", "0-10 C", "10-20 C", "20-30 C", ">30 C"]


def add_factor_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add the binned categorical factors the ANOVA tab offers (on a copy).

    `soc_bin` / `temp_bin` turn the two continuous confounders into ANOVA
    factors -- see SOC_BIN_EDGES / TEMP_BIN_EDGES above for why each uses the
    edges it does.
    """
    d = df.copy()
    d["soc_bin"] = pd.cut(d["soc_pct_est_pack"], bins=SOC_BIN_EDGES,
                          include_lowest=True, labels=SOC_BIN_LABELS)
    d["temp_bin"] = pd.cut(d["temp_avg_start_C"], bins=TEMP_BIN_EDGES, labels=TEMP_BIN_LABELS)
    return d


# Factor column -> (display name, note shown under the table).
FACTORS = {
    "can_tapper_id": (
        "CAN tapper (vehicle)",
        "Does the *vehicle* matter? Each tapper is one instrumented vehicle, so a "
        "real effect here means starts are not exchangeable across the fleet.",
    ),
    "soc_bin": (
        "SoC at start (binned)",
        "Does state of charge shift the feature? Compare against the continuous "
        "correlation on the **Feature vs SoC** tab.",
    ),
    "temp_bin": (
        "Battery temperature (binned)",
        "Does temperature shift the feature? Temperature and SoC are themselves "
        "correlated here (Spearman rho ~ 0.18), so neither is a clean factor on its own.",
    ),
    "start_color": (
        "start_color (null control)",
        "**Deliberate null control.** `start_color` is not a measurement: it is the "
        "plotting palette from the upstream export, assigned as `start_index % 9`. "
        "A correct ANOVA must find nothing here (p >> 0.05, eta^2 ~ 0). If this factor "
        "ever comes out significant, the test -- not the battery -- is what changed.",
    ),
}


# -- Effect-size grading ------------------------------------------------------

def effect_label(eta_sq: float) -> str:
    """Cohen's conventional thresholds for eta^2: .01 small, .06 medium, .14 large.
    Reported as a word so a p=1e-45 row with eta^2=0.05 still reads 'Small'."""
    if not np.isfinite(eta_sq):
        return "-"
    if eta_sq >= 0.14:
        return "Large"
    if eta_sq >= 0.06:
        return "Medium"
    if eta_sq >= 0.01:
        return "Small"
    return "Negligible"


# -- One-way ANOVA family -----------------------------------------------------

def welch_anova(groups: list) -> tuple:
    """Welch's heteroscedasticity-robust one-way ANOVA.

    Returns (F, p, df1, df2). Unlike scipy's f_oneway this does not assume the
    groups share a variance -- required here because Levene rejects equal
    variance for nearly every feature, and group sizes range 21..758.
    """
    groups = [np.asarray(g, dtype=float) for g in groups if len(g) >= 2]
    k = len(groups)
    if k < 2:
        return float("nan"), float("nan"), 0, 0
    n = np.array([len(g) for g in groups], dtype=float)
    m = np.array([g.mean() for g in groups])
    v = np.array([g.var(ddof=1) for g in groups])
    if np.any(v <= 0):  # a zero-variance group makes the weights blow up
        return float("nan"), float("nan"), k - 1, float("nan")
    w = n / v
    sw = w.sum()
    mw = (w * m).sum() / sw
    numer = (w * (m - mw) ** 2).sum() / (k - 1)
    lam = (((1 - w / sw) ** 2) / (n - 1)).sum()
    denom = 1 + (2 * (k - 2) / (k ** 2 - 1)) * lam
    f = numer / denom
    df2 = (k ** 2 - 1) / (3 * lam) if lam > 0 else float("inf")
    return float(f), float(stats.f.sf(f, k - 1, df2)), k - 1, float(df2)


def one_way_anova(df: pd.DataFrame, feature: str, factor: str, min_group: int = 5) -> dict:
    """Full one-way analysis of `feature` across the levels of `factor`.

    Returns classic F, Welch F, Kruskal-Wallis H, Levene's homoscedasticity
    test, and both eta^2 (descriptive, biased up) and omega^2 (less biased
    population estimate). Levels with fewer than `min_group` rows are dropped
    rather than silently destabilising the variance estimates.
    """
    d = clean_for(df, feature)
    d = d.dropna(subset=[factor])
    out = {
        "Feature": FEATURE_LABELS.get(feature, feature), "column": feature,
        "n": len(d), "k groups": 0,
    }
    if d.empty:
        return out

    groups, levels = [], []
    for lvl, g in d.groupby(factor, observed=True):
        vals = g[feature].to_numpy(dtype=float)
        vals = vals[np.isfinite(vals)]
        if len(vals) >= min_group:
            groups.append(vals)
            levels.append(lvl)
    if len(groups) < 2:
        return out

    n_used = sum(len(g) for g in groups)
    f_stat, p_val = stats.f_oneway(*groups)
    fw, pw, _, df2 = welch_anova(groups)
    try:
        lev_w, lev_p = stats.levene(*groups, center="median")
    except ValueError:
        lev_w, lev_p = float("nan"), float("nan")
    try:
        kw_h, kw_p = stats.kruskal(*groups)
    except ValueError:
        kw_h, kw_p = float("nan"), float("nan")

    allv = np.concatenate(groups)
    grand = allv.mean()
    ss_total = float(((allv - grand) ** 2).sum())
    ss_between = float(sum(len(g) * (g.mean() - grand) ** 2 for g in groups))
    k = len(groups)
    eta2 = ss_between / ss_total if ss_total > 0 else float("nan")
    ms_within = (ss_total - ss_between) / (n_used - k) if n_used > k else float("nan")
    omega2 = ((ss_between - (k - 1) * ms_within) / (ss_total + ms_within)
              if np.isfinite(ms_within) and (ss_total + ms_within) > 0 else float("nan"))

    out.update({
        "n": n_used, "k groups": k,
        "F": float(f_stat), "p": float(p_val),
        "Welch F": fw, "Welch p": pw, "Welch df2": df2,
        "eta2": eta2, "omega2": max(omega2, 0.0) if np.isfinite(omega2) else omega2,
        "Effect": effect_label(eta2),
        "Levene p": float(lev_p), "KW H": float(kw_h), "KW p": float(kw_p),
        "levels": levels,
    })
    return out


def anova_table(df: pd.DataFrame, factor: str, features=None, min_group: int = 5) -> pd.DataFrame:
    """One row per feature: the full ANOVA family for a single grouping factor,
    sorted by eta^2 descending (largest *effect* first -- not smallest p, which
    at this n would just rank by sample size)."""
    features = features or list(FEATURE_LABELS)
    rows = [one_way_anova(df, f, factor, min_group) for f in features if f in df.columns]
    rows = [r for r in rows if r.get("k groups", 0) >= 2]
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).drop(columns=["levels"], errors="ignore")
    return out.sort_values("eta2", ascending=False).reset_index(drop=True)


def group_summary(df: pd.DataFrame, feature: str, factor: str, min_group: int = 5) -> pd.DataFrame:
    """Per-level n / mean / sd / sem / 95% CI half-width / median, for the
    group-means plot. CI uses the t distribution per group (not a pooled sd),
    consistent with the Welch/Games-Howell treatment elsewhere in this module."""
    d = clean_for(df, feature).dropna(subset=[factor])
    rows = []
    for lvl, g in d.groupby(factor, observed=True):
        v = g[feature].to_numpy(dtype=float)
        v = v[np.isfinite(v)]
        if len(v) < min_group:
            continue
        sd = v.std(ddof=1)
        sem = sd / np.sqrt(len(v))
        rows.append({
            "level": str(lvl), "n": len(v), "mean": float(v.mean()), "sd": float(sd),
            "sem": float(sem),
            "ci95": float(stats.t.ppf(0.975, len(v) - 1) * sem),
            "median": float(np.median(v)),
        })
    return pd.DataFrame(rows).sort_values("level").reset_index(drop=True)


def games_howell(df: pd.DataFrame, feature: str, factor: str, min_group: int = 5) -> pd.DataFrame:
    """Games-Howell post-hoc: all pairwise comparisons, Welch-corrected, with a
    studentized-range critical value. Chosen over Tukey HSD because the groups
    here are both unbalanced and heteroscedastic. Returns one row per pair with
    the mean difference, adjusted p, and a standardized difference."""
    d = clean_for(df, feature).dropna(subset=[factor])
    stat = {}
    for lvl, g in d.groupby(factor, observed=True):
        v = g[feature].to_numpy(dtype=float)
        v = v[np.isfinite(v)]
        if len(v) >= min_group and v.var(ddof=1) > 0:
            stat[str(lvl)] = (len(v), v.mean(), v.var(ddof=1))
    levels = sorted(stat)
    k = len(levels)
    if k < 2:
        return pd.DataFrame()

    rows = []
    for i in range(k):
        for j in range(i + 1, k):
            a, b = levels[i], levels[j]
            na, ma, va = stat[a]
            nb, mb, vb = stat[b]
            se2 = va / na + vb / nb
            if se2 <= 0:
                continue
            diff = ma - mb
            # Welch-Satterthwaite df for the pair
            dfree = se2 ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
            # Studentized range statistic: |diff| / sqrt(se^2 / 2)
            q = abs(diff) / np.sqrt(se2 / 2)
            p_adj = float(stats.studentized_range.sf(q, k, dfree))
            pooled_sd = np.sqrt((va + vb) / 2)
            rows.append({
                "group A": a, "group B": b, "mean A": ma, "mean B": mb,
                "mean diff": diff, "se": float(np.sqrt(se2)), "df": float(dfree), "q": float(q),
                "p (Games-Howell)": min(p_adj, 1.0),
                "Cohen d": diff / pooled_sd if pooled_sd > 0 else float("nan"),
                "significant": "Yes" if p_adj < 0.05 else "No",
            })
    return pd.DataFrame(rows)


def games_howell_matrix(gh: pd.DataFrame) -> tuple:
    """Reshape games_howell() output into a symmetric (levels, p-matrix,
    diff-matrix) triple for a heatmap. Diagonal is NaN."""
    if gh.empty:
        return [], np.zeros((0, 0)), np.zeros((0, 0))
    levels = sorted(set(gh["group A"]) | set(gh["group B"]))
    idx = {lv: i for i, lv in enumerate(levels)}
    n = len(levels)
    pm = np.full((n, n), np.nan)
    dm = np.full((n, n), np.nan)
    for _, r in gh.iterrows():
        i, j = idx[r["group A"]], idx[r["group B"]]
        pm[i, j] = pm[j, i] = r["p (Games-Howell)"]
        dm[i, j] = r["mean diff"]
        dm[j, i] = -r["mean diff"]
    return levels, pm, dm


# -- Correlation (continuous predictors) --------------------------------------

def spearman_partial(x: pd.Series, y: pd.Series, z: pd.Series) -> tuple:
    """Spearman r(x, y) and the partial r(x, y | z), plus the partial's p-value
    and n. Partial Spearman = partial Pearson computed on ranks."""
    d = pd.DataFrame({"x": x, "y": y, "z": z}).dropna()
    n = len(d)
    if n < 5:
        return float("nan"), float("nan"), float("nan"), n
    rx, ry, rz = d["x"].rank(), d["y"].rank(), d["z"].rank()
    r_xy, r_xz, r_yz = rx.corr(ry), rx.corr(rz), ry.corr(rz)
    denom = np.sqrt((1 - r_xz ** 2) * (1 - r_yz ** 2))
    r_p = (r_xy - r_xz * r_yz) / denom if denom > 1e-9 else float("nan")
    if np.isfinite(r_p) and n > 3:
        t = r_p * np.sqrt((n - 3) / max(1 - r_p ** 2, 1e-15))
        p_p = float(2 * stats.t.sf(abs(t), n - 3))
    else:
        p_p = float("nan")
    return float(r_xy), float(r_p), p_p, n


def correlation_strength(r: float) -> str:
    """Word-grade for |r|, so a p=1e-30 correlation of 0.06 cannot be read as
    'strong'. Thresholds are the usual .1/.3/.5 convention."""
    a = abs(r)
    if not np.isfinite(a):
        return "-"
    if a >= 0.5:
        return "Strong"
    if a >= 0.3:
        return "Moderate"
    if a >= 0.1:
        return "Weak"
    return "Negligible"


def correlation_table(df: pd.DataFrame, x_col: str = "soc_pct_est_pack",
                      control: str = "temp_avg_start_C", features=None) -> pd.DataFrame:
    """Per-feature correlation against `x_col`, before and after partialling out
    `control`. Reports Spearman (monotone, outlier-robust) as the headline,
    Pearson for reference, and rho^2 as 'variance explained' -- the number that
    actually answers "is there a usable relationship here?"."""
    features = features or list(FEATURE_LABELS)
    rows = []
    for feat in features:
        if feat not in df.columns:
            continue
        d = clean_for(df, feat).dropna(subset=[x_col, control])
        if len(d) < 10:
            continue
        rho, p_rho = stats.spearmanr(d[x_col], d[feat])
        pear, _ = stats.pearsonr(d[x_col], d[feat])
        _, rho_partial, p_partial, n = spearman_partial(d[x_col], d[feat], d[control])
        rows.append({
            "Feature": FEATURE_LABELS.get(feat, feat), "column": feat, "n": n,
            "Spearman rho": float(rho), "p": float(p_rho),
            "Pearson r": float(pear),
            "rho | control": rho_partial, "p | control": p_partial,
            "Var. explained (rho^2)": float(rho ** 2),
            "Strength": correlation_strength(rho),
        })
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["_a"] = out["Spearman rho"].abs()
    return out.sort_values("_a", ascending=False).drop(columns="_a").reset_index(drop=True)


def binned_trend(df: pd.DataFrame, feature: str, x_col: str = "soc_pct_est_pack",
                 bins=None, min_count: int = 10) -> pd.DataFrame:
    """Median + IQR + 95% CI of the median per x-bin.

    This is the readable core of the "better SoC plot": with 4799 starts over
    only ~204 distinct SoC values the raw scatter is a solid block, and any
    trend in it is invisible. Median/IQR per bin survives the overplotting and
    the R_int outliers alike. CI of the median uses the 1.253*sem normal
    approximation.
    """
    if bins is None:
        bins = np.arange(40, 102.5, 5.0)
    d = clean_for(df, feature).dropna(subset=[x_col])
    d = d.assign(_bin=pd.cut(d[x_col], bins=bins, include_lowest=True))
    rows = []
    for b, g in d.groupby("_bin", observed=True):
        v = g[feature].to_numpy(dtype=float)
        v = v[np.isfinite(v)]
        if len(v) < min_count:
            continue
        q1, med, q3 = np.percentile(v, [25, 50, 75])
        ci = 1.253 * v.std(ddof=1) / np.sqrt(len(v))
        rows.append({
            "x_center": float(b.mid), "x_label": str(b), "n": len(v),
            "median": float(med), "q1": float(q1), "q3": float(q3),
            "mean": float(v.mean()), "ci_lo": float(med - ci), "ci_hi": float(med + ci),
        })
    return pd.DataFrame(rows).sort_values("x_center").reset_index(drop=True)


def temperature_terciles(df: pd.DataFrame, col: str = "temp_avg_start_C") -> tuple:
    """Split temperature into three equal-count bands, returned as
    (Series of labels, ordered label list, {label: (lo, hi)}).

    Equal-count (not equal-width) because this fleet's temperature distribution
    is lopsided; equal-width bands would leave the cold band nearly empty and
    make its trend line meaningless.
    """
    t = df[col]
    lo, hi = t.quantile([1 / 3, 2 / 3])
    labels = [f"Cold (<{lo:.0f}C)", f"Mid ({lo:.0f}-{hi:.0f}C)", f"Warm (>{hi:.0f}C)"]
    binned = pd.cut(t, bins=[-np.inf, lo, hi, np.inf], labels=labels)
    return binned, labels, {labels[0]: (float(t.min()), float(lo)),
                            labels[1]: (float(lo), float(hi)),
                            labels[2]: (float(hi), float(t.max()))}


def temperature_tercile_edges(df: pd.DataFrame, col: str = "temp_avg_start_C") -> tuple:
    """Same split as temperature_terciles(), but returns (edges, labels) for
    pd.cut rather than an already-binned Series -- so the SAME edges can be
    reused across several differently-filtered subsets of the data (see
    joint_grid()'s docstring for why that matters: recomputing terciles
    per-subset would silently shift bin boundaries between e.g. the R_int
    panel, which excludes invalid_R_int rows, and one that doesn't)."""
    t = df[col]
    lo, hi = t.quantile([1 / 3, 2 / 3])
    labels = [f"Cold (<{lo:.0f}C)", f"Mid ({lo:.0f}-{hi:.0f}C)", f"Warm (>{hi:.0f}C)"]
    return [-np.inf, float(lo), float(hi), np.inf], labels


def joint_grid(df: pd.DataFrame, feature: str = None, x_col: str = "soc_pct_est_pack",
               y_col: str = "temp_avg_start_C", x_edges=None, x_labels=None,
               y_edges=None, y_labels=None, min_count: int = 20, agg: str = "mean") -> dict:
    """2-D binned grid over (x_col, y_col): a count grid always, plus a
    mean/median `feature` grid when `feature` is given.

    This is what a 1-D trend line or a per-point continuous colour scale
    can't show cleanly at n ~ 4800: an INTERACTION -- e.g. "is voltage drop
    worst specifically at LOW SoC *and* LOW temperature together, or does
    each act independently?" A cell in this grid is a direct answer for that
    (SoC bin, temp band) combination; a scatter coloured by temperature just
    overplots into a wash at this n (confirmed -- see the SoC & Temp Effects
    tab's earlier per-point version).

    `y_edges`/`y_labels` are required, not derived automatically here --
    compute them ONCE (e.g. via temperature_tercile_edges() on the full,
    unfiltered dataset) and pass the SAME edges into every call for a given
    figure, so every panel shares an identical grid. Deriving them
    independently per feature would silently shift bin boundaries between,
    say, the R_int panel (which excludes invalid_R_int rows via clean_for)
    and a panel that doesn't.

    Returns {x_labels, y_labels, count (2D array, rows=y/cols=x), value (2D
    array, same shape, NaN wherever count < min_count -- or None if `feature`
    was not given), agg}.
    """
    x_edges = x_edges if x_edges is not None else SOC_BIN_EDGES
    x_labels = x_labels if x_labels is not None else SOC_BIN_LABELS
    if y_edges is None or y_labels is None:
        raise ValueError("joint_grid() requires explicit y_edges/y_labels -- see docstring")

    d = df if feature is None else clean_for(df, feature)
    subset_cols = [x_col, y_col] + ([feature] if feature is not None else [])
    d = d.dropna(subset=subset_cols)

    x_binned = pd.cut(d[x_col], bins=x_edges, labels=x_labels, include_lowest=True)
    y_binned = pd.cut(d[y_col], bins=y_edges, labels=y_labels)
    grp = d.groupby([y_binned, x_binned], observed=False)

    count = grp.size().unstack().reindex(index=y_labels, columns=x_labels).fillna(0)
    value = None
    if feature is not None:
        value = grp[feature].agg(agg).unstack().reindex(index=y_labels, columns=x_labels)
        value = value.where(count >= min_count)

    return {
        "x_labels": list(x_labels), "y_labels": list(y_labels),
        "count": count.to_numpy(dtype=float),
        "value": value.to_numpy(dtype=float) if value is not None else None,
        "agg": agg,
    }
