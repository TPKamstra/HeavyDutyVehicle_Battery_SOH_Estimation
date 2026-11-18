"""
detailed_features.py

Curve-based features for ul18_12_unit3, on top of the level/minimum/ΔV-ΔI
features of testday_v2_features.py. Brief: DETAILED_FEATURES_BRIEF.md.

Two groups, kept apart because they answer different questions:

field  - from the test-day events (20 Hz), so a vehicle that logs voltage and
         current around the same events could compute them. Candidate health
         indicators. One value per test-day run.
lab    - from tests a vehicle never runs: the C/5 capacity test, the CC/CV
         charges of the degradation cycles, the 30 min rests after each SoC
         step-down. Evidence about the ageing mechanism, not on-board
         indicators. One value per C/5, charge or rest.

Every feature has a catalogue entry (DETAILED_CATALOG) with its definition,
source, availability, literature key and validity rules. Field features also
inherit the invalid reason of a "parent" feature of FEATURE_CATALOG, so the
per-event rules (10.2 V floor, current not reached, ...) are not repeated.

Resolution: the voltage ADC step is ~8 mV. A fit residual close to that is
quantisation, so fit-quality thresholds are a few steps, not zero.
"""

import os
import re
import warnings

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

import testday_v2_features as v2feat

CACHE_VERSION = "detailed_v2"   # bump when a definition or rule below changes

# ── Constants ────────────────────────────────────────────────────────────────
CURRENT_RISE_S = v2feat.CURRENT_RISE_S   # the sink needs ~0.1 s to reach a new current
R0_SEARCH_S = 0.5          # R0 is taken at the largest current step within this window
REF_TAIL_S = 1.0           # V and I just before an event: mean over the last second of the event before
END_WIN_S = 0.5            # V and I at the end of an event: mean over its last half second
CRANK_SUSTAIN_FROM_S = 1.0 # cold crank: the -38 A plateau starts ~0.6 s after onset
ALT_SLOPE_S = 60.0         # alternator charge: voltage slope over the first minute
RELAX_LOG_FROM_S, RELAX_LOG_TO_S = 1.0, 100.0  # window of the log-time relaxation slope
FIT_RMSE_MAX_MV = 10.0     # ECM / relaxation fit worse than this (> 1 ADC step) is rejected
TRACK_TOL_FRAC = 0.10      # charge-event tracking, same tolerance as the discharge check
C5_SMOOTH_N = 31           # C/5 (1 Hz): running-median window for the curve features, samples
C5_NOISE_MAX_MV = 10.0     # C/5 voltage noise (robust std around that median) above this: rejected
C5_EQUAL_AH = 3.0          # C/5: dV/dQ compared at this charge removed (all blocks reach it)
C5_DVDQ_HALF_AH = 0.5      # ... over +-0.5 Ah
CDF_SEARCH_S = 900.0       # coup de fouet: trough and plateau searched in the first 15 min
CDF_MIN_DEPTH_V = 0.016    # two ADC steps: a smaller rise is not a coup de fouet
CHG_V_AT_AH = (0.5, 2.0)   # charge: V at these Ah charged (CC phase in every block)
CHG_CV_V = 14.35           # CV phase: voltage at or above this
CHG_MIN_BYTES = 50_000     # degradation charge files smaller than this are fragments

LIT = {
    "khodadadi2021": "Khodadadi Sadabadi et al. 2021, J. Energy Storage (step response ~7 s)",
    "kerley2015": "Kerley, Hyun, Ha 2015 (cranking voltage SOH)",
    "grube2008": "Grube 2008, MSc thesis (cranking voltage / parity relation)",
    "zhu2022": "Zhu et al. 2022, Nat. Commun. (relaxation voltage, Li-ion)",
    "pascoe2002": "Pascoe & Anbuky 2002, J. Power Sources (coup de fouet of VRLA)",
    "delaille2006": "Delaille et al. 2006, J. Power Sources (coup de fouet vs SoC and SOH)",
    "electronics2023": "Huang & Li 2023, Electronics 12(21) 4552 (CC charging-curve features, lead-acid)",
    "bose2000": "Bose & Laman 2000, INTELEC (SOH through coup de fouet)",
    "sun2009": "Sun, Jou, Wu 2009, Energy Convers. Manag. (sample entropy, lead-acid)",
    "yang2025": "Yang et al. 2025, Electrochim. Acta (DRT of VRLA degradation)",
    "sugumaran2024": "Sugumaran & Everill 2024, J. Energy Storage (IC/DV of lead-acid PAM decay)",
    "dubarry2022": "Dubarry & Anseán 2022 (incremental capacity best practices)",
    "du2024": "Du et al. 2024, PHM Society (12 V health indicators, enabling conditions)",
    "jiang2022": "Jiang & Song 2022, review of lead-acid SOH methods",
}


def _cat(source, formula, unit, meaning, availability, lit, parent=None, rules=(), event=None,
         adc_scale=None, note=""):
    return {"source": source, "formula": formula, "unit": unit, "meaning": meaning,
            "availability": availability, "lit": tuple(lit), "parent": parent,
            "rules": tuple(rules), "event": event, "adc_scale": adc_scale, "note": note}


