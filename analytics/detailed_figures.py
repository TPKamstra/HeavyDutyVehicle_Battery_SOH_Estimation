"""
detailed_figures.py

Plotly figures and tables for the unit3 dashboard's Detailed Features tab
(features from detailed_features.py, evaluation from soh_eval.py). No Panel
code here, so export scripts can draw the same figures.

Every figure answers one question, stated in its title, and maps to a paper
element (see the "Graph → paper" table in analytics/CLAUDE.md):
  raw      "Is the feature measured where we think, and does the fit follow the signal?"  → method figure
  vs_ah    "Does the feature separate the blocks at an equal charge removed?"            → Fig. 2c type
  vs_soh   "Does the feature follow SOH at an equal charge removed?"                     → Results
  ranking  "Which features carry SOH information beyond the charge level?"                → tab:features
"""

import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import spearmanr

import detailed_features as dfm
import soh_eval as se
import testday_v2_features as v2feat

INVALID_SUFFIX = "__invalid_reason"
INVALID_MARKER = dict(symbol="x", size=9, color="#9a9890")
SOURCE_LABEL = {"testday": "test day (20 Hz)", "c5": "C/5 capacity test", "charge": "CC/CV charge",
                "rest": "30 min rest after a step-down"}
PAPER_WIDTH_IN = {"single": 3.5, "double": 7.16}


def _layout(fig, title, height=420):
    fig.update_layout(title=dict(text=title, x=0.01, xanchor="left", font=dict(size=14)),
                      template="plotly_white", height=height, margin=dict(l=60, r=20, t=60, b=50),
                      legend=dict(orientation="v"))
    return fig


def _empty(title):
    return _layout(go.Figure(), title, 320)


def _valid_split(df, feature):
    reason = df.get(feature + INVALID_SUFFIX, pd.Series("", index=df.index)).fillna("")
    has = df[feature].notna()
    return df[has & (reason == "")], df[has & (reason != "")]


# ── Raw signal with the feature's window ─────────────────────────────────────

def raw_testday_fig(feature, runs, colors, read_run):
    """runs: rows (one per block) at one SoC, with 'block' and 'filename';
    read_run(filename) -> (df sorted by time, setpoint). One line per block,
    time since the event's onset, the feature's window shaded, fits dashed."""
    e = dfm.DETAILED_CATALOG[feature]
    etype, occ = e["event"]
    is_relax = etype == "recovery_rest"
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.05)
    for (_, r), color in zip(runs.iterrows(), colors):
        df, _ = read_run(r["filename"])
        if df is None:
            continue
        occs = v2feat.event_occurrences(df)
        keys = [(a, b) for a, b, _ in occs]
        if (etype, occ) not in keys:
            continue
        k = keys.index((etype, occ))
        g, prev = occs[k][2], (occs[k - 1][2] if k > 0 else None)
        t = g["Elapsed_s"].to_numpy(float) - float(g["Elapsed_s"].min())
        x = np.maximum(t, 0.05) if is_relax else t
        name = f"Block {int(r['block'])}"
        fig.add_trace(go.Scatter(x=x, y=g["Voltage"], mode="lines", name=name, legendgroup=name,
                                 line=dict(color=color, width=1.2)), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=g["Current"], mode="lines", name=name, legendgroup=name,
                                 showlegend=False, line=dict(color=color, width=1)), row=2, col=1)
        fit = None
        if "_ecm_" in feature:
            fit = dfm.ecm_fit(g, prev)
        elif is_relax and any(s in feature for s in ("tau", "a2_share")):
            fit = dfm.relaxation(g)
        if fit and "_curve" in fit:
            tc, vc = fit["_curve"]
            fig.add_trace(go.Scatter(x=np.maximum(tc, 0.05) if is_relax else tc, y=vc, mode="lines",
                                     name=f"{name} fit", legendgroup=name, showlegend=False,
                                     line=dict(color=color, width=1, dash="dash")), row=1, col=1)
    if "_R0_" in feature or feature.endswith("R0_mohm"):
        fig.add_vrect(x0=0, x1=dfm.R0_SEARCH_S, fillcolor="#2a78d6", opacity=0.08, line_width=0, row=1, col=1)
    if any(s in feature for s in ("Rend", "Rpol")):
        tmax = max((tr.x.max() for tr in fig.data if len(tr.x)), default=1)
        fig.add_vrect(x0=tmax - dfm.END_WIN_S, x1=tmax, fillcolor="#e34948", opacity=0.08, line_width=0, row=1, col=1)
    if "logslope" in feature:
        fig.add_vrect(x0=dfm.RELAX_LOG_FROM_S, x1=dfm.RELAX_LOG_TO_S, fillcolor="#2a78d6", opacity=0.08,
                      line_width=0, row=1, col=1)
    if is_relax:
        fig.update_xaxes(type="log")
    fig.update_xaxes(title_text="time since the event started [s]", row=2, col=1)
    fig.update_yaxes(title_text="V", row=1, col=1)
    fig.update_yaxes(title_text="I [A]", row=2, col=1)
    return _layout(fig, f"Is {feature} measured where we think? {etype} #{occ}, all blocks "
                        "(shaded: window; dashed: fit)", 520)


