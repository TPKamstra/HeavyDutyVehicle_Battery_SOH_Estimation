# Handoff: dashboard voor `ul18_12_unit3`

**Uitgevoerd op 2026-11-03**: zie `battery_feature_dashboard_unit3.py` en de sectie in `analytics/CLAUDE.md`. De prompt hieronder is alleen nog achtergrond.

Geef Claude Code in `analytics/` de prompt onderaan. Alles hierboven is de
onderbouwing, gecontroleerd op 2026-11-02 tegen de echte unit 3-logs (blok 1
compleet, blok 2 bezig).

## Wat er anders is dan bij unit 2

| Onderwerp | Unit 2 | Unit 3 | Gevolg voor de code |
|---|---|---|---|
| Datamap | `BatPi_Download\ul18_12_unit2\` | `BatPi_Download\LOGBATTEST\ul18_12_unit3\` (gevuld door `pi_daily_report.py`) | `LOG_DIR` anders opbouwen |
| Testday-bestand | `Block_01_OCV12p41V_testday_bdps_<ts>.csv` | `Block_01_SoC070_OCV12p80V_testday_bdps_<ts>.csv` | `_V2_TESTDAY_PAT` vindt nu **0 van de runs**; moet een optioneel `SoC<nnn>_` accepteren |
| SoC van een run | `_v_to_soc()` uit de OCV-tabel | Coulomb-geteld: `soc_cc_pct` in `_sweep_points.csv` | Niet de OCV-tabel gebruiken: 13,36 V na 30 min rust valt buiten de tabel, en 10 % SoC is hier 12,04 V |
| SOH-referentie | nameplate 18 Ah | Blok 1 in `_soh_reference.json` (15,247 Ah) | SOH % t.o.v. die referentie; stoplijn op 50 % |
| Blok | 10 degradatiecycli | 5 cycli, plus stages `rest_before_soh` en `*_rest_*` | Bestaande `_DEGR_PAT` werkt; bestandsinventaris uitbreiden |
| Boekhouding | geen | `_block_summary.csv`, `_sweep_points.csv`, `_events.csv`, `_soh_reference.json`, `_checkpoint.json` | Gebruiken als bron voor status en sweep |

## Valkuilen die al zichtbaar zijn in de data

1. **Profielstromen volgen het setpoint niet (ernstig, zie hieronder).**
2. **Verouderde voorlopige bestanden.** De Pi hernoemt `Block_01_SoC060_stepdown_*`
   naar `Block_01_SoC060_OCV12p68V_stepdown_*` zodra de OCV bekend is. De sync
   wist lokaal niets, dus de oude naam blijft op de laptop staan als duplicaat
   (nu: `Block_01_SoC060_stepdown_*`, `Block_02_SoC100_rest_*`). Bestanden met
   `_stepdown_`/`_rest_` zonder `OCV`-token moeten worden genegeerd.
3. **C/5 verschilt tussen Pi en analyse.** Blok 1: 15,247 Ah (Pi) tegen 15,452 Ah
   (`find_block_soh`); blok 2: 15,31 tegen 15,515 Ah, dus steeds ~0,2 Ah (1,3 %).
   De oorzaak is niet onderzocht. De Pi-waarde bepaalt de stopgrens, dus toon
   die als primair en toon het verschil.
4. **`wakeup_load_2_R_int_mohm`** is ook bij unit 3 negatief (−115…−178 mΩ):
   dezelfde bekende V_ref-bug. Niet tonen als feature.
5. **`step_hit_cutoff` en lopende runs.** Het logbestand van een lopende run is
   onvolledig. `cutoff_hit` vergelijkt het aantal events met het maximum, dus
   een run in uitvoering kan als afgebroken verschijnen.

## Probleem: ontlaadstromen in het profiel

Gemeten in `Block_01_SoC100_...testday` en `Block_01_SoC060_...testday` tegen
`lab-control/LabSetupV2/test_day_profile_new.csv`:

| Event | Setpoint | Gemeten, 100 % SoC | Gemeten, 60 % SoC |
|---|---|---|---|
| `wakeup_load` (1) | −1 A | −0,33 A | **−3,60 A** |
| `glow_plug_like_load` | −8 A | −0,33 A | **−3,60 A** |
| `crank_pulse` (koud) | −75…−38 A | −0,33 A | **−3,60 A** |
| `driving_aux_load` | −10…−1 A | −0,33 A | **−3,60 A** |
| `ramp_like_load` | −30…0 A | −0,33 A | **−3,60 A** |
| `alternator_charge` | +1,8…+4,5 A | tot +4,50 A | +1,82…+4,50 A (volgt wél) |

−3,6 A is de stroom van de stepdown vlak daarvoor. De Pi-commit `994fc95` (fix
met settling en readback, "not yet confirmed against real hardware") heeft dit
dus niet opgelost. De journal geeft 18 keer `BDPS did not confirm current
setpoint`, met readback 0,00 of +1,81 A: `SOUR:CURR?` wordt nooit negatief. Unit 2
laat hetzelfde zien (`wakeup_load` −9,0 A bij een setpoint van −1 A).

Gevolg: de SoC-sweep, de C/5-metingen en de degradatie zijn wel betrouwbaar,
maar alle features die op een ontlaadevent leunen niet (wake-up, gloeibougie,
crank, `driving_aux_load`, ramp). Het dashboard mag die niet presenteren alsof
ze kloppen. De oorzaak en oplossing horen op de Pi, niet in dit dashboard.

## Update 2026-11-02: oorzaak en nieuw profiel

De oorzaak is gevonden: de sink volgt alleen `SOUR:CURR:NEG`, en sinds profiel v2
(2 juli) werd die niet meer per stap gezet. De Pi krijgt de fix, en de koude crank
wordt afgekapt op −60 A in een nieuw profielbestand, omdat de BDPS `-75` weigert.

Gevolgen voor dit dashboard:

- Kies het setpoint-profiel per run op `Profile_Hash`, niet op één vaste bestandsnaam:
  oude runs horen bij `test_day_profile_new.csv` (crankpiek −75 A), nieuwe bij het
  −60 A-bestand.
- Runs vóór de rij `profile_replay_fixed` in `_events.csv` hebben ongeldige
  ontlaadevents; runs daarna horen de Setpoint-check te halen. "Alle ontlaadevents
  afgekeurd" geldt dus alleen voor die oude runs.
- `_sweep_points.csv` krijgt voor blok 2 dubbele rijen, omdat die sweep opnieuw
  begint, en nieuwe kolommen `profile_tracking_ok`/`max_tracking_error_a`. Gebruik
  per (block, run) de laatste rij, en sta lege waarden toe in oudere rijen.
- De warme crank haalt −35,6 A in plaats van −55 A: de output staat ervoor uit en de
  stroom is pas na ~0,1 s op niveau. Dat is bekend gedrag: toon het in de Setpoint-check,
  maar keur de run er niet om af, en reken met de gemeten stroom.
- De koude crank start vanuit de gloeibougielast (−8 A), dus `crank_cold_R_int_apparent`
  met I_peak onderschat R (24,7 tegen ~28,5 mΩ met ΔI, blok 2 bij 100 %). Toon ook een
  variant met ΔI = I_peak − I_pre; laat de bestaande feature ongewijzigd.

## Prompt

```text
Bouw een dashboard voor de lab-accu ul18_12_unit3. Lees eerst
analytics/CLAUDE.md en analytics/HANDOFF_unit3_dashboard.md. Unit 3 mag nooit
gepoold worden met unit 2 of old_ul18_12.