def _split_entries(prefix, event, parent, dI_text, lit, end_text, parent_end=None):
    """parent: catalogued feature whose validity covers the onset (R0);
    parent_end: the one covering the end of the event (default: the same)."""
    parent_end = parent_end or parent
    both = tuple(dict.fromkeys((parent, parent_end)))
    return {
        f"{prefix}_R0_mohm": _cat(
            "testday", f"(V(t*) - V_ref) / (I(t*) - I_ref) x 1000, t* = sample of the largest voltage "
            f"deviation within {R0_SEARCH_S:g} s of onset; V_ref, I_ref = last {REF_TAIL_S:g} s before", "mOhm",
            f"Instantaneous (ohmic plus fast charge-transfer) resistance at the {dI_text} step.",
            "field", lit, parent, ("fit_points",), event),
        f"{prefix}_Rend_mohm": _cat(
            "testday", f"(V_end - V_ref) / (I_end - I_ref) x 1000, end = last {END_WIN_S:g} s ({end_text})",
            "mOhm", "Total resistance at the end of the event, polarisation included.",
            "field", lit, parent_end, (), event),
        f"{prefix}_Rpol_mohm": _cat(
            "testday", "Rend - R0", "mOhm",
            "Polarisation built up during the event (diffusion, concentration).",
            "field", lit, both, (), event),
        f"{prefix}_Rpol_ratio": _cat(
            "testday", "Rpol / R0", "-",
            "Share of slow over fast resistance. Sulphation and electrolyte loss are expected to move "
            "the two parts differently.", "field", lit, both, (), event),
    }


