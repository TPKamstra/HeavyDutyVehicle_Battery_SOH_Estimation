"""
soh_eval.py

The SOH evaluation of the PHMIC 2027 paper (Table `tab:features`), as a module
so the table can be reproduced from the repo. Pure pandas/numpy/scipy, no
Panel or Plotly, like can_stats.py.

Method (one battery, one label per block):
- R2_OCV : share of a feature's variance explained by a quadratic fit on the
           rest OCV. Near 1 means the feature only follows the charge level.
- rho    : Spearman correlation of the OCV-fit residual with SOH, i.e. what
           the feature says about health once the charge level is removed.
- LOBO   : leave one block out. A linear model SOH ~ OCV + OCV^2 + feature
           (standardised inputs, least squares) is fitted on the other blocks;
           the prediction for the left-out block is the median over its runs.
           RMSE over the blocks, in SOH percentage points. All runs in a block
           share one label, so a block, not a run, is the unit of validation.

Lab features measured at a fixed state (a C/5, a full charge) have no OCV:
pass ocv_col=None, and the baseline becomes the mean SOH of the other blocks.

Paper values (unit3, blocks 2-11, invalid values masked): mean 18.8,
OCV only 17.1, cold-crank R_int deltaI 10.1 SOH %-points.
"""

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

OCV_COL = "ocv_window_Vpre"
MIN_BLOCKS = 6     # a feature valid in fewer blocks is not ranked
MIN_ROWS = 30      # run-level features; lab features pass their own minimum


def _design(z: pd.DataFrame, ocv_col, cols: list) -> np.ndarray:
    parts = [np.ones(len(z))]
    if ocv_col is not None:
        parts += [z[ocv_col].to_numpy(float), z[ocv_col].to_numpy(float) ** 2]
    parts += [z[c].to_numpy(float) for c in cols]
    return np.column_stack(parts)


def lobo(df: pd.DataFrame, cols: list, ocv_col=OCV_COL, label: str = "soh",
         group: str = "block") -> tuple:
    """(RMSE, {block: prediction - label}) of the leave-one-block-out model
    label ~ [OCV + OCV^2] + cols. With no OCV and no cols this is the mean of
    the other blocks' labels."""
    err = {}
    for b in sorted(df[group].unique()):
        tr, te = df[df[group] != b], df[df[group] == b]
        if not cols and ocv_col is None:
            pred = tr.groupby(group)[label].first().mean()
        else:
            a_tr, a_te = _design(tr, ocv_col, cols), _design(te, ocv_col, cols)
            mu, sd = a_tr[:, 1:].mean(0), a_tr[:, 1:].std(0) + 1e-12
            a_tr[:, 1:] = (a_tr[:, 1:] - mu) / sd
            a_te[:, 1:] = (a_te[:, 1:] - mu) / sd
            coef = np.linalg.lstsq(a_tr, tr[label].to_numpy(float), rcond=None)[0]
            pred = float(np.median(a_te @ coef))
        err[int(b)] = float(pred) - float(te[label].iloc[0])
    e = np.array(list(err.values()))
    return float(np.sqrt(np.mean(e ** 2))) if len(e) else np.nan, err


def mean_baseline(df: pd.DataFrame, label: str = "soh", group: str = "block") -> float:
    """LOBO RMSE of predicting each block by the mean label of the other blocks."""
    return lobo(df, [], ocv_col=None, label=label, group=group)[0]


def ocv_r2_and_rho(df: pd.DataFrame, feature: str, ocv_col=OCV_COL, label: str = "soh") -> tuple:
    """(R2 of a quadratic OCV fit, Spearman rho of its residual with the label).
    Without an OCV column: (nan, plain Spearman rho)."""
    y = df[feature].to_numpy(float)
    if ocv_col is None:
        return np.nan, float(spearmanr(y, df[label])[0])
    x = df[ocv_col].to_numpy(float)
    co = np.polyfit(x, y, 2)
    res = y - np.polyval(co, x)
    r2 = 1 - res.var() / y.var() if y.var() > 0 else np.nan
    return float(r2), float(spearmanr(res, df[label])[0])