def raw_lab_fig(feature, log_dir, table, colors, soc=None):
    """C/5 start, charge curve (cycle 3) or rest after a step-down, one line per block."""
    src = dfm.DETAILED_CATALOG[feature]["source"]
    fig = go.Figure()
    if table.empty:
        return _empty("No lab files found")
    if src == "rest":
        rows = table[table["target_soc_pct"] == soc] if soc is not None else table
        title = f"Rest after the step-down to {soc} % SoC: V - V(first sample)"
    elif src == "charge":
        rows = table[table["cycle"] == 3]
        title = "CC/CV charge after a full discharge (cycle 3): " + (
            "current in the CV phase" if "cv_tau" in feature else "voltage against Ah charged")
    else:
        rows = table
        title = "Start of the C/5 test from full (1 h rest): voltage, 31 s running median"
    for (_, r), color in zip(rows.sort_values("block").iterrows(), colors):
        d = pd.read_csv(os.path.join(log_dir, r["file"]), on_bad_lines="skip").sort_values("Elapsed_s")
        t = d["Elapsed_s"].to_numpy(float)
        v, i = d["Voltage"].to_numpy(float), d["Current"].to_numpy(float)
        name = f"Block {int(r['block'])}"
        if src == "rest":
            fig.add_trace(go.Scatter(x=t - t[0] + 10, y=(v - v[0]) * 1000, mode="lines", name=name,
                                     line=dict(color=color)))
        elif src == "charge":
            if "cv_tau" in feature:
                k = int(np.argmax((v >= dfm.CHG_CV_V) & (i < 0.95 * np.median(i[:50]))))
                fig.add_trace(go.Scatter(x=t[k:] - t[k], y=i[k:], mode="lines", name=name, line=dict(color=color)))
            else:
                ah = np.concatenate([[0.0], np.cumsum(np.clip(i[1:], 0, None) * np.clip(np.diff(t), 0, 10))]) / 3600
                fig.add_trace(go.Scatter(x=ah, y=v, mode="lines", name=name, line=dict(color=color)))
        else:
            load = np.abs(i) > 1.0
            t0 = t[np.argmax(load)]
            m = (t - t0 >= -5) & (t - t0 <= dfm.CDF_SEARCH_S * 2)
            vs = pd.Series(v).rolling(dfm.C5_SMOOTH_N, center=True, min_periods=1).median().to_numpy()
            fig.add_trace(go.Scatter(x=t[m] - t0, y=vs[m], mode="lines", name=name, line=dict(color=color)))
    if src == "rest":
        fig.update_xaxes(type="log", title_text="time into the rest [s]")
        fig.update_yaxes(title_text="V - V(first sample) [mV]")
    elif src == "charge":
        fig.update_xaxes(title_text="time in the CV phase [s]" if "cv_tau" in feature else "Ah charged")
        fig.update_yaxes(title_text="I [A]" if "cv_tau" in feature else "V")
    else:
        fig.add_vrect(x0=0, x1=dfm.CDF_SEARCH_S, fillcolor="#2a78d6", opacity=0.06, line_width=0)
        fig.update_xaxes(title_text="time since the C/5 current started [s]")
        fig.update_yaxes(title_text="V")
    return _layout(fig, title, 480)