DETAILED_CATALOG: dict = {
    # ── A1: ohmic vs polarisation split per load step ──────────────────────────
    **_split_entries("wakeup1", ("wakeup_load", 1), "wakeup_load_1_R_int_mohm", "1 A wake-up",
                     ["khodadadi2021"], "120 s at 1 A"),
    **_split_entries("glow", ("glow_plug_like_load", 1), "glow_plug_like_load_1_Vmean", "8 A glow",
                     ["khodadadi2021"], "45 s at 8 A"),
    **_split_entries("crank_cold", ("crank_pulse", 1), "crank_cold_R_int_deltaI_mohm", "-60 A cold-crank",
                     ["khodadadi2021", "kerley2015", "grube2008"], "4 s; -38 A plateau",
                     parent_end="crank_cold_V_sustained"),
    **_split_entries("crank_hot", ("crank_pulse", 2), "crank_hot_R_int_deltaI_mohm", "-35 A hot-crank",
                     ["khodadadi2021", "kerley2015"], "2.5 s at -30 A", parent_end="crank_hot_V_sustained"),
    "crank_cold_dRdt_mohm_per_s": _cat(
        "testday", f"slope of (V_ref - V)/(I - I_ref) over the -38 A plateau (from {CRANK_SUSTAIN_FROM_S:g} s "
        "to the end) x 1000", "mOhm/s",
        "How fast the resistance grows while the crank current is held: the polarisation rate.",
        "field", ["khodadadi2021"], "crank_cold_V_sustained", ("fit_points",), ("crank_pulse", 1)),
    # ── A2: equivalent-circuit fit ─────────────────────────────────────────────
    **{f"{p}_ecm_{k}": _cat(
        "testday", "least-squares fit of V(t) = V_ref + dI (R0 + R1 (1 - exp(-t/tau))) + k t "
        f"over the {ev[0]} step (dI signed)", u, m, "field", ["khodadadi2021"], par,
        ("fit_rmse", "fit_bound"), ev,
        note="The linear k t term takes up the OCV drift from the charge removed during the step.")
       for p, ev, par in (("glow", ("glow_plug_like_load", 1), "glow_plug_like_load_1_Vmean"),
                          ("wakeup1", ("wakeup_load", 1), "wakeup_load_1_R_int_mohm"))
       for k, u, m in (("R0_mohm", "mOhm", "Series resistance of the 1-RC model."),
                       ("R1_mohm", "mOhm", "Polarisation resistance of the 1-RC model."),
                       ("tau_s", "s", "Time constant of the RC branch."),
                       ("rmse_mV", "mV", "Fit residual: a quality check, not a health feature."))},
    # ── A3: relaxation after the cranks and the ramp ──────────────────────────
    **{f"relax_{lbl}_{k}": _cat(
        "testday", f, u, m, "field", ["zhu2022", "jiang2022"], par, rules, ("recovery_rest", occ),
        note="In the field the alternator charges after the start, so a rest like this only exists "
             "after a short stop or a failed start.")
       for lbl, occ, par in (("cold", 1, "recovery_after_cold_V_plus30s"),
                             ("ramp", 2, "recovery_after_ramp_V_plus30s"),
                             ("hot", 3, "recovery_after_hot_V_plus30s"))
       for k, f, u, m, rules in (
           ("logslope_mV_per_dec", f"slope of V against log10(t) between {RELAX_LOG_FROM_S:g} and "
            f"{RELAX_LOG_TO_S:g} s into the rest", "mV/decade",
            "Relaxation speed. Lead-acid relaxes roughly linearly in log(t).", ("fit_points",)),
           ("amp_mV", "V(end) - V(0.5 s)", "mV", "Total relaxation over the rest.", ("fit_points",)),
           ("tau1_s", "fast time constant of V = Vinf - a1 exp(-t/tau1) - a2 exp(-t/tau2)", "s",
            "Fast relaxation (charge transfer, double layer).", ("fit_rmse", "fit_bound")),
           ("tau2_s", "slow time constant of the same fit", "s",
            "Slow relaxation (acid diffusion into the plates).", ("fit_rmse", "fit_bound")),
           ("a2_share", "a2 / (a1 + a2)", "-", "Share of the slow component in the relaxation.",
            ("fit_rmse", "fit_bound")),
       )},
    # ── A5: charge acceptance during the alternator charge ─────────────────────
    "alt_R0_mohm": _cat(
        "testday", "(V(t*) - V_ref) / (I(t*) - I_ref) x 1000 at the +4.5 A charge step", "mOhm",
        "Charge-side instantaneous resistance.", "field", ["jiang2022"],
        "alternator_charge_after_cold_Ah", ("charge_tracking", "fit_points"), ("alternator_charge", 1)),
    "alt_dVdt_mV_per_min": _cat(
        "testday", f"slope of V over the first {ALT_SLOPE_S:g} s after the step", "mV/min",
        "How fast the voltage rises under charge: low charge acceptance (sulphation) rises faster.",
        "field", ["jiang2022"], "alternator_charge_after_cold_Ah", ("charge_tracking", "fit_points"),
        ("alternator_charge", 1)),
    "alt_R0_over_crank_R0": _cat(
        "testday", "alt_R0_mohm / crank_cold_R0_mohm", "-",
        "Charge versus discharge resistance. Sulphation is expected to raise the charge side more.",
        "field", ["jiang2022"], "crank_cold_R_int_deltaI_mohm", ("charge_tracking",), ("alternator_charge", 1)),
    # ── B1: start of the C/5 (lab, one per block) ──────────────────────────────
    "c5_R_1s_mohm": _cat(
        "c5", "(V of the last rest sample - V of the first sample under load) / |I| x 1000, after 1 h rest "
        "from full", "mOhm", "Resistance of the full battery at C/5 after 1 s (1 Hz logging).", "lab",
        ["jiang2022"], rules=("c5_complete",)),
    "c5_drop_60s_mV": _cat(
        "c5", "V(first sample under load) - V(60 s)", "mV", "Polarisation built up in the first minute at C/5 from full.",
        "lab", ["jiang2022"], rules=("c5_complete",)),
    "c5_cdf_depth_mV": _cat(
        "c5", f"max V after the trough - trough V, both in the first {CDF_SEARCH_S:g} s", "mV",
        "Coup de fouet: a dip and recovery at the start of a discharge from full, linked to sulphation.",
        "lab", ["pascoe2002", "delaille2006", "bose2000"], rules=("c5_complete",),
        note=f"Below {CDF_MIN_DEPTH_V*1000:g} mV (two ADC steps) there is no coup de fouet to time."),
    "c5_cdf_time_s": _cat(
        "c5", "time from the trough to the plateau (the max after it)", "s",
        "The coup-de-fouet timing reported as most related to sulphation (Delaille et al.).",
        "lab", ["pascoe2002", "delaille2006"], rules=("c5_complete", "cdf_present")),
    "c5_dVdQ_at3Ah_mV_per_Ah": _cat(
        "c5", f"slope of V against Ah removed over {C5_EQUAL_AH:g} +- {C5_DVDQ_HALF_AH:g} Ah", "mV/Ah",
        "Differential voltage at an equal charge removed: steeper when less active material takes part.",
        "lab", ["dubarry2022", "sugumaran2024"], rules=("c5_complete",)),
    # ── B2: CC/CV charges of the degradation cycles (lab, ~4 per block) ────────
    **{f"chg_V_at_{a:g}Ah".replace(".", "p"): _cat(
        "charge", f"V at {a:g} Ah charged (CC phase, 5.4 A)", "V",
        "Charging voltage at an equal charge returned: rises with charge resistance.", "lab",
        ["electronics2023"], rules=("charge_complete",)) for a in CHG_V_AT_AH},
    "chg_cc_dVdQ_mV_per_Ah": _cat(
        "charge", f"slope of V against Ah charged between {CHG_V_AT_AH[0]:g} and {CHG_V_AT_AH[1]:g} Ah", "mV/Ah",
        "Voltage rise per Ah in the CC phase.", "lab", ["electronics2023"], rules=("charge_complete",)),
    "chg_cc_share": _cat(
        "charge", f"Ah charged before the current leaves the CC level (V >= {CHG_CV_V:g} V) / total Ah charged", "-",
        "Share of the charge accepted at full current: falls when charge acceptance drops.", "lab",
        ["electronics2023", "jiang2022"], rules=("charge_complete",)),
    "chg_cv_tau_s": _cat(
        "charge", "time constant of an exponential fit I = I_inf + A exp(-t/tau) to the CV current", "s",
        "How slowly the last charge goes in.", "lab", ["electronics2023"],
        rules=("charge_complete", "fit_rmse_current")),
    "chg_factor": _cat(
        "charge", "Ah charged / Ah of the discharge just before", "-",
        "Charge returned per Ah taken out (1.0 = no overcharge). The protocol's low value is the basis "
        "of the undercharge/sulphation hypothesis.", "lab", ["jiang2022"], rules=("charge_complete",)),
    # ── B3: relaxation in the 30 min rest after each SoC step-down (lab) ───────
    "rest_relax_amp_mV": _cat(
        "rest", "V(end of the 1800 s rest) - V(first sample, ~10 s)", "mV",
        "OCV relaxation after a -3.6 A step-down, at a known charge removed.", "lab",
        ["zhu2022", "jiang2022"], rules=("rest_complete",)),
    "rest_relax_logslope_mV_per_dec": _cat(
        "rest", "slope of V against log10(t) over the rest", "mV/decade",
        "Relaxation speed after the step-down.", "lab", ["zhu2022"], rules=("rest_complete",)),
    "rest_relax_t63_s": _cat(
        "rest", "time until V has covered 63 % of its rise over the rest (model-free)", "s",
        "Relaxation time. A one-exponential fit leaves more than one ADC step of residual, "
        "so no time constant is fitted.", "lab", ["zhu2022"], rules=("rest_complete",)),
}

