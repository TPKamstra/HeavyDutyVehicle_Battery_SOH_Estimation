# Featureoverzicht — lab (test-day v2) en veld (Boxer CAN)

Per feature: wat het precies is, hoe het berekend wordt, hoe goed het in het
lab SOH volgt, en wat het tegenhanger-signaal in de Boxer-data is. Opgesteld
2026-10-30 uit de code zelf:

- lab: `testday_v2_features.py::extract_features()` (event-gedreven, één rij
  per test-day-run), profiel `lab-control/LabSetupV2/test_day_profile_new.csv`
- veld: de per-start tabellen uit de CAN-export (`dataset_boxer_can/*/features*.csv`)
  plus de vier kolommen die `build_boxer_can_features.py` zelf toevoegt
- koppeling: `lab_field_feature_comparison.py` (de drie families die nu
  vergeleken worden)
- lab-SOH-statistiek: `exports/ul18_12_unit2_summary_export/soc_robust_soh_indicators.csv`

## 0. Lezen van de tabellen

- **r_SOH** = Spearman r tussen de feature (per blok) en de gemeten C/5-capaciteit
  van dat blok (`r_this_block`, n = 18 blokken, `ul18_12_unit2`). Negatief =
  feature stijgt als de accu slechter wordt.
- **r_SoC** = Spearman r met de SoC van de run. Een goede SOH-indicator heeft
  |r_SOH| hoog en |r_SoC| laag.
- **Vergelijkbaarheid** lab ↔ veld: ✅ zelfde fysica, ⚠ zelfde idee maar
  andere omstandigheden, ❌ geen tegenhanger.

⚠ **Kanttekening bij alle r-waarden.** De SoC-sweep van `ul18_12_unit2`
stuurde op OCV-doelen. Vanaf blok ~8 draaiden daardoor bijna alle runs op een
vrijwel lege accu met een OCV-label van 12,2–12,7 V (zie
`lab-control/LabSetupV2/PI_HANDOFF_test_v3_5cycles_50pctSOH.md`, deel 2). In
die blokken zijn SoC en SOH dus niet onafhankelijk gevarieerd. Features die op
"lege accu" reageren (`total_ah_*`, `ocv_window_Vpre`, `I_peak`) krijgen
daardoor een hoge r_SOH die deels een SoC-effect is. Herbereken deze tabel met
de Ah-gebaseerde sweep van de nieuwe accu.

**Conventies die verschillen tussen lab en veld**

| | Lab | Veld |
|---|---|---|
| Spanning | V, één 12 V-accu | 1/10 V; per batterijgroep (`features_packs`, ~12,4 V) of de **som** van de vier groepen (`*_total_*`, ~49,6 V; dus niet het echte "FM-Total Voltage"-signaal) |
| Stroomteken | negatief = ontladen | `I_load_A` positief bij cranken (~400 A); `I_mean_post_A` negatief tijdens laden door de dynamo. De naam van het CAN-signaal ("Batteries Charging Current") en de data wijzen op positief = ontladen. Niet gedocumenteerd, dus verifiëren. |
| Stroommeting | per sample, BDPS 20 Hz | één pack-stroomsignaal; PG1/PG3 zijn **geschatte** groepen, alleen PG2/PG4 worden echt gemeten |
| SoC | Coulomb-geteld (vanaf nu) / OCV-label | `soc_pct_est_pack`, uit de OCV afgeleid in de export (ρ = 0,965 met V_pre; dus circulair met spanningsfeatures) |
| Ground truth SOH | C/5-capaciteit per blok | geen |

## 1. Het lab-profiel (event-volgorde)

De features zijn per event gedefinieerd, dus eerst wat elk event is (setpoints
uit `test_day_profile_new.csv`, totaal 2065 s):

| # | Event | Duur | Stroom (setpoint) | Doel / veld-analogie |
|---|---|---|---|---|
| 0 | `rest_baseline` | 390 s | 0 A | voertuig geparkeerd |
| 1 | `ocv_window` | 60 s | 0 A | OCV-meting vóór de dag |
| 2 | `wakeup_load` (1) | 120 s | −1 A | voertuig wordt "wakker" (elektronica) |
| 3 | `wakeup_load` (2) | 150 s | 0 … −2 A | idem, variabel |
| 4 | `glow_plug_like_load` | 45 s | −8 A | gloeibougies voorgloeien |
| 5 | `crank_pulse` (koud) | 4 s | −75 → −38 A (inrush-decay) | koude start |
| 6 | `recovery_rest` | 210 s | 0 A | ontspanning na start |
| 7 | `alternator_charge` | 164 s | +1,8 … +4,5 A | dynamo laadt bij |
| 8 | `driving_aux_load` | 180 s | −1 … −10 A, variabel | rijden met wisselende verbruikers (zonder dynamo) |
| 9 | `ramp_like_load` | 20 s | 0 → −30 A | oplopende last |
| 10 | `recovery_rest` | 120 s | 0 A | |
| 11 | `crank_pulse` (warm) | 2,5 s | −55 → −30 A | warme herstart |
| 12 | `recovery_rest` | 600 s | 0 A | |

⚠ **`old_ul18_12` en `ul18_12_unit2`:** daar kwam de crankstroom veel lager
uit dan het setpoint (mediaan 3,5–7 A in plaats van 38–75 A). Oorzaak
gevonden op 2026-11-02: de sink bleef op de vorige stroomgrens staan (zie
`analytics/CLAUDE.md`, "Frozen sink current"). **`ul18_12_unit3` vanaf blok 2**
draait het profiel `test_day_profile_v2_cap60A.csv` met de fix: de koude crank
haalt 60 A, de warme ~35 A (het setpoint van 55 A duurt 0,05 s, korter dan de
stroomopbouw van ~0,1 s). In het veld is de crankstroom ~400 A.

## 1a. Featurecatalogus en geldigheid per feature (ul18_12_unit3)

Bron: `FEATURE_CATALOG` in `testday_v2_features.py` (2026-11-17). De tabel
hieronder is daaruit gegenereerd met `catalog_markdown()`. Pas de catalogus
aan, niet deze tabel, en genereer opnieuw:
```
C:\Users\TPKam\miniconda3\envs\ddsm\python.exe -c "import testday_v2_features as v; print(v.catalog_markdown())"
```
`tests/test_feature_catalog.py` controleert dat de catalogus precies de
kolommen van `extract_features()` beschrijft. Per feature staan in de catalogus
ook de fysische betekenis en de veld-tegenhanger; het dashboard (tab Feature
Explorer) toont die tekst.

**Soort (`kind`).** `feature`: kandidaat-indicator. `diagnostic`: nuttig voor
controle, geen indicator. `profile_constant`: volgt het profiel, niet de accu.
`duplicate_of`: herhaalt een andere kolom. De laatste twee staan niet in de
heatmap, de scatter-keuzelijsten en de voorspellertabellen, wel in de
Excel-export.

**Geldigheid per feature per run** (`feature_validity()`). Een ongeldige waarde
wordt in elke statistiek NaN; de reden staat in `<feature>__invalid_reason`. De
regels:

