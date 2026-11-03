#!/usr/bin/env python3
"""
Unified Battery SOH Dashboard — composes the four existing standalone
dashboards into one Panel app, reachable from a single URL. This is
COMPOSITION, not a rewrite: every tab below reuses the source modules'
already-built Panel objects (their `dashboard`/`main_content`/`template`
variables) or their shared functions (`lab_field_feature_comparison.py`) --
nothing here re-implements feature extraction, plotting, or layout that
already exists. Each source file also stays independently runnable via its
own `if __name__ == "__main__":` block and own port (see analytics/CLAUDE.md
for the full port table).

Sections:
    "Lab — old_ul18_12"          battery_feature_dashboard.py's own Tabs
    "Lab — unit2"                 battery_feature_dashboard_unit2.py's own main_content
    "Lab — unit3"                 battery_feature_dashboard_unit3.py's own main_content
    "Field — Boxer CAN (15 taps)" boxer_can_dashboard.py's own summary + Tabs
                                   (this already includes its own nested
                                   "Lab vs Field Comparison" tab -- see the
                                   note on the top-level section of the same
                                   name below)
    "Lab vs Field Comparison"     built directly here from
                                   lab_field_feature_comparison.py's shared
                                   functions, reusing data already loaded by
                                   boxer_can_dashboard.py (no duplicate CSV
                                   reads) -- listed as its own top-level
                                   section per this composition's design,
                                   even though the Field tab above already
                                   contains the same content nested one level
                                   down. Deliberate, not deduplicated: pulling
                                   a single named tab out of another module's
                                   already-built `pn.Tabs` object would mean
                                   reaching into that module's internal
                                   structure, which is exactly what this file
                                   is trying to avoid doing.

Deliberately NOT included: boxer_battery_dashboard.py (the old, 41-start
field dataset). See that file's own docstring for why it's kept as a
separate, independently-run dashboard rather than folded in here -- main.tex's
current Results section is written specifically around its numbers, and its
data is already the can_tapper11 slice of the Field tab above.

Import-time side-effect note: every source module below builds a full Panel
layout (and, for boxer_can_dashboard.py and battery_feature_dashboard_unit2.py,
a `pn.template.FastListTemplate`) at MODULE level and calls `.servable()` on
it unconditionally -- that's fine when the file is run standalone via
`panel serve`, but importing multiple such modules into one process while a
Panel server document is active would register every one of their top-level
objects as an extra root on THIS app's page (duplicate content, not an
error, but not what this file wants). `.servable()` is neutralized (as a
no-op) for the duration of these imports only, then restored, so nothing
downstream that relies on real `.servable()` behavior (e.g. if this file's
own top-level object is later imported by something else) is affected.

Lazy tab loading (added 2026-08-25): each of the four source modules does
real work at import time -- reading every raw CSV (the Boxer CAN dataset
alone is 4799 starts across 15 taps, plus an ~82 MB raw-trace file for the
Outlier Inspector) and eagerly building every one of its own tabs' Plotly
figures -- with no caching. Importing all four back-to-back made the unified
app block on the *sum* of that work before Panel could return the very
first byte of HTML. Each tab below is now a placeholder until first
selected: its source module is only imported (and only then does the heavy
CSV read / figure build happen) the moment the tab becomes active, via
`tabs.param.watch` on `"active"`. The tab shown by default (`Lab —
old_ul18_12`) is loaded via `pn.state.onload`, which runs *after* the
initial page has already reached the browser, so the page shell (title,
tab bar) appears immediately instead of waiting on that tab's own load.
Each module is still only ever imported once per process (subsequent tab
switches re-show the already-built content).
"""
import os
import sys

import panel as pn

# `panel serve` (via Bokeh's CodeRunner.run()) inserts this file's directory
# onto sys.path only for the duration of *executing this script*, then
# restores sys.path to an exact pre-exec snapshot in a `finally` -- a full
# list replacement (`sys.path = _sys_path`), not a targeted removal, so it
# wipes out ANY insertion made during exec, including one done here at
# module level (tried that first; it does not survive). The lazy tab loading
# below (see docstring) imports sibling modules from inside a
# `pn.state.onload` callback, which in a real server session fires only
# after the page has reached the browser and reported "document_ready" --
# i.e. well after that cleanup has already run. Without re-inserting the
# directory at call time (see `_import_quietly` below), those deferred
# imports raise `ModuleNotFoundError: No module named 'battery_feature_dashboard'`
# the moment a tab is actually opened, even though `panel serve` starts up
# and the page shell loads without complaint.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# One consolidated call for the whole process -- avoids each source module
# re-registering the same "plotly"/"tabulator" extensions redundantly (harmless
# but noisy) when imported below.
pn.extension("plotly", "tabulator")

_real_servable = pn.viewable.Viewable.servable


