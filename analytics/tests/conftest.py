"""Shared fixtures for the analytics tests.

Run from the repo root or analytics/ with the ddsm environment:
    C:\\Users\\TPKam\\miniconda3\\envs\\ddsm\\python.exe -m pytest analytics/tests -q

Tests on real ul18_12_unit3 runs skip when the (gitignored) data folder is absent.
"""

import glob
import os
import sys

import numpy as np
import pandas as pd
import pytest

ANALYTICS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ANALYTICS_DIR)

import testday_v2_features as v2feat  # noqa: E402

PROFILE_DIR = os.path.join(ANALYTICS_DIR, "..", "lab-control", "LabSetupV2")
PROFILE_CAP60 = os.path.join(PROFILE_DIR, "test_day_profile_v2_cap60A.csv")
PROFILE_FILES = [os.path.join(PROFILE_DIR, f) for f in ("test_day_profile_new.csv", "test_day_profile_v2_cap60A.csv")]
UNIT3_DIR = os.path.join(
    os.environ.get("BATPI_DATA_DIR") or r"C:\Users\TPKam\Documents\BatPi_Download", "LOGBATTEST", "ul18_12_unit3"
)


def synthetic_run(r_ohm: float = 0.035, ocv: float = 12.8, floor: float | None = None,
                  dt: float = 0.05) -> tuple:
    """A run of the real cap60A profile on an ideal battery: V = OCV - R*|I|,
    current exactly the setpoint, sampled every dt seconds. With floor set, the
    sink holds V at the floor by cutting the current, as the BDPS does.
    Returns (df, setpoint array)."""
    p = pd.read_csv(PROFILE_CAP60)
    t = np.arange(0.0, float(p["Time (s)"].iloc[-1]), dt)
    idx = np.clip(np.searchsorted(p["Time (s)"].to_numpy(), t, side="right") - 1, 0, len(p) - 1)
    sp = v2feat.setpoint_at(t, p["Time (s)"].to_numpy(), p["Current (A)"].to_numpy())
    i = sp.copy()
    v = ocv - r_ohm * np.abs(i) * (i < 0) + 0.1 * (i > 0)
    if floor is not None:
        low = v < floor
        i[low] = -(ocv - floor) / r_ohm
        v[low] = floor
    df = pd.DataFrame({
        "Elapsed_s": t, "Voltage": v, "Current": i,
        "Event_Type": p["Event Type"].to_numpy()[idx], "Event_Index": p["Event Index"].to_numpy()[idx],
        "Profile_Hash": "synthetic",
    })
    return df, sp


@pytest.fixture(scope="session")
def unit3_profiles():
    return v2feat.load_profiles(PROFILE_FILES)


def unit3_run(block: int, soc: int):
    """(df sorted by time, setpoint) for the newest unit3 test-day run at that point, or skip."""
    hits = sorted(glob.glob(os.path.join(UNIT3_DIR, f"Block_{block:02d}_SoC{soc:03d}_OCV*_testday_bdps_*.csv")))
    if not hits:
        pytest.skip(f"unit3 data for Block {block} SoC {soc} % not available")
    df = pd.read_csv(hits[-1]).sort_values("Elapsed_s").reset_index(drop=True)
    profiles = v2feat.load_profiles(PROFILE_FILES)
    prof = profiles.get(str(df["Profile_Hash"].iloc[0]))
    assert prof is not None, "profile of this run not found"
    return df, v2feat.setpoint_at(df["Elapsed_s"].to_numpy(), prof[0], prof[1])
