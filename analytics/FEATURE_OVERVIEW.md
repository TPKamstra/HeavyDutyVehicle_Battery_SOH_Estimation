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

⚠ In de logs komt de crankstroom veel lager uit dan het setpoint: mediaan
3,5–7 A in plaats van 38–75 A. Waarschijnlijk begrenst de BDPS of de
replay-logica hem. In het veld is dat ~400 A. Alles wat met de crank te maken
heeft, is in het lab dus géén realistische crank.

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
| `crank_*_R_int_apparent_mohm` | (V_pre − V_min) / I_peak × 1000 | cold −0,28 / −0,01, hot −0,32 / 0,04 | `R_internal_est_ohm` (per groep) / `_pack`: ΔV/ΔI rond de piekstroom (`R_method` = "synchronized_peak_current_voltage_drop"). Twee varianten: `_min_voltage_method` (gebruikt V_min) en `_from_ocv` (gebruikt V_ocv als referentie). Mediaan 9 mΩ | ⚠ |
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
