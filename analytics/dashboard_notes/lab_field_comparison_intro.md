**Feature-family consistency check, not a value-for-value validation** — the lab battery
(Ultracell UL18-12 `unit2`, full 20-block campaign, 140 runs) and the field battery (EnerSys
ArmaSafe Plus 12FV120, 24 V 2S2P) are different products run through separate pipelines (BDPS
pulse-test rig vs. vehicle CAN bus), and the field side has no shared ground-truth SOH to validate
against. This checks whether the same feature families behave in a qualitatively similar way on
both sides, per the paper's "Lab-to-Field Validation Procedure" — not that the sides should land on
the same numbers. `crank_duration_s`/`glow_lead_s` are field-only (the lab's crank pulses have a
fixed, scripted duration) and are intentionally excluded.

Each panel below is one (condition, battery) pair — e.g. lab cold-crank, lab hot-crank, field — so
nothing is ever pooled into a single misleading box. Axes are capped to a robust (outlier-resistant)
range — a subtitle under each panel reports how many points were clipped from view and the most
extreme value among them; the table at the bottom always reports true, uncapped min/mean/max. The
field's crank-R_int panel additionally excludes ~4.8% of starts flagged unreliable by a
current-based diagnostic (a sign-flip cluster on one CAN tap, plus scattered near-zero-current
starts on most others) — see `lab_field_feature_comparison.py`'s module docstring for the full
investigation.

*(The other lab battery, `old_ul18_12`, used to appear here too — dropped 2026-08-26: only 8 runs
on a partial data-logging rollout, too thin a sample to read anything into next to unit2's 140 and
field's 4799. It still has its own full dashboard tab, "Lab — old_ul18_12".)*