| Regel | Ongeldig als | Waarom |
|---|---|---|
| `tracking` | het event `voltage_limited` of `failed` is (mediaan meetstroom > 10 % naast het setpoint) | de spanning hoort niet bij de bedoelde last |
| `not_failed` | het event `failed` is | voor de inrush-features van de crank (V_min, I_peak, R_int): op 10 % SoC kan het staartstuk de vloer raken na een volle inrush (blok 2 en 6) |
| `current_low` | I_peak < 90 % van het setpoint dat na de stroomopbouw (0,1 s) haalbaar is | de vloer begrenste de stroom |
| `v_pre_floor` | V_pre ≤ 10,2 V + 0,3 V | geen ruimte meer voor de dip van de puls |
| `delta_i_small` | stroomstap < 50 % van de stap die het profiel commandeert | V/I beschrijft de puls niet meer. Een verhouding, geen vaste ampèrewaarde: de gecommandeerde stap verandert niet met de veroudering |
| `i_range_small` | idem voor het stroombereik van een regressie (ramp, rijden) | |
| `few_samples` | < 3 samples in een venster (bij "+30 s": geen sample binnen 1 s) | vangt het grove loggen van de Pi af |
| `never` | altijd | `wakeup_load_2_R_int_mohm`: referentie gemeten onder last |

Resultaat op unit3 (blok 2–10): alle 81 runs op 20–100 % SoC zijn volledig
geldig. Op 10 % SoC is de koude-crankweerstand ongeldig in blok 3, 4, 5 en 10
(20–32 A in plaats van 60 A), en de hele koude crank (ook V_pre = 10,20 V) in
blok 7–9. De ramp en de warme crank zijn op 10 % in alle blokken begrensd.

| Feature | Kind | Event · window | Formula | Unit | Ageing | SoC dependence | Validity rules |
|---|---|---|---|---|---|---|---|
| `rest_baseline_Vmean` | feature | rest_baseline #1 · whole event | mean V over the whole rest_baseline event (390 s, 0 A) | V | none at a fixed charge; at a fixed relative SoC it rises, because less Ah is removed | strong: the OCV curve | few_samples |
| `ocv_window_Vpre` | feature | ocv_window #1 · last 20 s | mean V over the last 20 s of ocv_window (0 A) | V | as rest_baseline_Vmean | strong: the OCV curve | few_samples |
| `wakeup_load_1_sag_V` | feature | wakeup_load #1 · last 1 s of the event before, whole event | V_ref - mean V over the event; V_ref = mean V over the last 1 s of ocv_window | V | rises | weak; rises at low SoC | tracking, few_samples |
| `wakeup_load_1_R_int_mohm` | feature | wakeup_load #1 · last 1 s of the event before, whole event | wakeup_load_1_sag_V / mean |I| x 1000 | mOhm | rises | weak; rises at low SoC | tracking, delta_i_small, few_samples |
| `wakeup_load_2_sag_V` | diagnostic | wakeup_load #2 · last 1 s of the event before, whole event | V_ref - mean V over the event; V_ref = mean V over the last 1 s of wakeup_load 1 (under 1 A) | V | unclear | moderate | tracking, few_samples |
| `wakeup_load_2_R_int_mohm` | diagnostic | wakeup_load #2 · last 1 s of the event before, whole event | wakeup_load_2_sag_V / mean |I| x 1000 | mOhm | n/a | n/a | never |
| `glow_plug_like_load_1_Vmean` | feature | glow_plug_like_load #1 · whole event | mean V over the 8 A glow load (45 s) | V | falls | strong | tracking, few_samples |
| `crank_cold_V_pre` | feature | crank_pulse #1 · last 1 s of the event before | mean V over the last 1 s of glow_plug_like_load | V | falls (more polarisation under load) | strong: follows the OCV curve | v_pre_floor, few_samples |
| `crank_cold_V_min` | feature | crank_pulse #1 · first 0.3 s | min V in the first 0.3 s of the crank | V | falls (higher resistance) | strong: starts from a lower V_pre at low SoC | not_failed, current_low, v_pre_floor, few_samples |
| `crank_cold_I_peak_A` | profile_constant | crank_pulse #1 · whole event | max |I| over the crank | A | none while the current is delivered | none, until the floor limits the current | not_failed, current_low, v_pre_floor, few_samples |
| `crank_cold_V_sustained` | feature | crank_pulse #1 · last 1 s | mean V over the last 1 s of the crank | V | falls | strong | tracking, current_low, few_samples |
| `crank_cold_R_int_apparent_mohm` | diagnostic | crank_pulse #1 · last 1 s of the event before, first 0.3 s | (V_pre - V_min) / I_peak x 1000 | mOhm | rises | moderate: rises at low SoC | not_failed, current_low, v_pre_floor, delta_i_small, few_samples |
| `crank_cold_R_int_deltaI_mohm` | feature | crank_pulse #1 · last 1 s of the event before, first 0.3 s | (V_pre - V_min) / (I_peak - I_pre) x 1000; I_pre = median |I| over the last 1 s of glow_plug_like_load | mOhm | rises | moderate: rises at low SoC | not_failed, current_low, v_pre_floor, delta_i_small, few_samples |
| `crank_hot_V_pre` | feature | crank_pulse #2 · last 1 s of the event before | mean V over the last 1 s of the recovery_rest after the ramp | V | little change at fixed charge | strong: follows the OCV curve | v_pre_floor, few_samples |
| `crank_hot_V_min` | feature | crank_pulse #2 · first 0.3 s | min V in the first 0.3 s of the crank | V | falls (higher resistance) | strong: starts from a lower V_pre at low SoC | not_failed, current_low, v_pre_floor, few_samples |
| `crank_hot_I_peak_A` | profile_constant | crank_pulse #2 · whole event | max |I| over the crank | A | none while the current is delivered | none, until the floor limits the current | not_failed, current_low, v_pre_floor, few_samples |
| `crank_hot_V_sustained` | feature | crank_pulse #2 · last 1 s | mean V over the last 1 s of the crank | V | falls | strong | tracking, current_low, few_samples |
| `crank_hot_R_int_apparent_mohm` | duplicate_of:crank_hot_R_int_deltaI_mohm | crank_pulse #2 · last 1 s of the event before, first 0.3 s | (V_pre - V_min) / I_peak x 1000 | mOhm | rises | moderate: rises at low SoC | not_failed, current_low, v_pre_floor, delta_i_small, few_samples |
| `crank_hot_R_int_deltaI_mohm` | feature | crank_pulse #2 · last 1 s of the event before, first 0.3 s | (V_pre - V_min) / (I_peak - I_pre) x 1000; I_pre = median |I| over the last 1 s of the recovery_rest after the ramp | mOhm | rises | moderate: rises at low SoC | not_failed, current_low, v_pre_floor, delta_i_small, few_samples |
| `recovery_after_cold_V_plus30s` | feature | recovery_rest #1 · at +30 s | V at 30 s into the rest after the cold crank (0 A) | V | falls slightly (slower recovery) | strong: relaxes toward the OCV | few_samples, current_low@crank_pulse:1 |
| `recovery_after_ramp_V_plus30s` | feature | recovery_rest #2 · at +30 s | V at 30 s into the rest after the 0-30 A ramp (0 A) | V | falls slightly | strong: relaxes toward the OCV | few_samples, tracking@ramp_like_load:1 |
| `recovery_after_hot_V_plus30s` | feature | recovery_rest #3 · at +30 s | V at 30 s into the rest after the hot crank (0 A) | V | falls slightly | strong: relaxes toward the OCV | few_samples, current_low@crank_pulse:2 |
| `alternator_charge_after_cold_Ah` | feature | alternator_charge #1 · whole event | integral of I dt over alternator_charge (+1.8 to +4.5 A, 15 V ceiling) | Ah | falls (less charge acceptance) | strong: a full battery accepts less | few_samples |
| `alternator_charge_after_cold_ratio_pct` | feature | alternator_charge #1 · whole event | alternator_charge_after_cold_Ah / Ah discharged so far in the run x 100 | % | falls | strong | few_samples |
| `driving_aux_load_1_V_mean` | feature | driving_aux_load #1 · whole event | mean V over driving_aux_load (180 s, -1 to -10 A) | V | falls | strong | tracking, few_samples |
| `driving_aux_load_1_V_std` | feature | driving_aux_load #1 · whole event | std of V over driving_aux_load | V | rises | moderate | tracking, few_samples |
| `driving_aux_load_1_R_int_est_mohm` | feature | driving_aux_load #1 · whole event | |slope of a linear fit V(I)| over the event x 1000 | mOhm | rises | moderate | tracking, i_range_small, few_samples |
| `ramp_like_load_1_V_mean` | feature | ramp_like_load #1 · whole event | mean V over the 0-30 A ramp (20 s) | V | falls | strong | tracking, few_samples |
| `ramp_like_load_1_V_std` | feature | ramp_like_load #1 · whole event | std of V over the ramp | V | rises | moderate | tracking, few_samples |
| `ramp_like_load_1_R_int_est_mohm` | feature | ramp_like_load #1 · whole event | |slope of a linear fit V(I)| over the ramp x 1000 | mOhm | rises | moderate | tracking, i_range_small, few_samples |
| `total_ah_discharged` | profile_constant | whole run · whole event | sum of discharged Ah over all events | Ah | none | none, unless the floor limits | - |
| `total_ah_charged` | duplicate_of:alternator_charge_after_cold_Ah | whole run · whole event | sum of charged Ah over all events | Ah | see that feature |  | - |
| `run_duration_s` | profile_constant | whole run · whole event | last Elapsed_s of the run | s | none | none | - |
| `n_events` | diagnostic | whole run · whole event | number of distinct Event_Index values | - | none | none | - |
| `min_voltage` | diagnostic | whole run · whole event | min V over the run | V | falls | strong | - |
| `final_voltage` | diagnostic | whole run · whole event | last V of the run (end of a 600 s rest) | V | as the OCV | strong | - |

