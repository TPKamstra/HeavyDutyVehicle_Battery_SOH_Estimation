**Feature-family consistency check, not a value-for-value validation** — both lab batteries
(Ultracell UL18-12: `old_ul18_12`, already-aged, 8 runs; `unit2`, full 20-block campaign, 140 runs)
and the field battery (EnerSys ArmaSafe Plus 12FV120, 24 V 2S2P) are different products run through
separate pipelines (BDPS pulse-test rig vs. vehicle CAN bus), and the field side has no shared
ground-truth SOH to validate against. This checks whether the same feature families behave in a
qualitatively similar way on both sides, per the paper's "Lab-to-Field Validation Procedure" — not
that the sides should land on the same numbers. `old_ul18_12`'s 8 runs are a coarse spot-check, not
weighted equally against `unit2`'s 140. `crank_duration_s`/`glow_lead_s` are field-only (the lab's
crank pulses have a fixed, scripted duration) and are intentionally excluded.

Each panel below is one (battery, condition) pair — e.g. old_ul18_12 cold-crank, unit2 cold-crank,
field — so nothing is ever pooled into a single misleading box. Axes are capped to a robust
(outlier-resistant) range — a subtitle under each panel reports how many points were clipped from
view and the most extreme value among them; the table at the bottom always reports true, uncapped
min/mean/max. The field's crank-R_int panel additionally excludes ~4.8% of starts flagged
unreliable by a current-based diagnostic (a sign-flip cluster on one CAN tap, plus scattered
near-zero-current starts on most others) — see `lab_field_feature_comparison.py`'s module docstring
for the full investigation.