FIELD_FEATURES = [k for k, v in DETAILED_CATALOG.items() if v["availability"] == "field" and not k.endswith("_rmse_mV")]
LAB_SOURCES = ("c5", "charge", "rest")


def features_of(source: str, with_diagnostics: bool = False) -> list:
    return [k for k, v in DETAILED_CATALOG.items() if v["source"] == source
            and (with_diagnostics or not k.endswith("_rmse_mV"))]


# ── Small signal helpers ─────────────────────────────────────────────────────

def _ref(prev: pd.DataFrame | None) -> tuple:
    if prev is None or prev.empty:
        return None, None
    t = prev["Elapsed_s"].to_numpy(float)
    m = t >= t.max() - REF_TAIL_S
    return float(prev["Voltage"].to_numpy(float)[m].mean()), float(prev["Current"].to_numpy(float)[m].mean())


def _end(g: pd.DataFrame) -> tuple:
    t = g["Elapsed_s"].to_numpy(float)
    m = t >= t.max() - END_WIN_S
    return float(g["Voltage"].to_numpy(float)[m].mean()), float(g["Current"].to_numpy(float)[m].mean())


def step_split(g: pd.DataFrame, prev: pd.DataFrame | None) -> dict:
    """R0 at the largest current step within R0_SEARCH_S, Rend over the last
    END_WIN_S, both relative to the last second of the event before."""
    v_ref, i_ref = _ref(prev)
    if v_ref is None or len(g) < 3:
        return {}
    t = g["Elapsed_s"].to_numpy(float) - float(g["Elapsed_s"].min())
    v, i = g["Voltage"].to_numpy(float), g["Current"].to_numpy(float)
    win = t <= R0_SEARCH_S
    if win.sum() < 2:
        return {}
    # Largest voltage deviation, not largest current step: V and I are logged in
    # sequence, and the first high-current sample can still carry the old voltage.
    j = int(np.argmax(np.abs(v[win] - v_ref)))
    d_i0 = i[win][j] - i_ref
    v_end, i_end = _end(g)
    d_ie = i_end - i_ref
    out = {"_n_head": int(win.sum())}
    if abs(d_i0) >= v2feat.DELTA_I_COMPUTE_MIN_A * 0.5:
        out["R0_mohm"] = (v[win][j] - v_ref) / d_i0 * 1000
    if abs(d_ie) >= v2feat.DELTA_I_COMPUTE_MIN_A * 0.5:
        out["Rend_mohm"] = (v_end - v_ref) / d_ie * 1000
    if "R0_mohm" in out and "Rend_mohm" in out:
        out["Rpol_mohm"] = out["Rend_mohm"] - out["R0_mohm"]
        out["Rpol_ratio"] = out["Rpol_mohm"] / out["R0_mohm"] if out["R0_mohm"] > 0 else np.nan
    return out


def _rc_model(t, v0, r0, r1, tau, k, d_i):
    return v0 + d_i * (r0 + r1 * (1 - np.exp(-t / tau))) + k * t


def ecm_fit(g: pd.DataFrame, prev: pd.DataFrame | None) -> dict:
    """1-RC fit with linear drift over a constant-current step. Resistances in
    mOhm, tau in s, rmse in mV, plus 'at_bound' when tau hit a fit bound."""
    v_ref, i_ref = _ref(prev)
    if v_ref is None:
        return {}
    t = g["Elapsed_s"].to_numpy(float) - float(g["Elapsed_s"].min())
    v, i = g["Voltage"].to_numpy(float), g["Current"].to_numpy(float)
    m = t >= CURRENT_RISE_S
    if m.sum() < 10:
        return {}
    d_i = float(np.median(i[m])) - i_ref
    if abs(d_i) < 0.5:
        return {}
    tt, vv = t[m], v[m]
    tau_lo, tau_hi = 0.2, max(1.0, tt.max())
    f = lambda tt_, r0, r1, tau, k: _rc_model(tt_, v_ref, r0, r1, tau, k, d_i)  # noqa: E731
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            p, _ = curve_fit(f, tt, vv, p0=[0.03, 0.02, min(10.0, tau_hi / 3), 0.0],
                             bounds=([0, 0, tau_lo, -0.01], [1.0, 2.0, tau_hi, 0.01]), maxfev=5000)
        except (RuntimeError, ValueError):
            return {}
    rmse = float(np.sqrt(np.mean((f(tt, *p) - vv) ** 2)))
    at_bound = p[2] <= tau_lo * 1.01 or p[2] >= tau_hi * 0.99
    return {"R0_mohm": p[0] * 1000, "R1_mohm": p[1] * 1000, "tau_s": p[2], "rmse_mV": rmse * 1000,
            "_at_bound": bool(at_bound), "_curve": (tt, f(tt, *p))}


def _two_exp(t, vinf, a1, tau1, a2, tau2):
    return vinf - a1 * np.exp(-t / tau1) - a2 * np.exp(-t / tau2)


