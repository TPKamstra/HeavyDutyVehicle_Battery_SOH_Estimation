"""
combine_two_bdps_logs.py

Combine two BDPS log files from LOGBATTEST_Complete into one CSV.

Default behaviour
- Loads both CSVs
- Adds a source_file column
- Sorts by a detected time axis (elapsed_s, timestamp, time), otherwise keeps file order
- Removes exact duplicate rows
- Writes combined_discharge_c5_2025-12-16_bdps.csv into the same folder
"""

import os
import pandas as pd


BASE_DIR = os.path.dirname(__file__)
LOG_DIR = os.path.join(BASE_DIR, "LOGBATTEST_Complete")

FILE_1 = "discharge_c5_2025-12-16_14-05-57_bdps.csv"
FILE_2 = "discharge_c5_2025-12-16_14-07-12_bdps.csv"

OUT_FILE = "combined_discharge_c5_2025-12-16_bdps.csv"


def _detect_time_col(df: pd.DataFrame) -> str | None:
    cols_lower = {c.lower(): c for c in df.columns}
    for candidate in ["elapsed_s", "timestamp", "time"]:
        if candidate in cols_lower:
            return cols_lower[candidate]
    return None


def _try_parse_datetime(s: pd.Series) -> pd.Series:
    # Safe parse, returns datetime where possible, NaT where not
    return pd.to_datetime(s, errors="coerce")


def combine_two_files() -> str:
    p1 = os.path.join(LOG_DIR, FILE_1)
    p2 = os.path.join(LOG_DIR, FILE_2)
    out_path = os.path.join(LOG_DIR, OUT_FILE)

    if not os.path.exists(p1):
        raise FileNotFoundError(p1)
    if not os.path.exists(p2):
        raise FileNotFoundError(p2)

    df1 = pd.read_csv(p1)
    df2 = pd.read_csv(p2)

    # Tag provenance
    df1 = df1.copy()
    df2 = df2.copy()
    df1["source_file"] = FILE_1
    df2["source_file"] = FILE_2

    # Align columns (union of both)
    all_cols = sorted(set(df1.columns).union(df2.columns))
    df1 = df1.reindex(columns=all_cols)
    df2 = df2.reindex(columns=all_cols)

    combined = pd.concat([df1, df2], ignore_index=True)

    # Sort by time if possible
    time_col = _detect_time_col(combined)
    if time_col:
        if time_col.lower() in {"timestamp", "time"}:
            combined["_sort_time"] = _try_parse_datetime(combined[time_col])
            # If parse worked for at least some rows, sort
            if combined["_sort_time"].notna().any():
                combined = combined.sort_values("_sort_time", kind="stable")
            combined = combined.drop(columns=["_sort_time"])
        else:
            # elapsed_s assumed numeric
            combined[time_col] = pd.to_numeric(combined[time_col], errors="coerce")
            combined = combined.sort_values(time_col, kind="stable")

    # Drop exact duplicate rows
    combined = combined.drop_duplicates()

    combined.to_csv(out_path, index=False)
    # Only delete originals if the output file exists and is non-trivial
    if os.path.exists(out_path) and os.path.getsize(out_path) > 200:
        os.remove(p1)
        os.remove(p2)
    return out_path


if __name__ == "__main__":
    out = combine_two_files()
    print(f"Wrote: {out}")