**Momentopname Feature Explorer (unit3, blok 2–10, 2026-11-17).** Statistiek
alleen over geldige waarden. `r_charge_matched` = Spearman r met de capaciteit
na verwijderen van wat de ontladen Ah verklaart (kwadratische fit); effectieve n
= aantal blokken. Verschuiving = verandering over de campagne bij vaste SoC, in
ADC-stappen van 8 mV (gemeten in `rest_baseline`). Conclusies: *volgt lading*
(R² van de Ah-fit ≥ 0,8 of |r_soc| ≥ 0,8, en |r_charge_matched| < 0,5), *volgt
SOH* (|r_charge_matched| ≥ 0,5), *ruis* (verschuiving < 2 ADC-stappen),
*zwak* (geen van beide), *volgt profiel*, *duplicaat*, *ongeldig*.

| Feature | Geldig | r_soc | r_charge_matched | Verschuiving (ADC-stappen) | Conclusie |
|---|---|---|---|---|---|
| `rest_baseline_Vmean` | 90/90 | +0.94 | -0.10 | 39 | volgt lading |
| `ocv_window_Vpre` | 90/90 | +0.94 | -0.11 | 39 | volgt lading |
| `wakeup_load_1_sag_V` | 90/90 | +0.54 | -0.36 | 4 | zwak |
| `wakeup_load_1_R_int_mohm` | 90/90 | +0.54 | -0.36 | 4 | zwak |
| `wakeup_load_2_sag_V` | 90/90 | -0.40 | +0.51 | 1 | ruis |
| `wakeup_load_2_R_int_mohm` | 0/90 | – | – | – | ongeldig |
| `glow_plug_like_load_1_Vmean` | 89/90 | +0.85 | +0.14 | 35 | volgt lading · ongeldig bij lage SoC |
| `crank_cold_V_pre` | 87/90 | +0.82 | +0.09 | 37 | volgt lading · ongeldig bij lage SoC |
| `crank_cold_V_min` | 83/90 | +0.87 | +0.68 | 10 | volgt SOH en lading · ongeldig bij lage SoC |
| `crank_cold_I_peak_A` | 83/90 | -0.17 | +0.17 | – | volgt profiel · ongeldig bij lage SoC |
| `crank_cold_V_sustained` | 81/90 | +0.93 | +0.49 | 10 | volgt lading · ongeldig bij lage SoC |
| `crank_cold_R_int_apparent_mohm` | 83/90 | -0.48 | -0.70 | 40 | volgt SOH · ongeldig bij lage SoC |
| `crank_cold_R_int_deltaI_mohm` | 83/90 | -0.48 | -0.69 | 46 | volgt SOH · ongeldig bij lage SoC |
| `crank_hot_V_pre` | 90/90 | +0.93 | -0.42 | 39 | volgt lading |
| `crank_hot_V_min` | 81/90 | +0.94 | +0.58 | 2 | volgt SOH en lading · ongeldig bij lage SoC |
| `crank_hot_I_peak_A` | 81/90 | -0.04 | -0.24 | – | volgt profiel · ongeldig bij lage SoC |
| `crank_hot_V_sustained` | 81/90 | +0.94 | +0.43 | 19 | volgt lading · ongeldig bij lage SoC |
| `crank_hot_R_int_apparent_mohm` | 81/90 | -0.43 | -0.64 | 29 | duplicaat |
| `crank_hot_R_int_deltaI_mohm` | 81/90 | -0.43 | -0.64 | 29 | volgt SOH · ongeldig bij lage SoC |
| `recovery_after_cold_V_plus30s` | 83/90 | +0.93 | -0.60 | 35 | volgt SOH en lading · ongeldig bij lage SoC |
| `recovery_after_ramp_V_plus30s` | 81/90 | +0.93 | -0.71 | 35 | volgt SOH en lading · ongeldig bij lage SoC |
| `recovery_after_hot_V_plus30s` | 81/90 | +0.93 | -0.61 | 35 | volgt SOH en lading · ongeldig bij lage SoC |
| `alternator_charge_after_cold_Ah` | 90/90 | -0.44 | -0.01 | – | zwak |
| `alternator_charge_after_cold_ratio_pct` | 90/90 | -0.46 | -0.22 | – | zwak |
| `driving_aux_load_1_V_mean` | 90/90 | +0.95 | +0.36 | 35 | volgt lading |
| `driving_aux_load_1_V_std` | 90/90 | +0.40 | -0.27 | 5 | zwak |
| `driving_aux_load_1_R_int_est_mohm` | 90/90 | +0.23 | -0.45 | 16 | zwak |
| `ramp_like_load_1_V_mean` | 81/90 | +0.94 | +0.36 | 21 | volgt lading · ongeldig bij lage SoC |
| `ramp_like_load_1_V_std` | 81/90 | -0.61 | -0.46 | 9 | zwak · ongeldig bij lage SoC |
| `ramp_like_load_1_R_int_est_mohm` | 81/90 | -0.63 | -0.48 | 18 | zwak · ongeldig bij lage SoC |
| `total_ah_discharged` | 90/90 | +0.40 | +0.29 | – | volgt profiel |
| `total_ah_charged` | 90/90 | -0.44 | -0.01 | – | duplicaat |
| `run_duration_s` | 90/90 | -0.00 | +0.17 | – | volgt profiel |
| `n_events` | 90/90 | – | +0.31 | – | zwak |
| `min_voltage` | 90/90 | +0.88 | +0.63 | 5 | volgt SOH en lading |
| `final_voltage` | 90/90 | +0.94 | -0.30 | 39 | volgt lading |