Er staan ongecommitte wijzigingen van de gebruiker in de werkmap, onder meer in
battery_feature_dashboard_unit2.py, testday_v2_features.py, main_dashboard.py en
analytics/CLAUDE.md. Niet terugdraaien en niets meecommitten dat niet van jou is.
Commit alleen als de gebruiker daarom vraagt.

1. testday_v2_features.py
   - Breid _V2_TESTDAY_PAT uit met een optioneel (?:SoC(?P<soc>\d+)_)? vóór OCV,
     en geef de doel-SoC mee in find_v2_testday_runs(). Oude bestandsnamen van
     unit 2 en old_ul18_12 moeten exact dezelfde runs blijven geven: controleer
     het aantal runs vóór en na de wijziging.
   - Let op de feature-cache (.v2_feature_cache): bestaande resultaten mogen niet
     veranderen.

2. Nieuw bestand battery_feature_dashboard_unit3.py
   - Begin vanuit battery_feature_dashboard_unit2.py, zoals de repo per accu een
     eigen script heeft. Poort 5011. BATTERY_ID = "ul18_12_unit3".
   - LOG_DIR = os.path.join(DATA_DIR, "LOGBATTEST", BATTERY_ID), met dezelfde
     BATPI_DATA_DIR / _KNOWN_DATA_DIR-logica.
   - Negeer *_stepdown_* en *_rest_* zonder OCV-token in de bestandsnaam.
   - SoC per run komt uit _sweep_points.csv (koppel via testday_file, anders via
     block + run/target). Niet uit _v_to_soc(). Blijft een rij weg, toon dan de
     doel-SoC uit de bestandsnaam en markeer dat.
   - SOH: primair _block_summary.csv (soh_c5_ah, soh_pct_of_ref), referentie uit
     _soh_reference.json, horizontale lijn op 50 %. Toon find_block_soh ernaast
     als controle, inclusief het verschil van ~0,2 Ah.
   - Alle bestanden kunnen ontbreken of half geschreven zijn, want de test loopt
     nog. Niets mag crashen. Behoud de Rescan-knop.
   - Markeer lopende runs (minder events dan het profiel en het nieuwste bestand)
     apart in plaats van als afgebroken, en sluit ze uit van trends.

