**Feature-family consistency check, not a value-for-value validation** — the lab battery
(Ultracell UL18-12, 12 V) and the field battery (EnerSys ArmaSafe Plus 12FV120, 24 V 2S2P) are
different products run through separate pipelines (BDPS pulse-test rig vs. vehicle CAN bus), and
the field side has no shared ground-truth SOH to validate against. This checks whether the same
feature families behave in a qualitatively similar way on both sides, per the paper's
"Lab-to-Field Validation Procedure" — not that the two sides should land on the same numbers.
`crank_duration_s`/`glow_lead_s` are field-only (the lab's crank pulses have a fixed, scripted
duration) and are intentionally excluded.

Each panel below is one condition (e.g. lab cold-crank, lab hot-crank, field), so lab and field are
never pooled into a single misleading box. Axes are capped to a robust (outlier-resistant) range —
a subtitle under each panel reports how many points were clipped from view and the most extreme
value among them; the table at the bottom always reports true, uncapped min/mean/max.