Leesvoorbeeld: `rest_baseline_Vmean` verschuift 39 ADC-stappen over de
campagne bij vaste SoC, maar dat is de SoC-grid die met de capaciteit krimpt:
tegen ontladen Ah vallen alle blokken op één curve (R² 0,99). De
crank-weerstanden houden r_charge_matched ≈ −0,65 tot −0,70.

## 1b. Gedetailleerde curve-features (ul18_12_unit3, 2026-11-18)

Bron: `DETAILED_CATALOG` in `detailed_features.py`, beoordeeld met
`soh_eval.py` (de leave-one-block-out-methode van de papertabel
`tab:features`). Te zien op het dashboardtabblad **Detailed Features**. De
tabel hieronder is gegenereerd met `dfm.catalog_markdown()`.

Twee groepen:
- **field**: uit de testdag-events (20 Hz). Een voertuig dat spanning en
  stroom rond dezelfde events logt, kan ze ook berekenen.
- **lab**: uit tests die een voertuig nooit doet (C/5, CC/CV-laden, rust na
  een step-down). Dit is bewijs over het mechanisme, geen indicator aan boord.

Geldigheid: een field-feature erft de ongeldig-reden van een `parent`-feature
uit `FEATURE_CATALOG` (spanningsvloer, stroom niet gehaald, ...), plus zijn
eigen regels (fit-RMSE > 10 mV, tijdconstante op een fitgrens, laadstroom
volgt het setpoint niet).