# ── Feature against the charge removed, and against SOH ──────────────────────

def vs_ah_fig(feature, df, colors, x_col="removed_ah"):
    """Field and rest features: one line per block against the charge removed.
    C/5 and charge features (one state per block): against the block."""
    if df.empty or feature not in df.columns:
        return _empty(f"No values for {feature}")
    src = dfm.DETAILED_CATALOG.get(feature, {}).get("source", "testday")
    valid, inv = _valid_split(df, feature)
    fig = go.Figure()
    blocks = sorted(df["block"].dropna().unique())
    if src in ("c5", "charge"):
        for b, color in zip(blocks, colors):
            g = valid[valid["block"] == b]
            fig.add_trace(go.Scatter(x=[b] * len(g), y=g[feature], mode="markers", name=f"Block {int(b)}",
                                     marker=dict(color=color, size=9)))
        x_title, q = "block", f"Does {feature} change from block to block?"
        x_inv = inv["block"]
    else:
        for b, color in zip(blocks, colors):
            g = valid[valid["block"] == b].sort_values(x_col)
            if g.empty:
                continue
            fig.add_trace(go.Scatter(x=g[x_col], y=g[feature], mode="markers+lines", name=f"Block {int(b)}",
                                     line=dict(color=color), marker=dict(color=color, size=8)))
        x_title = "Ah removed from full before the point [Ah]"
        q = f"Does {feature} separate the blocks at an equal charge removed?"
        x_inv = inv[x_col] if x_col in inv else None
    if not inv.empty and x_inv is not None:
        reasons = inv[feature + INVALID_SUFFIX].fillna("") if feature + INVALID_SUFFIX in inv else ""
        fig.add_trace(go.Scatter(x=x_inv, y=inv[feature], mode="markers", marker=INVALID_MARKER,
                                 name="invalid (not used)", text=reasons,
                                 hovertemplate="%{y}<br>invalid: %{text}<extra></extra>"))
    fig.update_xaxes(title_text=x_title)
    fig.update_yaxes(title_text=f"{feature} [{dfm.DETAILED_CATALOG.get(feature, {}).get('unit', '')}]")
    return _layout(fig, q)


def per_block_at_charge(feature, df, removed_ah, x_col="removed_ah"):
    """Block -> value: interpolated at removed_ah for field/rest features, the
    block median for C/5 and charge features."""
    src = dfm.DETAILED_CATALOG.get(feature, {}).get("source", "testday")
    valid, _ = _valid_split(df, feature)
    if src in ("c5", "charge"):
        return valid.groupby("block")[feature].median()
    return se.value_at_charge(valid, feature, removed_ah, x_col=x_col)


def vs_soh_fig(feature, df, soh_by_block, removed_ah, colors):
    s = per_block_at_charge(feature, df, removed_ah)
    if s.empty:
        return _empty(f"No block has valid {feature} values on both sides of {removed_ah:g} Ah")
    x = [soh_by_block.get(int(b)) for b in s.index]
    ok = [xx is not None for xx in x]
    s, x = s[ok], [xx for xx in x if xx is not None]
    rho = spearmanr(x, s.values)[0] if len(s) >= 4 else np.nan
    src = dfm.DETAILED_CATALOG.get(feature, {}).get("source", "testday")
    where = "per block (median)" if src in ("c5", "charge") else f"at {removed_ah:g} Ah removed"
    fig = go.Figure(go.Scatter(
        x=x, y=s.values, mode="markers+lines+text", text=[f"B{int(b)}" for b in s.index], textposition="top center",
        marker=dict(size=10, color=colors[-1]), line=dict(color="#c3c2b7", width=1), name=feature))
    fig.update_xaxes(title_text="SOH (C/5, % of Block 1)", autorange="reversed")
    fig.update_yaxes(title_text=feature)
    return _layout(fig, f"Does {feature} follow SOH {where}? Spearman ρ = {rho:+.2f}, n = {len(s)} blocks")


