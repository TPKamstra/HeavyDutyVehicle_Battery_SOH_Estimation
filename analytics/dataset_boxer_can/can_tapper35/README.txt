Boxer motor start battery export

Tapper: can_tapper35
Query window: 2021-11-30_23:59:00.000000 .. 2026-07-31_00:02:00.000000
Event window: -60.0 s .. +120.0 s around each start
OCV window: 30.0 s, ending 5.0 s before the start
Grid: 20 ms, merge tolerance 2.0 s

Files:
  features.csv              one row per start, pack level
  features_packs.csv        one row per start per battery group
  start_metadata.csv        per start: data coverage and timing
  engine_events.csv         glow plug, starting and running markers
  voltage_series_raw.csv    measured voltage samples, long format
  current_series_raw.csv    measured pack current samples
  temperature_series_raw.csv measured temperature samples
  voltage_series_grid.csv   voltages on the common relative time grid
  imbalance_series_grid.csv per group imbalance on the same grid
  metadata.json             all settings used for this export

Row counts:
  features: 72
  features_packs: 288
  start_metadata: 72
  engine_events: 222
  voltage_series_raw: 152330
  current_series_raw: 25703
  temperature_series_raw: 35022
  voltage_series_grid: 648072
  imbalance_series_grid: 2592288