def same_sign_share(df: pd.DataFrame, feature: str, by: str = "target_soc_pct",
                    label: str = "soh", min_blocks: int = 5) -> float:
    """Share of SoC levels whose rank correlation with the label has the sign of
    the median one: 1.0 means the trend points the same way at every level."""
    if by not in df.columns:
        return np.nan
    per = []
    for _, g in df.groupby(by):
        if g["block"].nunique() >= min_blocks and g[feature].std() > 0:
            r = spearmanr(g[feature], g[label])[0]
            if not np.isnan(r):
                per.append(r)
    if not per:
        return np.nan
    return float(np.mean(np.sign(per) == np.sign(np.median(per))))


def rank_features(df: pd.DataFrame, features: list, ocv_col=OCV_COL, label: str = "soh",
                  min_blocks: int = MIN_BLOCKS, min_rows: int = MIN_ROWS) -> pd.DataFrame:
    """One row per feature with enough valid data: n, blocks, R2_OCV, rho,
    LOBO RMSE, the baseline on the same rows (OCV only, or the mean), and the
    worst block. Invalid values must already be NaN. Sorted on LOBO RMSE."""
    rows = []
    for f in features:
        need = [f, label] + ([ocv_col] if ocv_col else [])
        if f not in df.columns:
            continue
        sub = df.dropna(subset=need)
        if sub["block"].nunique() < min_blocks or len(sub) < min_rows or sub[f].std() == 0:
            continue
        r2, rho = ocv_r2_and_rho(sub, f, ocv_col, label)
        rm, errs = lobo(sub, [f], ocv_col, label)
        base = lobo(sub, [], ocv_col, label)[0] if ocv_col else mean_baseline(sub, label)
        worst = max(errs, key=lambda k: abs(errs[k]))
        rows.append({
            "feature": f, "n": len(sub), "blocks": int(sub["block"].nunique()),
            "r2_ocv": r2, "rho": rho, "same_sign": same_sign_share(sub, f, label=label),
            "lobo_rmse": rm, "baseline_rmse": base, "gain": base - rm,
            "worst_block": worst, "worst_err": errs[worst],
        })
    cols = ["feature", "n", "blocks", "r2_ocv", "rho", "same_sign", "lobo_rmse",
            "baseline_rmse", "gain", "worst_block", "worst_err"]
    return pd.DataFrame(rows, columns=cols).sort_values("lobo_rmse").reset_index(drop=True)


def baselines(df: pd.DataFrame, ocv_col=OCV_COL, label: str = "soh") -> dict:
    """The two reference rows of the paper's table on all rows with an OCV."""
    base = df.dropna(subset=[ocv_col, label]) if ocv_col else df.dropna(subset=[label])
    out = {"mean": mean_baseline(base, label)}
    if ocv_col:
        out["ocv_only"] = lobo(base, [], ocv_col, label)[0]
    return out


def value_at_charge(df: pd.DataFrame, feature: str, removed_ah: float,
                    x_col: str = "removed_ah", group: str = "block", max_gap_ah: float = 1.5) -> pd.Series:
    """Per block, the feature linearly interpolated at a fixed charge removed.
    The comparison the paper argues for: equal absolute charge, not equal
    relative SoC. A block without valid points within max_gap_ah on both sides
    gets no value rather than an extrapolation."""
    out = {}
    for b, g in df.dropna(subset=[feature, x_col]).groupby(group):
        g = g.sort_values(x_col)
        x, y = g[x_col].to_numpy(float), g[feature].to_numpy(float)
        lo, hi = x[x <= removed_ah], x[x >= removed_ah]
        if len(lo) == 0 or len(hi) == 0:
            continue
        if removed_ah - lo.max() > max_gap_ah or hi.min() - removed_ah > max_gap_ah:
            continue
        out[int(b)] = float(np.interp(removed_ah, x, y))
    return pd.Series(out, name=feature, dtype=float)