# ── Ranking ──────────────────────────────────────────────────────────────────

def ranking_table(runs, labs, soh_by_block, existing_features):
    """All features in one table: baselines first, then sorted on LOBO RMSE.
    runs: analysis-view run rows with invalid values already NaN; labs: dict
    source -> table with invalid values NaN. Lab features use blocks 1-11."""
    parts = []
    d = runs.copy()
    d["soh"] = d["block"].map(soh_by_block)
    b = se.baselines(d)
    base = pd.DataFrame([
        {"feature": "(none: mean SOH of the other blocks)", "set": "baseline", "lobo_rmse": b["mean"]},
        {"feature": "(none: OCV only)", "set": "baseline", "lobo_rmse": b["ocv_only"]},
    ])
    t = se.rank_features(d, list(existing_features) + dfm.FIELD_FEATURES)
    t["set"] = np.where(t["feature"].isin(dfm.DETAILED_CATALOG), "new", "existing")
    t["availability"], t["source"] = "field", "test day"
    parts.append(t)
    for src, (tab, ocv, min_rows) in labs.items():
        if tab.empty:
            continue
        x = tab.copy()
        x["soh"] = x["block"].map(soh_by_block)
        r = se.rank_features(x, dfm.features_of(src), ocv_col=ocv, min_rows=min_rows)
        r["set"], r["availability"], r["source"] = "new", "lab", SOURCE_LABEL[src]
        parts.append(r)
    out = pd.concat(parts, ignore_index=True).sort_values("lobo_rmse")
    out = pd.concat([base, out], ignore_index=True)
    cols = ["feature", "set", "availability", "source", "n", "blocks", "r2_ocv", "rho", "same_sign",
            "lobo_rmse", "baseline_rmse", "gain", "worst_block", "worst_err"]
    return out.reindex(columns=cols)


def ranking_latex(table, top=15):
    """Rows for the paper's table, in its column order (feature, R2_OCV, rho, RMSE)."""
    lines = []
    for _, r in table.head(top + 2).iterrows():
        r2 = "--" if pd.isna(r["r2_ocv"]) else f"{r['r2_ocv']:.2f}"
        rho = "--" if pd.isna(r["rho"]) else f"${r['rho']:+.2f}$"
        name = str(r["feature"]).replace("_", r"\_")
        lines.append(f"{name} & {r2} & {rho} & {r['lobo_rmse']:.1f} \\\\")
    return "\n".join(lines)


def export_for_paper(out_dir, table, figs: dict, height_in=2.6):
    """Ranking as CSV + LaTeX rows, each figure as PDF and PNG at single- and
    double-column width. Returns the list of written files."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    p = os.path.join(out_dir, "detailed_feature_ranking.csv")
    table.to_csv(p, index=False)
    written.append(p)
    p = os.path.join(out_dir, "detailed_feature_ranking_rows.tex")
    with open(p, "w", encoding="utf-8") as f:
        f.write(ranking_latex(table) + "\n")
    written.append(p)
    for name, fig in figs.items():
        for width_name, w in PAPER_WIDTH_IN.items():
            g = go.Figure(fig)
            g.update_layout(title=None, font=dict(size=9, family="serif"), margin=dict(l=45, r=10, t=10, b=40))
            for ext, scale in (("pdf", 1), ("png", 3)):
                path = os.path.join(out_dir, f"{name}_{width_name}.{ext}")
                g.write_image(path, width=int(w * 96), height=int(height_in * 96), scale=scale)
                written.append(path)
    return written
