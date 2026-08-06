Boxer motor start battery export

Tapper: can_tapper20
Query window: 2019-11-30_23:59:00.000000 .. 2026-05-31_00:02:00.000000
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
  features: 400
  features_packs: 1600
  start_metadata: 400
  engine_events: 1273
  voltage_series_raw: 766643
  current_series_raw: 141257
  temperature_series_raw: 267128
  voltage_series_grid: 3600400
  imbalance_series_grid: 14401600