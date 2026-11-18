"""detailed_features: curve features on synthetic signals with a known answer,
and on real unit3 files when the data folder is present."""

import os

import numpy as np
import pandas as pd
import pytest

import detailed_features as dfm
import testday_v2_features as v2feat
from conftest import UNIT3_DIR, synthetic_run, unit3_run


def _event(t, v, i, etype, idx):
    return pd.DataFrame({"Elapsed_s": t, "Voltage": v, "Current": i, "Event_Type": etype, "Event_Index": idx})


def _rc_step(r0=0.04, r1=0.02, tau=3.0, d_i=-8.0, v0=12.7, dur=45.0, dt=0.05):
    pre_t = np.arange(0, 5, dt)
    pre = _event(pre_t, np.full_like(pre_t, v0), np.zeros_like(pre_t), "rest", 0)
    t = np.arange(0, dur, dt)
    v = v0 + d_i * (r0 + r1 * (1 - np.exp(-t / tau)))
    step = _event(t + 5, v, np.full_like(t, d_i), "glow_plug_like_load", 1)
    return pre, step


def test_ecm_fit_recovers_rc_parameters():
    pre, step = _rc_step()
    fit = dfm.ecm_fit(step, pre)
    assert fit["R0_mohm"] == pytest.approx(40, abs=1.0)
    assert fit["R1_mohm"] == pytest.approx(20, abs=1.0)
    assert fit["tau_s"] == pytest.approx(3.0, rel=0.05)
    assert fit["rmse_mV"] < 0.5 and not fit["_at_bound"]


def test_step_split_ohmic_and_polarisation():
    pre, step = _rc_step(r0=0.04, r1=0.02, tau=3.0, dur=45.0)
    s = dfm.step_split(step, pre)
    assert s["R0_mohm"] == pytest.approx(40 + 20 * (1 - np.exp(-0.5 / 3)), abs=0.5)  # largest |dV| in 0.5 s
    assert s["Rend_mohm"] == pytest.approx(60, abs=0.5)
    assert s["Rpol_mohm"] == pytest.approx(s["Rend_mohm"] - s["R0_mohm"])


def test_relaxation_log_slope_and_two_exponentials():
    t = np.arange(0, 600, 0.05)
    v = 12.7 - 0.10 * np.exp(-t / 2.0) - 0.15 * np.exp(-t / 60.0)
    rx = dfm.relaxation(_event(t, v, np.zeros_like(t), "recovery_rest", 1))
    assert rx["tau1_s"] == pytest.approx(2.0, rel=0.1)
    assert rx["tau2_s"] == pytest.approx(60.0, rel=0.1)
    assert rx["a2_share"] == pytest.approx(0.6, abs=0.03)
    assert rx["logslope_mV_per_dec"] > 0 and rx["amp_mV"] > 0


def test_run_features_on_synthetic_profile_run():
    df, sp = synthetic_run(r_ohm=0.035)
    f = dfm.run_features(df, sp)
    # Ideal battery: no polarisation, every step resistance is the ohmic 35 mOhm.
    for name in ("glow_R0_mohm", "glow_Rend_mohm", "crank_cold_R0_mohm", "crank_hot_Rend_mohm"):
        assert f[name] == pytest.approx(35, abs=1.0), name
    assert abs(f["crank_cold_Rpol_mohm"]) < 1.0
    assert not any(k.startswith("alt_") and k.endswith("__own_reason") for k in f)  # charge follows the setpoint
    # No RC branch on an ideal battery, so the fitted tau is arbitrary and rejected.
    assert "glow_ecm_tau_s__own_reason" in f


def test_parent_validity_is_inherited():
    df = pd.DataFrame({"crank_cold_R0_mohm": [40.0, 40.0], "crank_cold_Rpol_mohm": [5.0, 5.0],
                       "crank_cold_R_int_deltaI_mohm__invalid_reason": ["", "event voltage_limited"],
                       "crank_cold_V_sustained__invalid_reason": ["", ""]})
    out = dfm.attach_run_validity(df)
    assert out["crank_cold_R0_mohm__invalid_reason"].tolist()[0] == ""
    assert "voltage_limited" in out["crank_cold_R0_mohm__invalid_reason"].tolist()[1]
    assert "voltage_limited" in out["crank_cold_Rpol_mohm__invalid_reason"].tolist()[1]


def test_charge_features_on_synthetic_cc_cv():
    t = np.arange(0, 12_000, 1.0)
    i = np.where(t < 6000, 5.4, 0.3 + 5.1 * np.exp(-(t - 6000) / 1500))
    v = np.where(t < 6000, 12.4 + 2.0 * t / 6000, 14.4)
    f = dfm.charge_features(pd.DataFrame({"Elapsed_s": t, "Voltage": v, "Current": i}), discharge_ah=10.0)
    assert f["chg_cv_tau_s"] == pytest.approx(1500, rel=0.02)
    assert 0.6 < f["chg_cc_share"] < 0.8
    assert f["chg_factor"] == pytest.approx(f["_ah_charged"] / 10.0)


def test_catalogue_covers_run_features():
    df, sp = synthetic_run()
    produced = {k for k in dfm.run_features(df, sp) if not k.endswith("__own_reason")}
    assert produced <= set(dfm.DETAILED_CATALOG)
    for name, e in dfm.DETAILED_CATALOG.items():
        assert e["availability"] in ("field", "lab"), name
        assert e["source"] in ("testday",) + dfm.LAB_SOURCES, name
        parents = e["parent"] if isinstance(e["parent"], tuple) else (e["parent"],) if e["parent"] else ()
        for p in parents:
            assert p in v2feat.FEATURE_CATALOG, (name, p)


def test_unit3_block4_soc10_cold_crank_split_inherits_floor_rejection():
    df, sp = unit3_run(4, 10)
    raw = dfm.run_features(df, sp)
    validity = v2feat.feature_validity(df, sp)
    row = pd.DataFrame([{**raw, **{k + "__invalid_reason": r for k, r in validity.items()}}])
    out = dfm.attach_run_validity(row)
    assert out["crank_cold_R0_mohm__invalid_reason"].iloc[0] != ""


@pytest.mark.skipif(not os.path.isdir(UNIT3_DIR), reason="unit3 data folder not available")
def test_unit3_lab_tables():
    c5 = dfm.build_c5_table(UNIT3_DIR)
    assert set(range(1, 12)) <= set(c5["block"])
    ch = dfm.build_charge_table(UNIT3_DIR)
    full = ch[ch["cycle"] >= 2]
    # The protocol returns only 1-3 % more charge than it took out (basis of the
    # undercharge hypothesis in the paper's Discussion).
    assert full["chg_factor"].between(1.0, 1.04).all()
    rs = dfm.build_rest_table(UNIT3_DIR)
    after_step = dfm.mask(rs)[rs["target_soc_pct"] < 100]
    assert after_step["rest_relax_amp_mV"].gt(0).all()
    assert rs.loc[rs["target_soc_pct"] == 100, "rest_relax_amp_mV__invalid_reason"].ne("").all()