| Feature | Source | Availability | Formula | Unit | Validity | Literature |
|---|---|---|---|---|---|---|
| `wakeup1_R0_mohm` | testday | field | (V(t*) - V_ref) / (I(t*) - I_ref) x 1000, t* = sample of the largest voltage deviation within 0.5 s of onset; V_ref, I_ref = last 1 s before | mOhm | parent `wakeup_load_1_R_int_mohm`, fit_points | khodadadi2021 |
| `wakeup1_Rend_mohm` | testday | field | (V_end - V_ref) / (I_end - I_ref) x 1000, end = last 0.5 s (120 s at 1 A) | mOhm | parent `wakeup_load_1_R_int_mohm` | khodadadi2021 |
| `wakeup1_Rpol_mohm` | testday | field | Rend - R0 | mOhm | parent `('wakeup_load_1_R_int_mohm',)` | khodadadi2021 |
| `wakeup1_Rpol_ratio` | testday | field | Rpol / R0 | - | parent `('wakeup_load_1_R_int_mohm',)` | khodadadi2021 |
| `glow_R0_mohm` | testday | field | (V(t*) - V_ref) / (I(t*) - I_ref) x 1000, t* = sample of the largest voltage deviation within 0.5 s of onset; V_ref, I_ref = last 1 s before | mOhm | parent `glow_plug_like_load_1_Vmean`, fit_points | khodadadi2021 |
| `glow_Rend_mohm` | testday | field | (V_end - V_ref) / (I_end - I_ref) x 1000, end = last 0.5 s (45 s at 8 A) | mOhm | parent `glow_plug_like_load_1_Vmean` | khodadadi2021 |
| `glow_Rpol_mohm` | testday | field | Rend - R0 | mOhm | parent `('glow_plug_like_load_1_Vmean',)` | khodadadi2021 |
| `glow_Rpol_ratio` | testday | field | Rpol / R0 | - | parent `('glow_plug_like_load_1_Vmean',)` | khodadadi2021 |
| `crank_cold_R0_mohm` | testday | field | (V(t*) - V_ref) / (I(t*) - I_ref) x 1000, t* = sample of the largest voltage deviation within 0.5 s of onset; V_ref, I_ref = last 1 s before | mOhm | parent `crank_cold_R_int_deltaI_mohm`, fit_points | khodadadi2021, kerley2015, grube2008 |
| `crank_cold_Rend_mohm` | testday | field | (V_end - V_ref) / (I_end - I_ref) x 1000, end = last 0.5 s (4 s; -38 A plateau) | mOhm | parent `crank_cold_V_sustained` | khodadadi2021, kerley2015, grube2008 |
| `crank_cold_Rpol_mohm` | testday | field | Rend - R0 | mOhm | parent `('crank_cold_R_int_deltaI_mohm', 'crank_cold_V_sustained')` | khodadadi2021, kerley2015, grube2008 |
| `crank_cold_Rpol_ratio` | testday | field | Rpol / R0 | - | parent `('crank_cold_R_int_deltaI_mohm', 'crank_cold_V_sustained')` | khodadadi2021, kerley2015, grube2008 |
| `crank_hot_R0_mohm` | testday | field | (V(t*) - V_ref) / (I(t*) - I_ref) x 1000, t* = sample of the largest voltage deviation within 0.5 s of onset; V_ref, I_ref = last 1 s before | mOhm | parent `crank_hot_R_int_deltaI_mohm`, fit_points | khodadadi2021, kerley2015 |
| `crank_hot_Rend_mohm` | testday | field | (V_end - V_ref) / (I_end - I_ref) x 1000, end = last 0.5 s (2.5 s at -30 A) | mOhm | parent `crank_hot_V_sustained` | khodadadi2021, kerley2015 |
| `crank_hot_Rpol_mohm` | testday | field | Rend - R0 | mOhm | parent `('crank_hot_R_int_deltaI_mohm', 'crank_hot_V_sustained')` | khodadadi2021, kerley2015 |
| `crank_hot_Rpol_ratio` | testday | field | Rpol / R0 | - | parent `('crank_hot_R_int_deltaI_mohm', 'crank_hot_V_sustained')` | khodadadi2021, kerley2015 |
| `crank_cold_dRdt_mohm_per_s` | testday | field | slope of (V_ref - V)/(I - I_ref) over the -38 A plateau (from 1 s to the end) x 1000 | mOhm/s | parent `crank_cold_V_sustained`, fit_points | khodadadi2021 |
| `glow_ecm_R0_mohm` | testday | field | least-squares fit of V(t) = V_ref + dI (R0 + R1 (1 - exp(-t/tau))) + k t over the glow_plug_like_load step (dI signed) | mOhm | parent `glow_plug_like_load_1_Vmean`, fit_rmse, fit_bound | khodadadi2021 |
| `glow_ecm_R1_mohm` | testday | field | least-squares fit of V(t) = V_ref + dI (R0 + R1 (1 - exp(-t/tau))) + k t over the glow_plug_like_load step (dI signed) | mOhm | parent `glow_plug_like_load_1_Vmean`, fit_rmse, fit_bound | khodadadi2021 |
| `glow_ecm_tau_s` | testday | field | least-squares fit of V(t) = V_ref + dI (R0 + R1 (1 - exp(-t/tau))) + k t over the glow_plug_like_load step (dI signed) | s | parent `glow_plug_like_load_1_Vmean`, fit_rmse, fit_bound | khodadadi2021 |
| `glow_ecm_rmse_mV` | testday | field | least-squares fit of V(t) = V_ref + dI (R0 + R1 (1 - exp(-t/tau))) + k t over the glow_plug_like_load step (dI signed) | mV | parent `glow_plug_like_load_1_Vmean`, fit_rmse, fit_bound | khodadadi2021 |
| `wakeup1_ecm_R0_mohm` | testday | field | least-squares fit of V(t) = V_ref + dI (R0 + R1 (1 - exp(-t/tau))) + k t over the wakeup_load step (dI signed) | mOhm | parent `wakeup_load_1_R_int_mohm`, fit_rmse, fit_bound | khodadadi2021 |
| `wakeup1_ecm_R1_mohm` | testday | field | least-squares fit of V(t) = V_ref + dI (R0 + R1 (1 - exp(-t/tau))) + k t over the wakeup_load step (dI signed) | mOhm | parent `wakeup_load_1_R_int_mohm`, fit_rmse, fit_bound | khodadadi2021 |
| `wakeup1_ecm_tau_s` | testday | field | least-squares fit of V(t) = V_ref + dI (R0 + R1 (1 - exp(-t/tau))) + k t over the wakeup_load step (dI signed) | s | parent `wakeup_load_1_R_int_mohm`, fit_rmse, fit_bound | khodadadi2021 |
| `wakeup1_ecm_rmse_mV` | testday | field | least-squares fit of V(t) = V_ref + dI (R0 + R1 (1 - exp(-t/tau))) + k t over the wakeup_load step (dI signed) | mV | parent `wakeup_load_1_R_int_mohm`, fit_rmse, fit_bound | khodadadi2021 |
| `relax_cold_logslope_mV_per_dec` | testday | field | slope of V against log10(t) between 1 and 100 s into the rest | mV/decade | parent `recovery_after_cold_V_plus30s`, fit_points | zhu2022, jiang2022 |
| `relax_cold_amp_mV` | testday | field | V(end) - V(0.5 s) | mV | parent `recovery_after_cold_V_plus30s`, fit_points | zhu2022, jiang2022 |
| `relax_cold_tau1_s` | testday | field | fast time constant of V = Vinf - a1 exp(-t/tau1) - a2 exp(-t/tau2) | s | parent `recovery_after_cold_V_plus30s`, fit_rmse, fit_bound | zhu2022, jiang2022 |
| `relax_cold_tau2_s` | testday | field | slow time constant of the same fit | s | parent `recovery_after_cold_V_plus30s`, fit_rmse, fit_bound | zhu2022, jiang2022 |
| `relax_cold_a2_share` | testday | field | a2 / (a1 + a2) | - | parent `recovery_after_cold_V_plus30s`, fit_rmse, fit_bound | zhu2022, jiang2022 |
| `relax_ramp_logslope_mV_per_dec` | testday | field | slope of V against log10(t) between 1 and 100 s into the rest | mV/decade | parent `recovery_after_ramp_V_plus30s`, fit_points | zhu2022, jiang2022 |
| `relax_ramp_amp_mV` | testday | field | V(end) - V(0.5 s) | mV | parent `recovery_after_ramp_V_plus30s`, fit_points | zhu2022, jiang2022 |
| `relax_ramp_tau1_s` | testday | field | fast time constant of V = Vinf - a1 exp(-t/tau1) - a2 exp(-t/tau2) | s | parent `recovery_after_ramp_V_plus30s`, fit_rmse, fit_bound | zhu2022, jiang2022 |
| `relax_ramp_tau2_s` | testday | field | slow time constant of the same fit | s | parent `recovery_after_ramp_V_plus30s`, fit_rmse, fit_bound | zhu2022, jiang2022 |
| `relax_ramp_a2_share` | testday | field | a2 / (a1 + a2) | - | parent `recovery_after_ramp_V_plus30s`, fit_rmse, fit_bound | zhu2022, jiang2022 |
| `relax_hot_logslope_mV_per_dec` | testday | field | slope of V against log10(t) between 1 and 100 s into the rest | mV/decade | parent `recovery_after_hot_V_plus30s`, fit_points | zhu2022, jiang2022 |
| `relax_hot_amp_mV` | testday | field | V(end) - V(0.5 s) | mV | parent `recovery_after_hot_V_plus30s`, fit_points | zhu2022, jiang2022 |
| `relax_hot_tau1_s` | testday | field | fast time constant of V = Vinf - a1 exp(-t/tau1) - a2 exp(-t/tau2) | s | parent `recovery_after_hot_V_plus30s`, fit_rmse, fit_bound | zhu2022, jiang2022 |
| `relax_hot_tau2_s` | testday | field | slow time constant of the same fit | s | parent `recovery_after_hot_V_plus30s`, fit_rmse, fit_bound | zhu2022, jiang2022 |
| `relax_hot_a2_share` | testday | field | a2 / (a1 + a2) | - | parent `recovery_after_hot_V_plus30s`, fit_rmse, fit_bound | zhu2022, jiang2022 |
| `alt_R0_mohm` | testday | field | (V(t*) - V_ref) / (I(t*) - I_ref) x 1000 at the +4.5 A charge step | mOhm | parent `alternator_charge_after_cold_Ah`, charge_tracking, fit_points | jiang2022 |
| `alt_dVdt_mV_per_min` | testday | field | slope of V over the first 60 s after the step | mV/min | parent `alternator_charge_after_cold_Ah`, charge_tracking, fit_points | jiang2022 |
| `alt_R0_over_crank_R0` | testday | field | alt_R0_mohm / crank_cold_R0_mohm | - | parent `crank_cold_R_int_deltaI_mohm`, charge_tracking | jiang2022 |
| `c5_R_1s_mohm` | c5 | lab | (V of the last rest sample - V of the first sample under load) / |I| x 1000, after 1 h rest from full | mOhm | c5_complete | jiang2022 |
| `c5_drop_60s_mV` | c5 | lab | V(first sample under load) - V(60 s) | mV | c5_complete | jiang2022 |
| `c5_cdf_depth_mV` | c5 | lab | max V after the trough - trough V, both in the first 900 s | mV | c5_complete | pascoe2002, delaille2006, bose2000 |
| `c5_cdf_time_s` | c5 | lab | time from the trough to the plateau (the max after it) | s | c5_complete, cdf_present | pascoe2002, delaille2006 |
| `c5_dVdQ_at3Ah_mV_per_Ah` | c5 | lab | slope of V against Ah removed over 3 +- 0.5 Ah | mV/Ah | c5_complete | dubarry2022, sugumaran2024 |
| `chg_V_at_0p5Ah` | charge | lab | V at 0.5 Ah charged (CC phase, 5.4 A) | V | charge_complete | electronics2023 |
| `chg_V_at_2Ah` | charge | lab | V at 2 Ah charged (CC phase, 5.4 A) | V | charge_complete | electronics2023 |
| `chg_cc_dVdQ_mV_per_Ah` | charge | lab | slope of V against Ah charged between 0.5 and 2 Ah | mV/Ah | charge_complete | electronics2023 |
| `chg_cc_share` | charge | lab | Ah charged before the current leaves the CC level (V >= 14.35 V) / total Ah charged | - | charge_complete | electronics2023, jiang2022 |
| `chg_cv_tau_s` | charge | lab | time constant of an exponential fit I = I_inf + A exp(-t/tau) to the CV current | s | charge_complete, fit_rmse_current | electronics2023 |
| `chg_factor` | charge | lab | Ah charged / Ah of the discharge just before | - | charge_complete | jiang2022 |
| `rest_relax_amp_mV` | rest | lab | V(end of the 1800 s rest) - V(first sample, ~10 s) | mV | rest_complete | zhu2022, jiang2022 |
| `rest_relax_logslope_mV_per_dec` | rest | lab | slope of V against log10(t) over the rest | mV/decade | rest_complete | zhu2022 |
| `rest_relax_t63_s` | rest | lab | time until V has covered 63 % of its rise over the rest (model-free) | s | rest_complete | zhu2022 |

