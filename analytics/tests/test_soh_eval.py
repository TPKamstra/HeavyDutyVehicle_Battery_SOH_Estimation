"""soh_eval: the leave-one-block-out evaluation of the paper's feature table."""

import os

import numpy as np
import pandas as pd
import pytest

import soh_eval as se
from conftest import UNIT3_DIR


def _blocks(n_blocks=8, runs=10, noise=0.0, seed=0):
    """Synthetic campaign: SOH falls per block, OCV varies within a block,
    'good' = linear in SOH plus an OCV effect, 'charge' = OCV only."""
    rng = np.random.default_rng(seed)
    rows = []
    for b in range(n_blocks):
        soh = 100 - 7 * b
        for r in range(runs):
            ocv = 12.0 + 0.1 * r
            rows.append({"block": b + 2, "soh": soh, "ocv_window_Vpre": ocv, "target_soc_pct": 10 * (r + 1),
                         "good": 50 - 0.2 * soh + 3 * ocv + noise * rng.normal(),
                         "charge": 2 * ocv + 0.01 * rng.normal()})
    return pd.DataFrame(rows)


def test_perfect_feature_has_zero_lobo_error():
    df = _blocks()
    rmse, err = se.lobo(df, ["good"])
    assert rmse < 1e-6
    assert set(err) == set(df["block"])


def test_charge_only_feature_does_not_beat_ocv_baseline():
    df = _blocks()
    t = se.rank_features(df, ["good", "charge"], min_rows=10)
    good, charge = t.set_index("feature").loc["good"], t.set_index("feature").loc["charge"]
    assert good["lobo_rmse"] < 1e-6 and good["gain"] > 10
    assert charge["r2_ocv"] > 0.99
    assert charge["lobo_rmse"] >= charge["baseline_rmse"] - 1.0


def test_mean_baseline_is_leave_one_out_mean():
    df = _blocks(n_blocks=3, runs=2)       # SOH 100, 93, 86
    expected = np.sqrt(np.mean([(89.5 - 100) ** 2, (93 - 93) ** 2, (96.5 - 86) ** 2]))
    assert se.mean_baseline(df) == pytest.approx(expected)


def test_value_at_charge_interpolates_and_refuses_extrapolation():
    df = pd.DataFrame({"block": [2, 2, 2, 3], "removed_ah": [1.0, 3.0, 5.0, 4.0], "f": [10.0, 30.0, 50.0, 7.0]})
    s = se.value_at_charge(df, "f", 2.0)
    assert s[2] == pytest.approx(20.0)
    assert 3 not in s.index


@pytest.mark.skipif(not os.path.isdir(UNIT3_DIR), reason="unit3 data folder not available")
def test_reproduces_paper_table():
    """The numbers in the paper's tab:features (blocks 2-11, invalid values masked)."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        import battery_feature_dashboard_unit3 as m
    d = m.mask_invalid(m._view())
    d["soh"] = d["block"].map(m._soh_by_block())
    b = se.baselines(d)
    assert round(b["mean"], 1) == 18.8
    assert round(b["ocv_only"], 1) == 17.1
    t = se.rank_features(d, ["crank_cold_R_int_deltaI_mohm", "crank_hot_R_int_deltaI_mohm",
                             "ramp_like_load_1_R_int_est_mohm", "rest_baseline_Vmean"]).set_index("feature")
    assert round(t.loc["crank_cold_R_int_deltaI_mohm", "lobo_rmse"], 1) == 10.1
    assert round(t.loc["ramp_like_load_1_R_int_est_mohm", "lobo_rmse"], 1) == 11.1
    assert round(t.loc["crank_hot_R_int_deltaI_mohm", "lobo_rmse"], 1) == 12.5
    assert round(t.loc["rest_baseline_Vmean", "lobo_rmse"], 1) == 17.1
    assert round(t.loc["crank_cold_R_int_deltaI_mohm", "rho"], 2) == -0.63
