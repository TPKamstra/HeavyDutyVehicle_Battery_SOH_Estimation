Same pack as the original 41-start dataset used in the paper's Results section 5.2
(can_tapper11 IS that dataset — identical start-for-start). This export adds {n_more_taps}
more taps ({n_more_starts} more starts) plus CAN commando event timing and a new post-start
voltage-stability feature (`post_start_V_std`), which is the field-side analogue of
`driving_aux_load_V_std` — the strongest SoC-robust indicator found in the lab campaign
(Section 5.1 of the paper). The original 41-start dataset could not test this, since it only
ever captured crank-window features. Some taps (currently can_tapper14/15) arrived split across
multiple zip exports covering different date ranges (a single export got too large); these are
merged per-tap by build_boxer_can_features.py before this dashboard ever sees them.

**Note on units:** `V_pre_total_1_10V` in features.csv is the *sum* of all four battery-group
voltages (~2x a single 12V-class group), not the vehicle's real CAN "Total Voltage" signal.
The new post-start feature instead uses the grid's per-group-average voltage, so it's on a
directly comparable scale to `V_pre_1_10V`/`V_min_1_10V` in features_packs.csv.
