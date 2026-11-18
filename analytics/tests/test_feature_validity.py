"""Per-feature validity rules (testday_v2_features.feature_validity)."""

import os

import numpy as np
import pandas as pd
import pytest

import testday_v2_features as v2feat
from conftest import PROFILE_FILES, UNIT3_DIR, synthetic_run, unit3_run

ALWAYS = {"wakeup_load_2_R_int_mohm"}  # rule "never"
CRANK_COLD_R = "crank_cold_R_int_deltaI_mohm"


def _validity(df, sp):
    out = v2feat.feature_validity(df, sp)
    return {k: v for k, v in out.items() if k not in ALWAYS}


def _event_rows(df, etype, occ):
    for t, o, g in v2feat.event_occurrences(df):
        if (t, o) == (etype, occ):
            return g.index
    raise KeyError((etype, occ))


# ── synthetic runs ───────────────────────────────────────────────────────────

def test_healthy_run_is_valid():
    df, sp = synthetic_run()
    assert _validity(df, sp) == {}
    assert "never" in v2feat.FEATURE_CATALOG["wakeup_load_2_R_int_mohm"]["rules"]
    assert "wakeup_load_2_R_int_mohm" in v2feat.feature_validity(df, sp)


def test_floor_limited_crank_voids_current_vmin_and_resistance_not_vpre():
    # OCV 11.6 V, 35 mOhm: the glow load leaves 11.32 V, the 60 A crank would
    # need 9.5 V, so the sink cuts the current to hold 10.2 V.
    df, sp = synthetic_run(ocv=11.6, floor=10.2)
    bad = _validity(df, sp)
    for f in (CRANK_COLD_R, "crank_cold_R_int_apparent_mohm", "crank_cold_V_min", "crank_cold_I_peak_A"):
        assert "I_peak" in bad.get(f, ""), f
    assert "crank_cold_V_pre" not in bad
    assert "rest_baseline_Vmean" not in bad


def test_vpre_at_floor_voids_vpre():
    df, sp = synthetic_run()
    glow = _event_rows(df, "glow_plug_like_load", 1)
    df.loc[glow, "Voltage"] = 10.25
    bad = _validity(df, sp)
    assert "within 0.3 V" in bad["crank_cold_V_pre"]
    assert "within 0.3 V" in bad[CRANK_COLD_R]


def test_small_current_step_voids_resistance_only():
    # Peak current fine (55 A, inrush only, so the event median still tracks),
    # but the glow load already drew 45 A: the step is ~10 A of the commanded ~50 A.
    df, sp = synthetic_run()
    glow = _event_rows(df, "glow_plug_like_load", 1)
    df.loc[glow, "Current"] = -45.0
    crank = _event_rows(df, "crank_pulse", 1)
    t = df.loc[crank, "Elapsed_s"]
    head = crank[(t <= t.min() + 0.3).to_numpy()]
    df.loc[head, "Current"] = -55.0
    bad = _validity(df, sp)
    assert "commanded step" in bad[CRANK_COLD_R]
    assert "crank_cold_V_min" not in bad
    assert "crank_cold_I_peak_A" not in bad


def test_hot_crank_short_setpoint_peak_is_not_a_failure():
    """-55 A lasts 0.05 s, shorter than the 0.1 s current rise: never reachable."""
    df, sp = synthetic_run()
    crank = _event_rows(df, "crank_pulse", 2)
    head = df.loc[crank, "Elapsed_s"] < df.loc[crank, "Elapsed_s"].min() + 0.1
    df.loc[crank[head.to_numpy()], "Current"] = -1.0  # still rising
    assert "crank_hot_R_int_deltaI_mohm" not in _validity(df, sp)


def test_few_samples_in_window():
    df, sp = synthetic_run()
    crank = _event_rows(df, "crank_pulse", 1)
    t0 = df.loc[crank, "Elapsed_s"].min()
    drop = crank[(df.loc[crank, "Elapsed_s"] > t0).to_numpy() & (df.loc[crank, "Elapsed_s"] <= t0 + 0.3).to_numpy()]
    keep = ~df.index.isin(drop)
    bad = _validity(df[keep].reset_index(drop=True), sp[keep])
    assert "window 'first 0.3 s'" in bad["crank_cold_V_min"]


def test_recovery_follows_its_crank():
    df, sp = synthetic_run(ocv=11.6, floor=10.2)
    bad = _validity(df, sp)
    assert "crank_pulse #1" in bad["recovery_after_cold_V_plus30s"]


def test_tracking_rule_on_whole_event_voltage():
    df, sp = synthetic_run()
    glow = _event_rows(df, "glow_plug_like_load", 1)
    df.loc[glow, "Current"] = -2.0   # sink not following, voltage far from the floor
    bad = _validity(df, sp)
    assert "event failed" in bad["glow_plug_like_load_1_Vmean"]


def test_missing_event_is_reported():
    df, sp = synthetic_run()
    keep = (df["Event_Index"] < 11).to_numpy()  # stop before the hot crank
    bad = v2feat.feature_validity(df[keep].reset_index(drop=True), sp[keep])
    assert "missing" in bad["crank_hot_V_min"]


# ── real ul18_12_unit3 runs, Block 4 ─────────────────────────────────────────

def test_unit3_block4_soc10_cold_crank_resistance_invalid():
    df, sp = unit3_run(4, 10)
    bad = _validity(df, sp)
    assert "I_peak" in bad[CRANK_COLD_R]
    assert "crank_cold_V_min" in bad
    assert "crank_cold_V_pre" not in bad          # 10.55 V, above the margin
    assert "rest_baseline_Vmean" not in bad
    assert "wakeup_load_1_sag_V" not in bad


def test_unit3_block4_soc20_all_valid():
    df, sp = unit3_run(4, 20)
    assert _validity(df, sp) == {}


# ── whole campaign: the controls from the work brief ─────────────────────────

def test_unit3_campaign_controls():
    if not os.path.isdir(UNIT3_DIR):
        pytest.skip("unit3 data not available")
    profiles = v2feat.load_profiles(PROFILE_FILES)
    infos = [i for i in v2feat.find_v2_testday_runs(UNIT3_DIR) if i["block"] and i["block"] > 1 and i["target_soc"]]

    def one(info):
        df = pd.read_csv(info["bdps_path"]).sort_values("Elapsed_s").reset_index(drop=True)
        prof = profiles.get(str(df["Profile_Hash"].iloc[0]))
        sp = v2feat.setpoint_at(df["Elapsed_s"].to_numpy(), prof[0], prof[1])
        return info["block"], info["target_soc"], _validity(df, sp)

    results = v2feat.parallel_map(one, infos)
    for block, soc, bad in results:
        if soc >= 20:
            assert bad == {}, (block, soc, bad)
        elif block in (3, 4, 5, 10):
            assert CRANK_COLD_R in bad, block
        elif block in (7, 8, 9):
            for f in ("crank_cold_V_pre", "crank_cold_V_min", CRANK_COLD_R, "crank_cold_I_peak_A"):
                assert f in bad, (block, f)