def relaxation(g: pd.DataFrame) -> dict:
    """Log-time slope, amplitude and a two-exponential fit of a rest."""
    t = g["Elapsed_s"].to_numpy(float) - float(g["Elapsed_s"].min())
    v = g["Voltage"].to_numpy(float)
    if len(t) < 20:
        return {}
    out = {}
    lw = (t >= RELAX_LOG_FROM_S) & (t <= RELAX_LOG_TO_S)
    if lw.sum() >= 10:
        out["logslope_mV_per_dec"] = float(np.polyfit(np.log10(t[lw]), v[lw], 1)[0]) * 1000
    v05 = float(v[np.argmin(np.abs(t - 0.5))])
    out["amp_mV"] = (float(v[-1]) - v05) * 1000
    # Weigh the early part: resample on a log grid, so 600 s at 20 Hz don't drown the first seconds.
    m = t >= 0.1
    grid = np.unique(np.searchsorted(t[m], np.logspace(-1, np.log10(t[m].max()), 200)).clip(0, m.sum() - 1))
    tt, vv = t[m][grid], v[m][grid]
    tmax = float(tt.max())
    bounds = ([vv.min() - 0.5, 0, 0.1, 0, 5.0], [vv.max() + 0.5, 2.0, 10.0, 2.0, max(10.0, 5 * tmax)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            p, _ = curve_fit(_two_exp, tt, vv, p0=[vv[-1], 0.05, 2.0, 0.1, tmax / 3], bounds=bounds, maxfev=10000)
        except (RuntimeError, ValueError):
            return out
    rmse = float(np.sqrt(np.mean((_two_exp(tt, *p) - vv) ** 2)))
    out.update({"tau1_s": p[2], "tau2_s": p[4], "a2_share": p[3] / (p[1] + p[3]) if p[1] + p[3] > 0 else np.nan,
                "_rmse_mV": rmse * 1000, "_curve": (tt, _two_exp(tt, *p)),
                "_at_bound": bool(p[2] <= 0.101 or p[2] >= 9.9 or p[4] <= 5.05 or p[4] >= bounds[1][4] * 0.99)})
    return out


# ── Field (test-day) features ────────────────────────────────────────────────

def run_features(df: pd.DataFrame, sp: np.ndarray | None = None) -> dict:
    """Detailed field features of one test-day run, plus '<name>__invalid_reason'
    entries for this module's own rules (fit quality, charge tracking). The
    parent feature's reason is added later, by attach_run_validity().
    df: the bdps log sorted by time; sp: setpoint per row (for charge tracking)."""
    occ = v2feat.event_occurrences(df)
    by_key = {(e, o): g for e, o, g in occ}
    prev_of = {}
    last = None
    for e, o, g in occ:
        prev_of[(e, o)] = last
        last = g
    feats, reasons = {}, {}

    def put(name, value, reason=""):
        if value is None or (isinstance(value, float) and not np.isfinite(value)):
            return
        feats[name] = float(value)
        if reason:
            reasons[name] = reason

    for prefix, key in (("wakeup1", ("wakeup_load", 1)), ("glow", ("glow_plug_like_load", 1)),
                        ("crank_cold", ("crank_pulse", 1)), ("crank_hot", ("crank_pulse", 2))):
        if key not in by_key:
            continue
        s = step_split(by_key[key], prev_of[key])
        few = "fewer than 2 samples in the R0 window" if s.get("_n_head", 0) < 2 else ""
        for k in ("R0_mohm", "Rend_mohm", "Rpol_mohm", "Rpol_ratio"):
            put(f"{prefix}_{k}", s.get(k), few if k == "R0_mohm" else "")

    # Cold crank: resistance growth over the -38 A plateau.
    key = ("crank_pulse", 1)
    if key in by_key:
        g = by_key[key]
        v_ref, i_ref = _ref(prev_of[key])
        t = g["Elapsed_s"].to_numpy(float) - float(g["Elapsed_s"].min())
        m = t >= CRANK_SUSTAIN_FROM_S
        if v_ref is not None and m.sum() >= 10:
            d_i = g["Current"].to_numpy(float)[m] - i_ref
            ok = np.abs(d_i) > 1.0
            if ok.sum() >= 10:
                r = (g["Voltage"].to_numpy(float)[m][ok] - v_ref) / d_i[ok]
                put("crank_cold_dRdt_mohm_per_s", np.polyfit(t[m][ok], r, 1)[0] * 1000)

    for prefix, key in (("glow", ("glow_plug_like_load", 1)), ("wakeup1", ("wakeup_load", 1))):
        if key not in by_key:
            continue
        fit = ecm_fit(by_key[key], prev_of[key])
        if not fit:
            continue
        reason = ""
        if fit["rmse_mV"] > FIT_RMSE_MAX_MV:
            reason = f"fit RMSE {fit['rmse_mV']:.1f} mV > {FIT_RMSE_MAX_MV:g} mV"
        elif fit["_at_bound"]:
            reason = "tau at a fit bound"
        for k in ("R0_mohm", "R1_mohm", "tau_s"):
            put(f"{prefix}_ecm_{k}", fit[k], reason)
        put(f"{prefix}_ecm_rmse_mV", fit["rmse_mV"])

    for lbl, occ_n in (("cold", 1), ("ramp", 2), ("hot", 3)):
        key = ("recovery_rest", occ_n)
        if key not in by_key:
            continue
        rx = relaxation(by_key[key])
        fit_reason = ""
        if "_rmse_mV" in rx and rx["_rmse_mV"] > FIT_RMSE_MAX_MV:
            fit_reason = f"fit RMSE {rx['_rmse_mV']:.1f} mV > {FIT_RMSE_MAX_MV:g} mV"
        elif rx.get("_at_bound"):
            fit_reason = "a time constant at a fit bound"
        for k in ("logslope_mV_per_dec", "amp_mV"):
            put(f"relax_{lbl}_{k}", rx.get(k))
        for k in ("tau1_s", "tau2_s", "a2_share"):
            put(f"relax_{lbl}_{k}", rx.get(k), fit_reason)

    key = ("alternator_charge", 1)
    if key in by_key:
        g = by_key[key]
        track = _charge_tracking_reason(g, sp, df)
        s = step_split(g, prev_of[key])
        put("alt_R0_mohm", s.get("R0_mohm"), track)
        t = g["Elapsed_s"].to_numpy(float) - float(g["Elapsed_s"].min())
        m = (t >= CURRENT_RISE_S) & (t <= ALT_SLOPE_S)
        if m.sum() >= 10:
            put("alt_dVdt_mV_per_min", np.polyfit(t[m], g["Voltage"].to_numpy(float)[m], 1)[0] * 60_000, track)
        if "alt_R0_mohm" in feats and feats.get("crank_cold_R0_mohm", 0) > 0:
            put("alt_R0_over_crank_R0", feats["alt_R0_mohm"] / feats["crank_cold_R0_mohm"], track)

    out = dict(feats)
    for k, r in reasons.items():
        out[k + "__own_reason"] = r
    return out


def _charge_tracking_reason(g: pd.DataFrame, sp, df: pd.DataFrame) -> str:
    """'' when the charge current followed the setpoint (median within 10 %)."""
    if sp is None:
        return ""
    s = pd.Series(sp, index=df.index).loc[g.index].to_numpy(float)
    i = g["Current"].to_numpy(float)
    m = s > v2feat.SETPOINT_ZERO_A
    if m.sum() < v2feat.MIN_SINK_SAMPLES:
        return ""
    sp_med, meas_med = float(np.median(s[m])), float(np.median(i[m]))
    if abs(meas_med - sp_med) > max(v2feat.TRACKING_TOL_MIN_A, TRACK_TOL_FRAC * sp_med):
        return f"charge current {meas_med:.2f} A vs setpoint {sp_med:.2f} A (voltage ceiling)"
    return ""


def attach_run_validity(df: pd.DataFrame, suffix: str = "__invalid_reason") -> pd.DataFrame:
    """Combine each field feature's own reason with its parent's reason (the
    parent's '<parent>__invalid_reason' column must already be in df). Adds
    '<feature><suffix>' columns ('' = valid) and drops the '__own_reason' ones."""
    df = df.copy()
    for name in FIELD_FEATURES + [k for k in DETAILED_CATALOG if k.endswith("_rmse_mV")]:
        if name not in df.columns:
            continue
        e = DETAILED_CATALOG[name]
        reason = df.get(name + "__own_reason", pd.Series("", index=df.index)).fillna("").astype(str)
        parents = e["parent"] if isinstance(e["parent"], tuple) else ((e["parent"],) if e["parent"] else ())
        for p in parents:
            par = df.get(p + suffix, pd.Series("", index=df.index)).fillna("").astype(str)
            par = par.where(par == "", "parent " + p + ": " + par)
            reason = (reason + "; " + par).str.strip("; ")
        df[name + suffix] = reason
    return df.drop(columns=[c for c in df.columns if c.endswith("__own_reason")])


# ── Lab features: C/5, charges, rests ────────────────────────────────────────

_C5_PAT = re.compile(r"^Block_(\d+)_SOH_C5_bdps_.*\.csv$")
_CHG_PAT = re.compile(r"^Block_(\d+)_Degr_(\d+)_(charge|discharge)_bdps_(.*)\.csv$")
_REST_PAT = re.compile(r"^Block_(\d+)_SoC(\d+)_OCV(\d+p\d+)V_rest_bdps_(.*)\.csv$")


def c5_features(d: pd.DataFrame) -> dict:
    """Start-of-C/5 features. d: the C/5 bdps log."""
    d = d.sort_values("Elapsed_s")
    t = d["Elapsed_s"].to_numpy(float)
    v, i = d["Voltage"].to_numpy(float), d["Current"].to_numpy(float)
    load = np.abs(i) > 1.0
    if load.sum() < 120:
        return {}
    t0 = float(t[np.argmax(load)])
    tt = t - t0
    i_load = float(np.median(np.abs(i[load])))
    out = {"_reached_cutoff": bool(v.min() <= v2feat.SOH_CUTOFF_V)}
    j1 = int(np.argmax(load))   # first sample under load (1 Hz: up to ~1 s after the step)
    if j1 > 0:                  # the sample before it is the rest voltage
        out["c5_R_1s_mohm"] = (float(v[j1 - 1]) - float(v[j1])) / i_load * 1000
    # Some C/5 logs carry +-60 mV of noise for minutes (Blocks 3 and 5): smooth
    # with a running median, and record the noise so such a block can be rejected.
    vs = pd.Series(v).rolling(C5_SMOOTH_N, center=True, min_periods=1).median().to_numpy()
    head = (tt >= 0) & (tt <= CDF_SEARCH_S)
    out["_noise_mV"] = float(np.median(np.abs(v[head] - vs[head]))) * 1000 * 1.4826
    out["c5_drop_60s_mV"] = (float(v[j1]) - float(np.interp(60.0, tt, vs))) * 1000
    w = (tt >= 0) & (tt <= CDF_SEARCH_S)
    vw, tw = vs[w], tt[w]
    # Ignore the first 5 s (the ohmic drop itself) when looking for the trough.
    after = tw >= 5.0
    k = int(np.argmin(vw[after]))
    trough, t_trough = float(vw[after][k]), float(tw[after][k])
    later = tw > t_trough
    if later.any():
        kk = int(np.argmax(vw[later]))
        plateau, t_plat = float(vw[later][kk]), float(tw[later][kk])
        out["c5_cdf_depth_mV"] = max(0.0, plateau - trough) * 1000
        out["_cdf_present"] = plateau - trough >= CDF_MIN_DEPTH_V
        out["c5_cdf_time_s"] = t_plat - t_trough
    else:
        out["c5_cdf_depth_mV"] = 0.0
        out["_cdf_present"] = False
    ah = np.concatenate([[0.0], np.cumsum(np.abs(i[1:]) * np.clip(np.diff(t), 0, 10))]) / 3600
    m = (ah >= C5_EQUAL_AH - C5_DVDQ_HALF_AH) & (ah <= C5_EQUAL_AH + C5_DVDQ_HALF_AH)
    if m.sum() >= 30:
        co = np.polyfit(ah[m], vs[m], 1)
        out["c5_dVdQ_at3Ah_mV_per_Ah"] = float(co[0]) * 1000
        out["_dvdq_rmse_mV"] = float(np.sqrt(np.mean((np.polyval(co, ah[m]) - vs[m]) ** 2))) * 1000
    # A discharge at constant current only falls after the coup de fouet. A rise
    # of the smoothed voltage later on is a disturbance (seen in Blocks 3-6, 11),
    # and a dip-and-rise at the start can then not be told apart from one.
    late = (tt > CDF_SEARCH_S) & (tt <= 3 * CDF_SEARCH_S)
    if late.sum() > 10:
        vl = vs[late]
        out["_late_rise_mV"] = float(np.max(vl - np.minimum.accumulate(vl))) * 1000
    return out


def charge_features(c: pd.DataFrame, discharge_ah: float | None) -> dict:
    """CC/CV charge features. c: the Degr charge bdps log."""
    c = c.sort_values("Elapsed_s")
    t = c["Elapsed_s"].to_numpy(float)
    v, i = c["Voltage"].to_numpy(float), c["Current"].to_numpy(float)
    ah = np.concatenate([[0.0], np.cumsum(np.clip(i[1:], 0, None) * np.clip(np.diff(t), 0, 10))]) / 3600
    total = float(ah[-1])
    out = {"_ah_charged": total, "_ended_taper": bool(i[-1] < 0.5)}
    for a in CHG_V_AT_AH:
        if total > a:
            out[f"chg_V_at_{a:g}Ah".replace(".", "p")] = float(np.interp(a, ah, v))
    m = (ah >= CHG_V_AT_AH[0]) & (ah <= CHG_V_AT_AH[1]) & (v < CHG_CV_V)
    if m.sum() >= 30:
        out["chg_cc_dVdQ_mV_per_Ah"] = float(np.polyfit(ah[m], v[m], 1)[0]) * 1000
    # The CV phase starts when the current leaves the CC level, not when the
    # voltage first touches the ceiling (it does so while still at 5.4 A).
    i_cc = float(np.median(i[: max(10, len(i) // 10)]))
    cv = (v >= CHG_CV_V) & (i < 0.95 * i_cc)
    if cv.any() and total > 0:
        k = int(np.argmax(cv))
        out["chg_cc_share"] = float(ah[k]) / total
        tc, ic = t[k:] - t[k], i[k:]
        if len(tc) >= 60:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    p, _ = curve_fit(lambda x, a, tau, c0: c0 + a * np.exp(-x / tau), tc, ic,
                                     p0=[ic[0], 1500.0, 0.3], bounds=([0, 10, 0], [10, 50_000, 2]), maxfev=5000)
                    out["chg_cv_tau_s"] = float(p[1])
                    out["_cv_rmse_A"] = float(np.sqrt(np.mean((p[2] + p[0] * np.exp(-tc / p[1]) - ic) ** 2)))
                except (RuntimeError, ValueError):
                    pass
    if discharge_ah and discharge_ah > 0.5:
        out["chg_factor"] = total / discharge_ah
    return out


def rest_features(d: pd.DataFrame) -> dict:
    """Relaxation of a 30 min rest after a step-down (0.1 Hz)."""
    d = d.sort_values("Elapsed_s")
    t = d["Elapsed_s"].to_numpy(float)
    v = d["Voltage"].to_numpy(float)
    if len(t) < 60:
        return {}
    t = t - t[0] + max(1.0, float(np.median(np.diff(t))))  # first sample ~one interval after the step
    amp = float(v[-1]) - float(v[0])
    out = {"_duration_s": float(t[-1]), "_v_end": float(v[-1]),
           "rest_relax_amp_mV": amp * 1000,
           "rest_relax_logslope_mV_per_dec": float(np.polyfit(np.log10(t), v, 1)[0]) * 1000}
    # Model-free: one exponential leaves > 1 ADC step of residual on most rests
    # (the relaxation is closer to linear in log t), so no time constant is fitted.
    if amp > 2 * CDF_MIN_DEPTH_V:
        k = int(np.argmax(v - v[0] >= 0.63 * amp))
        out["rest_relax_t63_s"] = float(t[k])
    return out


def _cached(tag: str, path: str, fn):
    return v2feat.cached_call(f"{CACHE_VERSION}_{tag}", (path, v2feat._fingerprint(path)), fn)


def build_c5_table(log_dir: str) -> pd.DataFrame:
    rows = []
    for fn in sorted(os.listdir(log_dir)):
        m = _C5_PAT.match(fn)
        if not m:
            continue
        path = os.path.join(log_dir, fn)
        f = _cached("c5", path, lambda: c5_features(pd.read_csv(path, on_bad_lines="skip")))
        if not f:
            continue
        r = {"block": int(m.group(1)), "file": fn}
        reason = "" if f["_reached_cutoff"] else "C/5 did not reach the cutoff (incomplete)"
        if not reason and f.get("_noise_mV", 0) > C5_NOISE_MAX_MV:
            reason = f"voltage noise {f['_noise_mV']:.0f} mV in the first {CDF_SEARCH_S:g} s"
        r["c5_noise_mV"] = f.get("_noise_mV")
        r["c5_late_rise_mV"] = f.get("_late_rise_mV")
        irregular = (f"voltage rises {f['_late_rise_mV']:.0f} mV later in the discharge (disturbance)"
                     if f.get("_late_rise_mV", 0) > CDF_MIN_DEPTH_V * 1000 else "")
        for k in features_of("c5"):
            if k in f:
                r[k] = f[k]
                rr = reason
                if k.startswith("c5_cdf"):
                    rr = rr or irregular
                if k == "c5_cdf_time_s" and not f.get("_cdf_present"):
                    rr = rr or f"no coup de fouet (depth < {CDF_MIN_DEPTH_V*1000:g} mV)"
                if k == "c5_dVdQ_at3Ah_mV_per_Ah" and f.get("_dvdq_rmse_mV", 0) > FIT_RMSE_MAX_MV:
                    rr = rr or f"irregular voltage: linear-fit RMSE {f['_dvdq_rmse_mV']:.0f} mV"
                r[k + "__invalid_reason"] = rr
        rows.append(r)
    out = pd.DataFrame(rows)
    return out.drop_duplicates("block", keep="last") if not out.empty else out


def build_charge_table(log_dir: str) -> pd.DataFrame:
    files = {}
    for fn in sorted(os.listdir(log_dir)):
        m = _CHG_PAT.match(fn)
        if m:
            files[(int(m.group(1)), int(m.group(2)), m.group(3))] = fn  # sorted: a retry wins
    rows = []
    for (b, k, kind), fn in sorted(files.items()):
        if kind != "charge":
            continue
        path = os.path.join(log_dir, fn)
        if os.path.getsize(path) < CHG_MIN_BYTES:
            continue
        dis_fn = files.get((b, k, "discharge"))
        dis_ah = None
        if dis_fn:
            dp = os.path.join(log_dir, dis_fn)
            dis_ah = _cached("dis_ah", dp, lambda: _discharge_ah(dp))
        f = _cached("charge", path, lambda: charge_features(pd.read_csv(path, on_bad_lines="skip"), dis_ah))
        if not f:
            continue
        r = {"block": b, "cycle": k, "file": fn, "ah_charged": f["_ah_charged"], "discharge_ah": dis_ah}
        reason = "" if f["_ended_taper"] else "charge did not end in the CV taper"
        if k == 1:
            reason = reason or "cycle 1 follows the SoC sweep, not a full discharge"
        for name in features_of("charge"):
            if name in f:
                r[name] = f[name]
                rr = reason
                if name == "chg_cv_tau_s" and f.get("_cv_rmse_A", 0) > 0.1:
                    rr = rr or f"CV fit RMSE {f['_cv_rmse_A']:.2f} A"
                r[name + "__invalid_reason"] = rr
        rows.append(r)
    return pd.DataFrame(rows)


def _discharge_ah(path: str) -> float:
    d = pd.read_csv(path, on_bad_lines="skip").sort_values("Elapsed_s")
    if len(d) < 2:
        return 0.0
    t, i = d["Elapsed_s"].to_numpy(float), d["Current"].to_numpy(float)
    return float(np.sum(np.abs(np.clip(i[1:], None, 0)) * np.clip(np.diff(t), 0, 10)) / 3600)


def build_rest_table(log_dir: str, sweep: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per post-step-down rest, with the charge removed at that point
    (from _sweep_points.csv) and the block's C/5 as 'cap'."""
    rows = []
    for fn in sorted(os.listdir(log_dir)):
        m = _REST_PAT.match(fn)
        if not m:
            continue
        path = os.path.join(log_dir, fn)
        f = _cached("rest", path, lambda: rest_features(pd.read_csv(path, on_bad_lines="skip")))
        if not f:
            continue
        r = {"block": int(m.group(1)), "target_soc_pct": int(m.group(2)), "file": fn,
             "rest_ocv_end_V": f["_v_end"]}
        reason = "" if f["_duration_s"] >= 1500 else f"rest only {f['_duration_s']:.0f} s"
        if int(m.group(2)) == 100:
            reason = "follows the full charge, not a step-down (relaxes downward)"
        for name in features_of("rest"):
            if name in f:
                r[name] = f[name]
                r[name + "__invalid_reason"] = reason
        rows.append(r)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.drop_duplicates(["block", "target_soc_pct"], keep="last")
    if sweep is not None and not sweep.empty:
        sp = sweep.groupby(["block", "target_soc_pct"], as_index=False).last()
        out = out.merge(sp[["block", "target_soc_pct", "removed_ah_before_run"]],
                        on=["block", "target_soc_pct"], how="left").rename(
                            columns={"removed_ah_before_run": "removed_ah"})
    return out


def mask(df: pd.DataFrame, suffix: str = "__invalid_reason") -> pd.DataFrame:
    """Invalid values to NaN (same convention as the dashboard's mask_invalid)."""
    out = df.copy()
    for col in df.columns:
        if col.endswith(suffix) and col[: -len(suffix)] in out.columns:
            out.loc[out[col].fillna("") != "", col[: -len(suffix)]] = np.nan
    return out


def catalog_markdown() -> str:
    rows = ["| Feature | Source | Availability | Formula | Unit | Validity | Literature |",
            "|---|---|---|---|---|---|---|"]
    for k, e in DETAILED_CATALOG.items():
        val = ", ".join(([f"parent `{e['parent']}`"] if e["parent"] else []) + list(e["rules"])) or "-"
        rows.append(f"| `{k}` | {e['source']} | {e['availability']} | {e['formula']} | {e['unit']} | {val} | "
                    f"{', '.join(e['lit'])} |")
    return "\n".join(rows)