**Resultaat (LOBO-RMSE in SOH-procentpunten).**

| Model | RMSE | Opmerking |
|---|---|---|
| Baseline: gemiddelde SOH | 18,8 | |
| Baseline: alleen OCV | 17,1 | |
| Beste bestaande feature (`crank_cold_R_int_deltaI_mohm`) | 10,1 | |
| `relax_hot_tau2_s` | 5,6 | ρ = +0,98 over 10 blokken bij 3 Ah ontladen |
| `relax_cold_tau2_s` | 9,1 | |
| `rest_relax_t63_s` (lab) | 9,4 | |
| `wakeup1_ecm_tau_s` | 10,2 | |

Toelichting op de resultaten:
- **Wat beter is dan 10,1:** drie nieuwe features: `relax_hot_tau2_s`,
  `relax_cold_tau2_s` en `rest_relax_t63_s`. Bij `relax_hot_tau2_s` is
  R²_OCV 0,28, dus de feature volgt niet alleen de lading. Bij gelijke
  ontladen lading (3 Ah) daalt hij monotoon van 62 s (blok 2) naar 46 s
  (blok 11).
- **Wat de relaxatietijden zeggen:** alle drie zijn relaxatietijden. Een
  verouderde accu relaxeert sneller, ook bij gelijke ontladen lading.
- **Mogelijke verklaring (hypothese, niet getoetst):** minder actieve massa
  neemt deel, dus het zuur hoeft minder ver te diffunderen.
- **Ohmse/polarisatiesplitsing:** voegt niets toe ten opzichte van de
  bestaande crankweerstand. `crank_cold_R0_mohm` haalt 12,9;
  `crank_*_Rpol_*` haalt ongeveer de baseline.
- **C/5-features:**
  - een coup de fouet is er alleen in blok 3;
  - in de C/5-logs van blok 4–7 en 11 zit een spanningsverstoring van 75–100
    mV;
  - `c5_dVdQ_at3Ah` en de coup-de-fouet-features worden daar afgekeurd.

  De capaciteit zelf (stroomintegraal) is niet aangetast.
- **Laadfactor:** `chg_factor` is 1,013–1,021 in alle blokken. Dat bevestigt
  de lage laadteruggave uit de discussie van het paper (1–3 %).
  `chg_cc_share` daalt van 0,77 naar 0,50: de laadacceptatie neemt af.
- **Meervoudig toetsen:** er zijn ongeveer 40 kandidaten en maar 10 blokken.
  De beste paar zijn dus deels toevallig geselecteerd. Behandel ze als
  aanwijzing en bevestig ze op een volgende accu.
- **Niet geïmplementeerd:**
  - sample-entropie (A6): de 8 mV ADC-stap domineert waarschijnlijk;
  - een volledige IC/DV-analyse (B4): de C/5-spanning is in vijf blokken
    verstoord.

## 2. Lab-features en hun veld-tegenhanger

### 2.1 Rust / OCV

| Lab-feature | Definitie | r_SOH / r_SoC | Veld-tegenhanger | Vgl. |
|---|---|---|---|---|
| `rest_baseline_Vmean` | gemiddelde V over het hele `rest_baseline`-event (390 s, 0 A) | −0,91 / 0,99 | `V_ocv_1_10V` (packs) / `V_ocv_total_1_10V`: gemiddelde V over een venster van 30 s dat 5 s vóór de start eindigt | ⚠ |
| `ocv_window_Vpre` | gemiddelde V over de laatste 20 s van `ocv_window` | −0,93 / 0,98 | idem `V_ocv_*`; `V_ocv_slope_mV_per_s` geeft aan of de spanning in dat venster nog beweegt (mediaan 3 mV/s), `n_ocv_samples` hoeveel samples erin zaten | ⚠ |

Dit zijn **SoC-proxies, geen SOH-features**: r_SoC ≈ 0,98. De hoge r_SOH komt
door het sweep-artefact hierboven. In het veld is het "OCV"-venster geen echte
rust: de voertuigelektronica is vaak al wakker (`I_pre_A`). Dat is
vergelijkbaar met de lab-`wakeup_load`, niet met `ocv_window`.

### 2.2 Wake-up (kleine last vóór de start)

| Lab-feature | Definitie | r_SOH / r_SoC | Veld-tegenhanger | Vgl. |
|---|---|---|---|---|
| `wakeup_load_1_sag_V` | V_ref − gemiddelde V tijdens wakeup 1; V_ref = gemiddelde V in de laatste 1 s van het vorige event | **−0,68 / 0,09** | Niet direct beschikbaar. Wel af te leiden: `V_ocv − V_pre` met stroom `I_pre_A` (`V_pre` = gemiddelde V van −0,5 tot −0,1 s vóór de start) | ⚠ (kandidaat) |
| `wakeup_load_1_R_int_mohm` | sag / gemiddelde \|I\| × 1000 | −0,59 / 0,41 | idem: (`V_ocv` − `V_pre`) / `I_pre_A`, nog niet berekend | ⚠ (kandidaat) |
| `wakeup_load_2_sag_V` | idem voor wakeup 2 | −0,51 / 0,38 | — | |
| `wakeup_load_2_R_int_mohm` | idem | **onbruikbaar** (in 132/133 runs negatief: bug in V_ref, zie `NOTES.md`) | — | ❌ |

`wakeup_load_1_sag_V` is na `driving_aux_load_1_V_std` de beste SoC-robuuste
lab-indicator. Hij heeft nog geen veld-tegenhanger in de pipeline.

### 2.3 Gloeibougies

| Lab-feature | Definitie | r_SOH / r_SoC | Veld-tegenhanger | Vgl. |
|---|---|---|---|---|
| `glow_plug_like_load_1_Vmean` | gemiddelde V tijdens de last van 8 A (45 s) | 0,18 / 0,18 (zwak) | Alleen de timing: `t_glow_plug_s` (start van het voorgloeien t.o.v. de start) en `glow_lead_s` (= −`t_glow_plug_s`, mediaan 2,0 s). De spanning tijdens het voorgloeien staat wel in de grid-reeks, maar wordt nog niet als feature berekend | ⚠ (kandidaat) |

### 2.4 Crank (de start zelf)