def _import_quietly(name: str):
    """Import a dashboard source module with .servable() neutralized so its
    module-level `.servable()` call doesn't register an extra root on this
    app's page (see module docstring).

    Re-inserts `_THIS_DIR` onto sys.path right here, at call time, rather
    than relying on an insertion made earlier at module-exec time -- see the
    comment by `_THIS_DIR`'s definition above for why the earlier one gets
    silently wiped by Bokeh before this function is ever called from a
    deferred (`pn.state.onload`) tab load."""
    if _THIS_DIR not in sys.path:
        sys.path.insert(0, _THIS_DIR)
    pn.viewable.Viewable.servable = lambda self, *a, **kw: self
    try:
        import importlib
        return importlib.import_module(name)
    finally:
        pn.viewable.Viewable.servable = _real_servable


# ── Lab vs Field Comparison (top-level section) ─────────────────────────────
# Reuses data field_can already loaded at import time (LAB_FEATURES, FEAT,
# FEAT_PK) -- no duplicate CSV reads. Rebuilds the figures fresh via lfc's
# pure functions rather than reaching into field_can.tabs to pull out its
# nested copy of the same content (see module docstring).

def _build_comparison_section() -> pn.Column:
    field_can = _import_quietly("boxer_can_dashboard")
    lfc = _import_quietly("lab_field_feature_comparison")
    families = lfc.build_matched_series(
        field_can.LAB_FEATURES, field_can.FEAT, field_can.FEAT_PK,
    )
    summary = lfc.summary_table(families)
    rows = "".join(
        f"| {r['feature_family']} | {r['series']} | {r['n']} | {r['min']:.2f} | {r['mean']:.2f} | "
        f"{r['median']:.2f} | {r['max']:.2f} |\n"
        for _, r in summary.iterrows()
    )
    intro = pn.pane.Markdown(
        "**Feature-family consistency check, not a value-for-value validation** — the lab "
        "battery `unit2` vs. the field Boxer CAN dataset. See "
        "`lab_field_feature_comparison.py`'s module docstring for the full framing, the lab "
        "crank-current caveat, and the field R_int reliability filter (excludes ~6.4% of field "
        "starts -- a sign-flip cluster on `can_tapper14` plus scattered near-zero-current starts "
        "across most other taps).",
        sizing_mode="stretch_width", margin=(0, 20, 10, 20),
    )
    table = pn.pane.Markdown(
        f"### Full (uncapped) summary statistics\n\n"
        f"| Feature family | Series | n | Min | Mean | Median | Max |\n"
        f"|---|---|---|---|---|---|---|\n{rows}",
        sizing_mode="stretch_width", margin=(10, 20, 20, 20),
    )
    figs = [
        pn.pane.Plotly(lfc.build_family_figure(fam), config={"responsive": True}, sizing_mode="stretch_width")
        for fam in families
    ]
    return pn.Column(intro, *figs, table, sizing_mode="stretch_width")


def _build_lab_old() -> pn.viewable.Viewable:
    return _import_quietly("battery_feature_dashboard").dashboard


def _build_lab_unit2() -> pn.viewable.Viewable:
    return _import_quietly("battery_feature_dashboard_unit2").main_content


def _build_lab_unit3() -> pn.viewable.Viewable:
    return _import_quietly("battery_feature_dashboard_unit3").main_content


def _build_field_can() -> pn.viewable.Viewable:
    field_can = _import_quietly("boxer_can_dashboard")
    return pn.Column(*field_can.template.main, sizing_mode="stretch_width")


# ── Compose (lazy) ───────────────────────────────────────────────────────────
# Each tab starts as a spinner placeholder; `_TAB_BUILDERS[i]` only runs the
# first time tab i becomes active (see module docstring above).

_TAB_BUILDERS = [
    ("Lab — old_ul18_12", _build_lab_old),
    ("Lab — unit2", _build_lab_unit2),
    ("Lab — unit3", _build_lab_unit3),
    ("Field — Boxer CAN (15 taps)", _build_field_can),
    ("Lab vs Field Comparison", _build_comparison_section),
]


def _placeholder(label: str) -> pn.Column:
    return pn.Column(
        pn.Row(
            pn.indicators.LoadingSpinner(value=True, width=32, height=32),
            pn.pane.Markdown(f"Loading **{label}**…"),
            align="center",
        ),
        sizing_mode="stretch_both", margin=40,
    )


_loaded = [False] * len(_TAB_BUILDERS)

tabs = pn.Tabs(
    *[(label, _placeholder(label)) for label, _ in _TAB_BUILDERS],
    dynamic=True,
    sizing_mode="stretch_both",
)


def _load_tab(index: int) -> None:
    if _loaded[index]:
        return
    _loaded[index] = True
    label, builder = _TAB_BUILDERS[index]
    tabs[index] = (label, builder())


def _on_active_change(event) -> None:
    _load_tab(event.new)


tabs.param.watch(_on_active_change, "active")

template = pn.template.FastListTemplate(
    title="Battery SOH Dashboard — Lab + Field",
    main=[tabs],
    accent_base_color="#2C4F8C",
    header_background="#2C4F8C",
    header_color="#FFFFFF",
    main_max_width="100%",
)

template.servable()

# Load the default (first) tab only after the page shell has already reached
# the browser, so the initial HTTP response isn't blocked on it.
pn.state.onload(lambda: _load_tab(0))

if __name__ == "__main__":
    pn.serve(template, show=True, port=5010)
