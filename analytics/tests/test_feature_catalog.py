"""FEATURE_CATALOG must describe exactly what extract_features() returns."""

import pytest

import testday_v2_features as v2feat
from conftest import synthetic_run, unit3_run


def _check_entry(name, e):
    assert set(e) >= {"event", "windows", "formula", "unit", "meaning", "ageing", "soc", "rules", "field", "kind"}
    assert e["kind"].split(":")[0] in v2feat.FEATURE_KINDS, name
    if e["kind"].startswith("duplicate_of:"):
        assert e["kind"].split(":", 1)[1] in v2feat.FEATURE_CATALOG, name
    for rule in e["rules"]:
        rule_name, _, target = rule.partition("@")
        assert rule_name in {"tracking", "not_failed", "current_low", "v_pre_floor", "delta_i_small",
                             "i_range_small", "few_samples", "never"}, (name, rule)
        if target:
            etype, occ = target.split(":")
            assert int(occ) >= 1
    if e["event"] is None:
        assert e["rules"] == (), f"{name}: whole-run features have no event to check"


def test_catalog_entries_are_complete():
    for name, e in v2feat.FEATURE_CATALOG.items():
        _check_entry(name, e)


def test_catalog_matches_extract_features_synthetic():
    df, _ = synthetic_run()
    feats = v2feat.extract_features(df)
    assert set(feats) == set(v2feat.FEATURE_CATALOG)


def test_catalog_matches_extract_features_real_run():
    df, _ = unit3_run(4, 20)
    feats = v2feat.extract_features(df)
    assert set(feats) == set(v2feat.FEATURE_CATALOG)


def test_recovery_labels_follow_the_event_before():
    """The rest after the ramp used to overwrite recovery_after_cold."""
    df, _ = synthetic_run()
    occ = v2feat.event_occurrences(df)
    rests = [g for etype, _, g in occ if etype == "recovery_rest"]
    feats = v2feat.extract_features(df)
    for name, g in zip(("cold", "ramp", "hot"), rests):
        expected = v2feat._value_at_offset(g, v2feat.RECOVERY_OFFSET_S)
        assert feats[f"recovery_after_{name}_V_plus30s"] == pytest.approx(expected)


def test_documented_duplicates_are_duplicates():
    df, _ = unit3_run(4, 20)
    feats = v2feat.extract_features(df)
    assert feats["total_ah_charged"] == pytest.approx(feats["alternator_charge_after_cold_Ah"])
    assert feats["crank_hot_R_int_apparent_mohm"] == pytest.approx(feats["crank_hot_R_int_deltaI_mohm"], rel=1e-3)


def test_catalog_markdown_lists_every_feature():
    md = v2feat.catalog_markdown()
    for name in v2feat.FEATURE_CATALOG:
        assert f"`{name}`" in md