Koud = eerste `crank_pulse`, warm = tweede (op volgorde, niet op stroom). Bij
een afgebroken run met maar één crank heten de kolommen `crank_1_*`.

| Lab-feature | Definitie | r_SOH / r_SoC | Veld-tegenhanger | Vgl. |
|---|---|---|---|---|
| `crank_*_V_pre` | V_ref vlak vóór de crank (laatste 1 s van `glow_plug_like_load`) | cold 0,09 / 0,17, hot 0,34 / 0,60 | `V_pre_1_10V` (per groep) / `V_pre_total_1_10V`: gemiddelde van −0,5 tot −0,1 s | ✅ |
| `crank_*_V_min` | minimum V in de eerste 0,3 s van de crank | ≈ 0,1–0,2 / 0,2 | `V_min_1_10V` / `V_min_total_1_10V`: minimum in het crankvenster (2 s); `t_min_s` = tijdstip van dat minimum (mediaan 0,76 s); `V_at_Iload_1_10V` = V op het moment van de piekstroom | ✅ |
| `crank_*_V_sustained` | gemiddelde V in de laatste 1 s van de crank | ≈ 0,1 / 0,2 | Geen exacte. Het dichtst erbij: `V_rec_1s_1_10V` | ⚠ |
| `crank_*_I_peak_A` | max \|I\| in de crank | cold 0,73 / −0,57, hot 0,91 / −0,73 | `I_load_A` (piekstroom, mediaan 402 A), `delta_I_A` (= `I_load_A` − `I_pre_A`), `t_load_s` (tijdstip van de piek) | ⚠ |
| `crank_*_R_int_deltaI_mohm` (sinds 2026-11-17 in `extract_features()`) | (V_pre − V_min) / (I_peak − I_pre) × 1000, I_pre = mediaan \|I\| in de laatste 1 s van het vorige event. **De aanbevolen crankweerstand**: de koude crank start vanuit −8 A | unit3: zie §1a (r_charge_matched −0,69 koud, −0,64 warm) | idem `R_internal_est_ohm` | ⚠ |
| `crank_*_R_int_apparent_mohm` | (V_pre − V_min) / I_peak × 1000. Koud leest te laag (deelt door de piek, niet door de stap); warm is gelijk aan `_deltaI_` | cold −0,28 / −0,01, hot −0,32 / 0,04 | `R_internal_est_ohm` (per groep) / `_pack`: ΔV/ΔI rond de piekstroom (`R_method` = "synchronized_peak_current_voltage_drop"). Twee varianten: `_min_voltage_method` (gebruikt V_min) en `_from_ocv` (gebruikt V_ocv als referentie). Mediaan 9 mΩ | ⚠ |
| (geen) | — | — | `V_drop_pct` / `V_drop_total_pct`: (V_pre − V_min)/V_pre × 100, mediaan 9,6 % | ✅ (lab kan dit uit V_pre/V_min berekenen, maar doet het nog niet) |
| (geen) | — | — | `crank_duration_s` = `t_running_cmd_s` − `t_starting_cmd_s` (startcommando → "engine running" via CAN, mediaan 1,6 s) | ❌ veld-only (lab heeft een vaste crankduur) |

Waarom ⚠ en geen ✅ bij R_int en I_peak:

- **Lab:** de gelogde I_peak is 2 ordes kleiner dan in het veld (3,5–7 A tegen
  ~400 A). R_int = ΔV / I wordt dan door ruis gedomineerd. De `*_invalid`-kolommen
  vlaggen negatieve waarden en |I| < 1 A. De hoge r_SOH van `I_peak` betekent
  vooral dat een lege of verouderde accu de setpoint-stroom niet meer kan
  leveren. Dat is geen weerstandsmeting.
- **Veld:** `invalid_R_int` vlagt 306 van de 4799 starts (`I_load_A` < 200 A,
  ontbrekend, of negatief: cluster op `can_tapper14`). Gebruik R_int altijd met
  die vlag. De R_int-definities worden **upstream** in de CAN-export berekend,
  niet in deze repo. De omschrijving hierboven komt uit de kolomnamen,
  `R_method` en `metadata.json` (`pre_window_s = [−0,5, −0,1]`,
  `crank_window_s = 2`). Laat de exacte formule bevestigen door wie de export
  maakt.

### 2.5 Herstel na de crank

| Lab-feature | Definitie | r_SOH / r_SoC | Veld-tegenhanger | Vgl. |
|---|---|---|---|---|
| `recovery_after_cold_V_plus30s` | V op 30 s na het begin van `recovery_rest` (**0 A, pure ontspanning**) | 0,57 / 0,47 | `V_rec_30s_1_10V` (huidige koppeling in `lab_field_feature_comparison.py`) | ❌ fysisch anders, zie hieronder |
| `recovery_after_hot_V_plus30s` | idem na de warme crank | 0,37 / 0,59 | idem | ❌ |
| `recovery_after_ramp_V_plus30s` (nieuw, 2026-11-17) | idem na de ramp van 0 → 30 A (event 10) | unit3: zie §1a | — | ❌ |

⚠ **Labelbug, gerepareerd 2026-11-17.** Tot die datum kreeg elke
`recovery_rest` het label van de laatste crank. De rust na de ramp (event 10)
overschreef daardoor `recovery_after_cold_V_plus30s`: die kolom was in feite
V op +30 s **na de ramp**. De r-waarden hierboven (unit2-export) gaan nog over
die verkeerde kolom. Sinds de fix krijgt alleen de rust direct na een crank het
cranklabel; andere rusten heten naar hun vorige event
(`recovery_after_ramp_*`; bij afwijkende eventvolgordes in unit2 ook
bijvoorbeeld `recovery_after_rest_baseline_*`).
| (geen) | — | — | `V_rec_1s/2s/5s/10s/30s/60s_1_10V`: V op 1…60 s na de start | zie hieronder |
| (geen) | — | — | `t_recovery_s` / `_max`: tijd tot de spanning hersteld is (mediaan 1,0 s); `tau_relax_s` / `_median`: tijdconstante van een exponentiële fit van de ontspanning (mediaan 2,9 s), met `V_inf_1_10V` (asymptoot), `relax_fit_r2` en `n_relax_samples` | ⚠ |

⚠ **Belangrijk voor §5.3 van de paper.** In het veld begint de dynamo al na
`t_charge_onset_s` ≈ 2,15 s na de start te laden. Op +30 s staat de groep dan
op ~14,2 V (mediaan `V_rec_30s` = 142 in 1/10 V): dat is de regelspanning van
de dynamo, geen ontspanning van de accu. Het lab meet op +30 s een accu in
rust. De huidige familie (b) vergelijkt daarom twee verschillende dingen.
Beter is het lab-herstel te vergelijken met het veldherstel **vóór** de dynamo
inschakelt (`V_rec_1s`, `V_rec_2s`, `t_recovery_s`, `tau_relax_s`), of het lab
op +1/+2 s te meten (bijvoorbeeld een `recovery_after_*_V_plus1s` toevoegen).

### 2.6 Dynamo / laadacceptatie