3. Behoud de tabs van unit 2 (Run Inspector, Compare Runs, SoC Sweep,
   Degradation Trends, Feature Correlations, SOH History, Degradation Cycles,
   File Inventory, Export) en voeg toe:
   - "Campagne": positie uit _checkpoint.json, leeftijd van de heartbeat, SOH per
     blok met lineaire schatting tot 50 %, degr_charge_timeouts,
     unplanned_gap_h en _events.csv.
   - "Sweep-kwaliteit": target_soc_pct tegen soc_cc_pct per blok,
     ocv_after_rest tegen SoC per blok (de OCV–SoC-curve die verschuift met
     veroudering), profile_net_ah, step_hit_cutoff.
   - "Setpoint-check": per run en per event gemeten stroom tegen het setpoint uit
     test_day_profile_new.csv. Match op Event_Index, vergelijk min/max/mediaan en
     de tracking-fout. Zet Profile_Hash erbij en waarschuw als de hash niet bij
     het profielbestand past.

4. Data-kwaliteit
   - Een run krijgt per ontlaadevent een vlag discharge_tracking_ok (mediaan
     gemeten stroom binnen 10 % van het setpoint, minimaal 0,2 A; gebruik voor de
     crank alleen de eerste 0,3 s niet als maatstaf).
   - Laat in Feature Correlations en Degradation Trends standaard alleen features
     zien waarvan de events de check doorstaan, met een schakelaar "toon ook
     ongeldige", en een duidelijke banner bovenaan zolang runs afgekeurd worden.
     Op dit moment worden alle ontlaadevents afgekeurd: dat is correct gedrag.
   - wakeup_load_2_R_int_mohm niet als feature aanbieden.

5. Koppelen en documenteren
   - Voeg "Lab — unit3" lazy toe aan main_dashboard.py, op dezelfde manier als
     _build_lab_unit2.
   - Werk de poorttabel en "Two batteries" (nu drie) in analytics/CLAUDE.md bij.

6. Controleren
   - Start het dashboard tegen de echte data en open elke tab. Blok 1 moet 10 runs
     geven met SoC 100…10 %, 15,247 Ah als referentie, en de Setpoint-check moet
     de waarden uit de tabel in dit handoff-bestand reproduceren.
   - Start ook battery_feature_dashboard_unit2.py en controleer dat het aantal
     runs ongewijzigd is. Rapporteer wat je niet hebt kunnen controleren.

Niet doen: niets op de Pi wijzigen of de BDPS aansturen, geen export-bundle of
lab-veldvergelijking voor unit 3 maken (daarvoor zijn de features nog niet
geldig), main.tex niet aanpassen.
```
