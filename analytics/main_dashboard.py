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
"""
import panel as pn

# One consolidated call for the whole process -- avoids each source module
# re-registering the same "plotly"/"tabulator" extensions redundantly (harmless
# but noisy) when imported below.
pn.extension("plotly", "tabulator")

_real_servable = pn.viewable.Viewable.servable
pn.viewable.Viewable.servable = lambda self, *a, **kw: self  # no-op during composed imports

import battery_feature_dashboard as lab_old            # noqa: E402  (old_ul18_12)
import battery_feature_dashboard_unit2 as lab_unit2     # noqa: E402
import boxer_can_dashboard as field_can                 # noqa: E402
import lab_field_feature_comparison as lfc               # noqa: E402

pn.viewable.Viewable.servable = _real_servable


# ── Lab vs Field Comparison (top-level section) ─────────────────────────────
# Reuses data field_can already loaded at import time (LAB_OLD_FEATURES,
# LAB_FEATURES, FEAT, FEAT_PK) -- no duplicate CSV reads. Rebuilds the figures
# fresh via lfc's pure functions rather than reaching into field_can.tabs to
# pull out its nested copy of the same content (see module docstring).

def _build_comparison_section() -> pn.Column:
    families = lfc.build_matched_series(
        field_can.LAB_OLD_FEATURES, field_can.LAB_FEATURES, field_can.FEAT, field_can.FEAT_PK,
    )
    summary = lfc.summary_table(families)
    rows = "".join(
        f"| {r['feature_family']} | {r['series']} | {r['n']} | {r['min']:.2f} | {r['mean']:.2f} | "
        f"{r['median']:.2f} | {r['max']:.2f} |\n"
        for _, r in summary.iterrows()
    )
    intro = pn.pane.Markdown(
        "**Feature-family consistency check, not a value-for-value validation** — both lab "
        "batteries (`old_ul18_12`, `unit2`) vs. the field Boxer CAN dataset. See "
        "`lab_field_feature_comparison.py`'s module docstring for the full framing, the lab "
        "crank-current caveat, and the field R_int reliability filter (excludes ~4.8% of field "
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


# ── Compose ──────────────────────────────────────────────────────────────────

tabs = pn.Tabs(
    ("Lab — old_ul18_12", lab_old.dashboard),
    ("Lab — unit2", lab_unit2.main_content),
    ("Field — Boxer CAN (15 taps)", pn.Column(*field_can.template.main, sizing_mode="stretch_width")),
    ("Lab vs Field Comparison", _build_comparison_section()),
    sizing_mode="stretch_both",
)

template = pn.template.FastListTemplate(
    title="Battery SOH Dashboard — Lab + Field",
    main=[tabs],
    accent_base_color="#2C4F8C",
    header_background="#2C4F8C",
    header_color="#FFFFFF",
    main_max_width="100%",
)

template.servable()

if __name__ == "__main__":
    pn.serve(template, show=True, port=5010)