| Lab-feature | Definitie | r_SOH / r_SoC | Veld-tegenhanger | Vgl. |
|---|---|---|---|---|
| `alternator_charge_after_cold_Ah` | ∫I dt tijdens `alternator_charge` (setpoint +1,8…+4,5 A, BDPS met een spanningsgrens van 15 V). Minder Ah = de accu neemt minder op omdat de spanning de grens raakt | **0,85 / −0,54** | `I_mean_post_A` (gemiddelde stroom na de start, mediaan −30 A = laden), `I_max_post_A`, `t_charge_onset_s` (begin van het laden, mediaan 2,15 s) | ⚠ (kandidaat, nog niet gekoppeld) |
| `alternator_charge_after_cold_ratio_pct` | dat Ah / tot dan toe ontladen Ah × 100 | −0,53 / 0,53 | — | |

Het lab stuurt een vaste stroom met een spanningsplafond; de dynamo regelt een
spanning (~14,2 V per groep) en de stroom volgt uit wat de accu accepteert. De
grootheid is anders, maar het idee (laadacceptatie daalt met SOH) is
hetzelfde. Dit is een sterke lab-indicator die nog niet in de lab-veld-vergelijking
zit. De SoC-afhankelijkheid (r_SoC −0,54) moet in het veld wel worden
meegenomen (`soc_pct_est_pack`).

### 2.7 Rijden / variabele lasten

| Lab-feature | Definitie | r_SOH / r_SoC | Veld-tegenhanger | Vgl. |
|---|---|---|---|---|
| `driving_aux_load_1_V_std` | standaarddeviatie van V tijdens `driving_aux_load` (180 s, −1…−10 A, **zonder dynamo**) | **−0,92 / 0,25** (beste SoC-robuuste indicator) | `post_start_V_std_1_10V`: std van de groepsgemiddelde spanning tussen +15 en +115 s na de start (berekend in `build_boxer_can_features.py`) | ⚠ |
| `driving_aux_load_1_V_mean` | gemiddelde V in datzelfde event | −0,05 / 0,42 | `post_start_V_mean_1_10V` | ⚠ |
| `driving_aux_load_1_R_int_est_mohm` | \|helling\| van een lineaire fit V(I) over alle samples × 1000 | −0,24 / 0,45 | — | ❌ |
| `ramp_like_load_1_V_mean` / `_V_std` / `_R_int_est_mohm` | idem voor de ramp van 0 → 30 A (20 s) | zwak | — | ❌ |

⚠ Familie (c) heeft hetzelfde probleem als het herstel: in het lab spreidt de
spanning omdat de accu zelf de wisselende last moet leveren. In het veld valt
het venster van +15–115 s in de periode waarin de dynamo laadt. De spreiding
komt dan grotendeels van dynamoregeling en voertuigverbruikers, en de accu is
maar beperkt zichtbaar. Dat verklaart deels waarom de veldwaarde per voertuig
zo verschilt (dikke staart, zie `analytics/CLAUDE.md`). Een eerlijker
veld-analoog is spanningsvariatie **zonder** dynamo: bijvoorbeeld de
`I_pre_A`-periode vóór de start, of stilstand met draaiende verbruikers en
motor uit, als die in de data zit.

### 2.8 Run-totalen (diagnostisch, geen veldfeature)

| Lab-feature | Definitie | r_SOH / r_SoC |
|---|---|---|
| `total_ah_discharged` / `total_ah_charged` | som van ontladen / geladen Ah over alle events | 0,71 / −0,57 en 0,86 / −0,76 |
| `run_duration_s`, `n_events` | duur en aantal events; `cutoff_hit` = minder events dan normaal voor die accu (= vroegtijdig afgebroken) | — |
| `min_voltage`, `final_voltage` | minimum en laatste V van de run | zwak / `final_voltage` is een SoC-proxy |

De totalen zakken vooral als de accu leeg is en het profiel zijn stroom niet
meer kan leveren. Gebruik ze als **kwaliteitsvlag** voor een run, niet als
SOH-feature.

## 3. Veld-only features

| Kolom | Betekenis |
|---|---|
| `max_abs_imbalance_pct` / `mean_abs_imbalance_pct` (`_pack`) | grootste / gemiddelde procentuele afwijking van een groepsspanning t.o.v. het gemiddelde van de vier groepen tijdens de start (mediaan max 8 %). Geen lab-tegenhanger: het lab heeft één accu. Let op: PG1/PG3 zijn geschat (en `PG3_est` is byte-identiek aan `PG4`, zie `boxer_summary_export/NOTES.md`). |
| `temp_avg_start_C` | gemiddelde accutemperatuur rond de start (mediaan 16 °C). Het lab heeft temperatuur in de sensorlog (25–29 °C, constant), maar niet als feature. **De sterkste driver van veld-R_int** (koud ≈ 10–11 mΩ, warm ≈ 7,5–8 mΩ). |
| `soc_pct_est` / `soc_pct_est_ocv` (`_pack`) | SoC-schatting uit de OCV, in de export bepaald (mediaan 81 %). Circulair met de spanningsfeatures. |
| `t_since_previous_start_s` | tijd sinds de vorige start. Relevant voor de rusttijd: zie het capaciteitsherstel na lange rust in het lab. |
| `crank_duration_s`, `glow_lead_s`, `t_*_cmd_s` | CAN-commando-timing (zie 2.3/2.4) |
| `invalid_R_int`, `R_int_current_sensor_dropout`, `R_int_negative_current` | kwaliteitsvlaggen voor R_int (zie 2.4) |
| `start_color` | plotkleur (`start_index % 9`), **geen meting**; alleen bruikbaar als nulcontrole |
| `n_*_samples`, `pre/post_data_duration_s`, `t_before_s`, `t_after_s`, `zip_part` | dekking en herkomst van de data |

## 4. Samenvatting — wat transfereert

| Familie | Beste lab-feature | Veld | Status nu | Aanbeveling |
|---|---|---|---|---|
| Spanningsvariatie onder wisselende last | `driving_aux_load_1_V_std` (r_SOH −0,92) | `post_start_V_std_1_10V` | vergeleken (c) | ⚠ veld valt in de periode met dynamo; zoek een venster zonder dynamo |
| Wake-up sag | `wakeup_load_1_sag_V` (−0,68, SoC-robuust) | af te leiden uit `V_ocv`, `V_pre`, `I_pre_A` | niet gekoppeld | nieuwe veldfeature `pre_crank_sag_V` / `pre_crank_R_mohm` |
| Laadacceptatie | `alternator_charge_after_cold_Ah` (0,85, wel SoC-gevoelig) | `I_mean_post_A`, `I_max_post_A` | niet gekoppeld | toevoegen als familie (d), met SoC als covariaat |
| Crank-weerstand | `crank_*_R_int_apparent_mohm` (zwak, lab-stroom onrealistisch) | `R_internal_est_ohm_pack` | vergeleken (a) | alleen als consistentiecheck; lab-crankstroom repareren voor echte vergelijking |
| Herstel | `recovery_after_*_V_plus30s` | `V_rec_30s` | vergeleken (b) | ❌ in de huidige vorm: vergelijk met `V_rec_1s/2s`, `tau_relax_s` |
| Spanningsdip | (lab berekent het niet) | `V_drop_total_pct` | — | lab-feature `crank_*_V_drop_pct` toevoegen |
| OCV / SoC | `ocv_window_Vpre` | `V_ocv_*`, `soc_pct_est_pack` | — | SoC-covariaat, geen SOH-feature |
