# Pi-handoff: testplan v3 — 5 cycli per blok, stop bij 50 % SOH

Geef dit hele bestand aan Claude Code op de Raspberry Pi
(`/home/pi/PhD_PiCode/LabSetupV2`). Opgesteld 2026-10-29 op basis van de
analyse van de volledige `ul18_12_unit2`-campagne (20 blokken, 2026-07-03 →
2026-08-02).

---

## Prompt voor Claude Code (kopieer vanaf hier)

Je werkt in `/home/pi/PhD_PiCode/LabSetupV2` (lees eerst `../CLAUDE.md`).
We gaan het full-plan in `backend.py` aanpassen voor een nieuwe
degradatietest op een **nieuwe** Ultracell UL18-12. Draai niets op de
hardware en herstart de dashboard-service niet zolang er een test loopt;
controleer dat eerst (`tmux ls`, `systemctl status battery-dashboard`, en of
`running_test` actief is in de console). Bestaande methodes in
`Classes/functions_class.py` mogen uitgebreid worden, maar houd ze
backward-compatible (de oude aanroepen moeten blijven werken).

### Achtergrond — waarom deze wijzigingen

Analyse van de vorige campagne (`ul18_12_unit2`) liet zien:

1. **Capaciteitsherstel na onbedoelde rust.** Op 2026-07-15 08:56 werd het
   Python-proces door de kernel gekilld (OOM, geheugenlek in
   `SensorReader.data_log`, inmiddels gefixt) midden in de laadfase van
   Block 5 / Degr 07. De accu stond daarna **30,6 uur** stil op gedeeltelijke
   lading, tot de test op 2026-07-16 15:33 handmatig werd hervat. Direct
   daarna steeg de afgegeven Ah per degradatie-ontlading van ~7,7–8,5 Ah naar
   ~9,4–11 Ah, en de SOH-C/5-meting van Block 6 kwam uit op 13,97 Ah tegen
   12,12 Ah in Block 5. Dat verklaart de niet-monotone SOH-curve. Temperatuur
   is uitgesloten (alle SOH-metingen 25–29 °C, Block 6 was juist koeler dan
   Block 5). Daarbij werd het plan na de crash niet automatisch hervat: het
   draaide in tmux, niet als systemd-service, en de hervatting vereist een
   klik op "Full Plan".
2. **Laadfasen werden afgekapt.** `charge_battery_cc_cv(duration_hours=3)` in
   de degradatiecycli stopte in Block 1–8 bij 56 van de 81 laadfasen op de
   3 h-limiet terwijl de stroom nog 0,4–1,35 A was (taper-criterium 0,3 A
   niet bereikt). De accu werd dus niet volledig geladen en cyclde op een
   steeds wisselende, gedeeltelijke SoC. Ook de sweep-lading (max. 4 h) liep
   in Block 6–7 tegen de limiet aan. Dat versterkt sulfatatie en maakt de
   degradatiesnelheid afhankelijk van hoe lang er geladen kon worden.
3. **Rust vóór de SOH-meting was niet vastgelegd.** De C/5-ontlading begon
   direct (~1 min) na de lading, zonder gestandaardiseerde rustperiode.

### Wijzigingen

#### 1. Configuratie (`backend.py`)

- `BATTERY_ID = "ul18_12_unit3"` (of een andere naam die de gebruiker
  opgeeft: **vraag dit na voordat je het invult**). Nieuwe accu = nieuwe map,
  zodat er geen oude `_checkpoint.json` wordt opgepakt.
- `CYCLES_PER_BLOCK = 5`
- `FULL_PLAN_BLOCKS = 40`: dit is nu alleen een veiligheidsmaximum. De test
  stopt normaal op het SOH-criterium (zie punt 2). Verwachting ±16 blokken.
- `TOTAL_DEGRADATION_CYCLES` blijft `FULL_PLAN_BLOCKS * CYCLES_PER_BLOCK`,
  maar pas de console-teksten aan zodat ze "cycle 37 (max 200)" tonen in
  plaats van "cycle 37/200", omdat het eindpunt niet meer vastligt.
- Nieuwe constanten:
  ```python
  SOH_STOP_FRACTION   = 0.50   # stop zodra C5 <= 50 % van de referentie (Block 1)
  REST_BEFORE_SOH_S   = 3600   # vaste open-klem-rust tussen volladen en SOH C/5
  DEGR_CHARGE_MAX_H   = 5      # was 3; veiligheidslimiet, taper is het normale einde
  FULL_CHARGE_MAX_H   = 6      # was 4; geldt voor charge_full en sweep_charge
  UNPLANNED_GAP_S     = 1800   # gat > 30 min bij hervatten = onbedoelde rust
  AUTO_RESUME         = True   # hervat full plan automatisch bij opstarten als er een checkpoint is
  ```
- Pas in `main_dashboard.py` de knopteksten aan ("5 Degradation Cycles",
  "Full Plan (tot 50 % SOH)") en lees ze bij voorkeur af uit de constanten.

#### 2. Stopcriterium op 50 % SOH

- Laat `_run_discharge_c5_block` de gemeten capaciteit teruggeven (of
  `None` bij stop/fout), niet alleen `True/False`. Pas de aanroeper aan.
- Sla de referentiecapaciteit **persistent** op in
  `LOG_ROOT/<battery_id>/_soh_reference.json`
  (`{"block": 1, "capacity_ah": ..., "measured_at": ...}`). Schrijf dit
  alleen bij Block 1, en alleen als de C/5-ontlading echt tot 10,8 V is
  gekomen (niet bij stop of exception). Bij hervatten in Block > 1 zonder
  referentiebestand: **halt** met een duidelijke melding. Kies niet stilletjes
  een latere meting als referentie.
- Houd een overzicht bij in `LOG_ROOT/<battery_id>/_block_summary.csv` (append,
  één rij per blok) met: `block, soh_c5_ah, soh_pct_of_ref,
  soh_start, soh_end, rest_before_soh_s, ocv_before_soh, unplanned_gap_h,
  degr_charge_timeouts, battery_id`.
- Na elke SOH-meting: `ratio = cap / ref`. Log dit duidelijk
  (`Block 7: SOH = 12.31 Ah = 67.8 % van referentie`).
  Als `ratio <= SOH_STOP_FRACTION`:
  1. log een banner `STOP CRITERION REACHED — SOH xx.x % <= 50 %`;
  2. voer de **SoC-sweep van dit blok nog wel uit** (dan heb je ook features op
     het eindpunt), maar sla de degradatiecycli over;
  3. laad de accu daarna vol (zodat hij niet leeg blijft staan) en wis het
     checkpoint;
  4. sluit netjes af via `_finish()`.
- Het checkpoint krijgt een extra stage-waarde of vlag zodat een herstart
  na het bereiken van het criterium niet opnieuw degradatiecycli start.

#### 3. Vaste rust vóór de SOH-meting

- Voeg in `_run_full_plan` tussen `full_charge` en `soh_c5` een stage
  `rest_before_soh` toe (ook in `FULL_PLAN_STAGE_ORDER` en dus in de
  checkpoint-logica). Daarin: output uit, `REST_BEFORE_SOH_S` seconden rust,
  checkt elke seconde `stop_event`, logt elke 10 s V (BDPS `readVoltage`) naar
  `Block_<nn>_rest_before_soh_bdps_<ts>.csv` met Mode `REST`, en logt de
  eind-OCV naar console en block summary.
- Gebruik ervoor een generieke helper `_rest(seconds, log_path)` die je later
  ook elders kunt inzetten.

#### 4. Laden tot de accu echt vol is

- `charge_battery_cc_cv` krijgt via de constanten een langere max-duur
  (degradatie: `DEGR_CHARGE_MAX_H`, full/sweep: `FULL_CHARGE_MAX_H`). Het
  normale einde blijft CV-modus met I < 0,3 A (≈ C/60 voor een nieuwe accu,
  en bij 50 % SOH nog ≈ C/30, dus prima zolang we bij 50 % stoppen).
- Leg de reden van beëindiging vast zonder de return-waarde te breken: zet
  bijv. `self.last_charge_end_reason = "taper" | "timeout" | "stopped"` in
  `bdps_control`. Log in de backend een **waarschuwing** bij `timeout`, en tel
  de timeouts per blok voor `_block_summary.csv`.

#### 5. Onbedoelde rust detecteren en voorkomen

- **Heartbeat:** laat een daemon-thread (of de bestaande meetlussen) elke
  60 s `last_alive` bijwerken in `_checkpoint.json` (atomisch schrijven:
  eerst naar `.tmp`, dan `os.replace`).
- **Bij hervatten:** bereken `gap = now - last_alive`. Is
  `gap > UNPLANNED_GAP_S`, dan:
  - log een banner met de duur van het gat;
  - schrijf een rij naar `LOG_ROOT/<battery_id>/_events.csv`
    (`timestamp, event, block, stage, cycle, gap_h`);
  - doe **eerst een volledige lading** (taper-criterium) voordat de
    onderbroken stage opnieuw begint, zodat de accu niet vanaf een
    onbekende, gedeeltelijke SoC verder cyclet;
  - zet `unplanned_gap_h` voor dit blok in de block summary, zodat de
    analyse de eerstvolgende SOH-meting kan markeren.
- **Auto-resume:** als `AUTO_RESUME` aan staat en er bij het opstarten van de
  backend een checkpoint bestaat, controleer dan dat `LOG_ROOT` gemount en
  schrijfbaar is en dat de BDPS reageert (`MEAS:VOLT?`). Start daarna
  `start_full_plan()` automatisch. Lukt een van die checks niet, start dan
  niets en log waarom.
- **Draai de test als systemd-service, niet in tmux.** `battery-dashboard.service`
  heeft al `Restart=on-failure`. Zet `RestartSec=30` en
  `StartLimitIntervalSec=0`, en controleer dat de service enabled is. Een
  OOM-kill (SIGKILL) telt als failure, dus de service komt dan vanzelf terug.
  Leg in `../CLAUDE.md` uit hoe je logs bekijkt
  (`journalctl -u battery-dashboard -f`).
- Optioneel: een loggingrate-waakhond. Als de mediane interval tussen
  BDPS-logregels in een charge/discharge langer dan 5 min boven 5 s ligt,
  log je een waarschuwing met `ps`-geheugengebruik (vroege signalering van
  een regressie zoals in juli).

#### 6. Testen voordat het op de hardware draait

- `python -m py_compile backend.py main_dashboard.py Classes/*.py`
- Schrijf `tests/test_plan_logic.py` met een `FakeBDPS` (implementeert
  `charge_battery_cc_cv`, `discharge_cc_until_voltage`,
  `discharge_cc_to_ocv_target`, `readVoltage`, `outputOff`, ... en geeft
  meteen een capaciteit terug uit een lijst, bijv. `[18.0, 16.5, 14.0, 9.5, 8.9]`)
  en monkeypatch `time.sleep`, `LOG_ROOT` (tmp-map), `OCV_REST_S` en
  `REST_BEFORE_SOH_S`. Test minimaal:
  1. de plan stopt na het blok met 8,9 Ah (≤ 50 % van 18,0), voert de sweep
     van dat blok nog uit en draait geen degradatiecycli meer;
  2. 5 degradatiecycli per blok en globale nummering 1–5, 6–10, ...;
  3. hervatten midden in blok 3 / cyclus 4 gaat verder bij cyclus 4;
  4. hervatten met `last_alive` 2 uur geleden geeft een `_events.csv`-rij en
     een extra volledige lading vóór de hervatte stage;
  5. ontbrekend `_soh_reference.json` bij hervatten in Block > 1 geeft een halt;
  6. `rest_before_soh` staat in de checkpoint-volgorde en wordt bij hervatten
     correct opnieuw gedaan.
- Werk `../CLAUDE.md` bij (constanten-tabel, full-plan-volgorde, nieuwe
  bestanden `_soh_reference.json`, `_block_summary.csv`, `_events.csv`,
  stage `rest_before_soh`, stopcriterium, auto-resume).
- Commit op een aparte branch en laat de gebruiker de diff zien voordat de
  service herstart wordt.

### Nieuwe volgorde per blok

`full_charge` (taper, max 6 h) → `rest_before_soh` (1 h) → `soh_c5`
(→ stopcheck) → `soc_sweep` → `degradation_cycles` (5×: C/2-ontlading tot
10,8 V + CC/CV-lading tot taper, max 5 h).

(kopieer tot hier)

---

## Verwachte testduur (berekend op de data van `ul18_12_unit2`)

Methode: uit de ruwe BDPS-logs (`BatPi_Download/ul18_12_unit2`) is per blok
de duur van de overhead (vollading + SOH + sweep-lading + 10 test-day-runs +
step-downs) en de duur per degradatiecyclus bepaald, beide als functie van de
gemeten capaciteit C (Block 5 is weggelaten vanwege het crashgat):

- overhead per blok ≈ 1,00 h/Ah · C + 3,2 h (≈ 21 h bij 18 Ah, ≈ 12 h bij 9 Ah)
- degradatiecyclus ≈ 0,24 h/Ah · C + 0,5 h (≈ 4,9 h bij 18 Ah, ≈ 2,7 h bij 9 Ah)

De oude test bereikte 50 % SOH (9,08 Ah) bij ≈ 87 volledige cycli
(SOH-ontlading + degradatiecycli; de sweep plus degradatiecyclus 1 tellen samen
als één cyclus). Dat lag tussen Block 8 (10,21 Ah) en Block 9 (8,97 Ah), na
±80 degradatiecycli. Met 5 cycli per blok is één blok ≈ 6 volledige cycli, dus:

| Scenario | Laatste SOH-meting | Duur |
|---|---|---|
| Zelfde degradatiesnelheid, oude laad-/rusttijden | Block 16 | ≈ 22 dagen (520 h) |
| + 1 h rust vóór SOH + langere laadfasen (≈ +0,5 h/cyclus) | Block 16 | **≈ 24 dagen (575 h)** |
| Accu degradeert 20 % sneller | Block 13 | ≈ 19 dagen |
| Accu degradeert 20 % langzamer (bijv. door volledig laden) | Block 19 | ≈ 29 dagen |

**Reken op ongeveer 3,5 weken (19–29 dagen)**, met ±15 SoC-sweeps in plaats
van 8–9 tot het 50 %-punt. Het aantal degradatiecycli tot 50 % blijft ongeveer
gelijk (±75–80), maar het aantal blokken (en dus sweeps/overhead) verdubbelt.

Kanttekeningen:
- Het is een andere accu; spreiding tussen exemplaren kan groter zijn dan ±20 %.
- Volledig laden (punt 4) gaat sulfatatie tegen en kan de test daardoor
  verlengen. Het oude verloop (−23 % in de eerste 20 cycli) is voor VRLA vrij
  steil, en de afgekapte laadfasen hebben daar waarschijnlijk aan bijgedragen.
- Nieuwe VRLA-accu's winnen soms in de eerste cycli nog capaciteit
  ("formatie"). Hier was Block 1 het maximum, maar valt Block 2 bij de nieuwe
  accu hoger uit, overweeg dan `max(Block 1, Block 2)` als referentie. Dat is
  een keuze die je vóór de start moet maken.
- Elke onbedoelde stilstand komt er bovenop. De gemiddelde blokduur is ±1,5
  dag, dus een crash die een nacht + dag onopgemerkt blijft, kost ±10 %.

---

# Deel 2 — SoC-sweep op Ah-basis in plaats van OCV-doelen (toegevoegd 2026-10-30)

## Analyse: waarom de SoC-punten dicht op elkaar lagen

Per test-day-run van `ul18_12_unit2` is de werkelijke SoC teruggerekend met
Coulomb counting vanaf de volle sweep-lading. Dat is gedaan relatief aan de
C/5-capaciteit van datzelfde blok, met de gemeten Ah uit de BDPS-logs van de
step-downs én de profielen:

| Blok | C5 (Ah) | Werkelijke SoC bij de 10 runs (%) | OCV-label (V) |
|---|---|---|---|
| 1 | 18,2 | 100, 67, 58, 50, 41, 36, 32, 23, 17, 12 | 13,54 → 11,73 |
| 3 | 13,9 | 100, 62, 51, 39, 33, 27, 21, 15, 8, 2 | 13,39 → 11,80 |
| 6 | 14,0 | 100, 52, 40, 29, 23, 17, 11, 7, 4, 2 | 13,61 → 11,83 |
| 8 | 10,2 | 100, 34, 18, 11, 6, 4, 2, 0, −1, −2 | 13,56, 12,49, 12,28 … 12,18 |
| 9 | 9,0 | 100, 33, 17, 10, 7, 5, 4, 3, 2, 1 | 13,55, 12,57, 12,35 … 12,34 |
| 10 | 7,4 | 100, 18, 8, 3, 1, −1 … −7 | 13,59, 12,54, 12,50 … 12,45 |

Een negatieve SoC betekent dat er meer Ah is afgegeven dan de C/5-capaciteit.
Dat kan bij de lage gemiddelde stroom van het profiel, maar in de praktijk is
de accu dan leeg.

Er zijn vier oorzaken, in volgorde van impact:

1. **De OCV-tabel klopt niet voor deze AGM-accu, vooral bovenin.** Na 120 s
   rust na een lading op 14,4 V leest de accu 13,4–13,6 V (oppervlaktelading).
   De pulse-loop ontlaadt daarom door tot de OCV ≤ 12,58 V ("90 %"), en dat
   kost elke keer 5–7 Ah: 33 % van de capaciteit in blok 1, 66 % in blok 8 en
   82 % in blok 10. Het eerste "90 %"-punt ligt in werkelijkheid op 67 %
   (blok 1) tot 18 % (blok 10). Een volle UL18-12 zit na echte rust eerder
   rond 12,9–13,0 V dan op de aangenomen 12,70 V.
2. **De OCV–SoC-relatie verschuift met veroudering.** Vanaf blok 8 is de accu
   (Coulomb-geteld) al leeg, terwijl de OCV nog 12,2–12,7 V aangeeft. Volgens
   de tabel is dat 50–90 %. De step-down ziet dan "OCV ≤ doel" en ontlaadt
   niet meer, dus alle volgende profielen draaien op dezelfde lege accu. De
   labels clusteren daardoor rond 12,2–12,5 V. Het profiel kan zijn stroom
   niet meer leveren (netto Ah per run zakt van 0,84 naar 0,08 Ah) en de
   crank-Vmin zakt tot ~9,9 V.
3. **Het profiel zelf bepaalt de stapgrootte.** Een test-day-run haalt gemeten
   ~0,84 Ah netto uit de accu (1,03 Ah ontladen, 0,20 Ah geladen). Na de run
   ligt de OCV na 120 s vaak al onder het volgende doel, dus er wordt niet
   ontladen ("rest-first"). Het interval wordt dan 0,84 Ah in plaats van 10 %:
   dat is 4,6 % bij 18 Ah en 8 % bij 10 Ah.
   *Neveneffect om te controleren:* volgens `test_day_profile_new.csv` zou het
   profiel netto maar 0,27 Ah moeten ontladen (0,46 Ah ontladen, 0,20 Ah
   geladen). Gemeten is 1,03 Ah ontladen, vooral in `wakeup_load` (0,42 Ah)
   en `driving_aux_load` (0,45 Ah). Ergens in de replay ontstaat dus ongeveer
   2× zoveel ontlaadstroom als bedoeld.
4. **De stappen zijn grof en de rust is kort.** Eén puls is 300 s × 9 A =
   0,75 Ah (4 % bij 18 Ah, 8 % bij 9 Ah), dus het doel wordt nooit precies
   geraakt. Daarnaast is 120 s rust te kort voor een stabiele OCV: na een
   cutoff stijgt de OCV binnen een paar minuten nog 0,2–0,3 V.

Ook met het nieuwe stopcriterium (50 % SOH ≈ 9 Ah) speelt dit nog. Blok 8–9
van de vorige test, die rond dat punt lagen, hadden precies dit gedrag
(100, 34, 18, 11, 6, … %).

Het oorspronkelijke argument voor OCV in `../CLAUDE.md` ("1,8 Ah vast is
betekenisloos bij een verouderde accu") geldt voor stappen op basis van de
*nominale* 18 Ah. Het geldt niet voor stappen op basis van de C/5-capaciteit
die in hetzelfde blok vlak vóór de sweep is gemeten. Die is in het lab bekend.
De OCV blijft gewoon gelogd als veld-observeerbaar label en feature.

## Prompt 2 voor Claude Code (kopieer vanaf hier)

Lees `../CLAUDE.md` en dit bestand (deel 1 en 2). Pas de SoC-sweep in
`backend.py` (`_run_soc_sweep`) aan: **stap af op Ah relatief aan de
C/5-capaciteit van hetzelfde blok** in plaats van op OCV-doelwaarden. De OCV
wordt alleen nog gemeten en gelogd als label/feature, want dat is wat in het
veld beschikbaar is. Hij wordt niet meer gebruikt om te sturen. Draai niets
op de hardware zolang er een test loopt.

1. **Constanten**
   ```python
   SWEEP_MODE          = "ah"    # "ocv" = oude methode als terugvaloptie
   SWEEP_SOC_TARGETS   = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10]  # % van C5 van dit blok
   SWEEP_STEP_CURRENT  = 3.6     # A (C/5): zelfde rate als de SOH-meting, minder polarisatie dan 9 A
   SWEEP_REST_S        = 1800    # vaste open-klem-rust vóór elke profielrun (ook de 100 %-run)
   SWEEP_CUTOFF_V      = 10.8
   ```
   Laat `OCV_SOC_TABLE`, `OCV_PULSE_S` en `discharge_cc_to_ocv_target` bestaan
   (niet verwijderen). Ze worden alleen gebruikt als `SWEEP_MODE == "ocv"`.
2. **Referentiecapaciteit.** Gebruik de C/5-capaciteit die in **hetzelfde
   blok** net vóór de sweep is gemeten (de return-waarde van
   `_run_discharge_c5_block` / `_block_summary.csv`). Bij hervatten midden in
   een sweep lees je hem uit `_block_summary.csv`. Bij een standalone sweep
   (via de knop) neem je de laatste C/5 uit `_block_summary.csv`. Is die er
   niet, log dan een waarschuwing en gebruik 18,0 Ah nominaal.
3. **De profielrun telt mee.** Laat `_run_profile_sync` ook de **gemeten**
   netto Ah vastleggen in `self.last_profile_net_ah`: integreer de gemeten
   BDPS-stroom × de werkelijke dt per sample, met ontladen negatief. Laat de
   return-waarde True/False ongewijzigd. Gebruik niet de nominale waarde uit
   de CSV: gemeten is ~0,84 Ah netto, nominaal maar 0,27 Ah.
4. **Sweep-volgorde**
   - Doe de sweep-lading (taper, max `FULL_CHARGE_MAX_H`) en zet
     `removed_ah = 0`.
   - Voor elk doel `s` in `SWEEP_SOC_TARGETS`:
     - `target_removed = (100 - s) / 100 * C5_block`
     - `step = target_removed - removed_ah`. Is `step > 0.05` Ah, ontlaad dan
       met de bestaande methode `bdps.discharge_cc_fixed_ah(current=SWEEP_STEP_CURRENT,
       target_ah=step, cutoff_voltage=SWEEP_CUTOFF_V, ...)` en tel de werkelijk
       ontladen Ah op bij `removed_ah`. Is `step ≤ 0.05` (het profiel heeft al
       genoeg weggehaald), sla de ontlading over en log dat.
     - Haalt de step-down 10,8 V voordat `step` bereikt is (afgegeven Ah
       < `step` − 0,05), log dat dan, draai **geen** profiel meer en beëindig
       de sweep netjes (return True).
     - Rust `SWEEP_REST_S` met de output uit, check `stop_event` elke seconde,
       log V elke 10 s met Mode `REST` en lees aan het eind de OCV.
     - Draai het profiel en tel `-self.last_profile_net_ah` op bij `removed_ah`.
   - Log per run in de console: doel-SoC, werkelijke SoC vóór de run
     (`100 * (1 - removed_ah / C5_block)`), OCV na rust en profiel-netto-Ah.
5. **Bestandsnamen en logging.** Gebruik de doel-SoC in de naam met de OCV
   erachter, zodat de bestaande analyse-regex `OCV(\d+)p(\d+)V` blijft werken:
   `Block_<nn>_SoC070_OCV12p41V_testday_bdps_<ts>.csv`, en idem voor
   `_stepdown_` en `_rest_`. Schrijf daarnaast per run een rij naar
   `LOG_ROOT/<battery_id>/_sweep_points.csv` met: `block, run, target_soc_pct,
   soc_cc_pct, removed_ah_before_run, c5_block_ah, ocv_after_rest, rest_s,
   profile_net_ah, step_ah, step_hit_cutoff, testday_file, battery_id`.
6. **Checkpoint.** Sla binnen de sweep `sweep_run` en `removed_ah` op in het
   checkpoint, zodat een hervatting midden in de sweep weet waar hij is. Bij
   een onbedoeld gat (deel 1, punt 5) start de sweep na de extra volle lading
   opnieuw vanaf 100 %, want de Coulomb-telling klopt dan niet meer.
7. **Profiel-replay controleren (alleen rapporteren).** Vergelijk voor één
   bestaande test-day-log de gemeten stroom per `Event_Type` met de setpoints
   uit `test_day_profile_new.csv`. Neem bijvoorbeeld
   `ul18_12_unit2/Block_01_OCV12p44V_testday_bdps_*.csv`, als die op de USB
   staat. Gemeten is ~1,03 Ah ontladen tegen 0,46 Ah nominaal, vooral in
   `wakeup_load` en `driving_aux_load`. Zoek uit waar het verschil ontstaat:
   blijft een setpoint hangen na een polariteitswissel, of werkt de
   snap-to-zero onder 0,2 A niet goed? Rapporteer en stel een fix voor, maar
   **pas de replay pas aan na akkoord van de gebruiker**.
8. **Tests.** Breid `tests/test_plan_logic.py` uit met een `FakeBDPS` waarvan
   `discharge_cc_fixed_ah` precies `target_ah` teruggeeft (of minder, om een
   cutoff na X Ah te simuleren), en een nep-profiel dat −0,84 Ah netto geeft.
   Controleer:
   - de 10 runs liggen op een werkelijke SoC van 100, 90, …, 10 % (±1 %), zowel
     bij C5 = 18 Ah als bij C5 = 9 Ah;
   - stappen worden overgeslagen als het profiel al genoeg heeft weggehaald;
   - bij een cutoff halverwege stopt de sweep zonder verdere profielruns;
   - hervatten met `sweep_run = 6` gaat verder bij run 6, met de juiste
     `removed_ah`;
   - `SWEEP_MODE = "ocv"` geeft nog het oude gedrag.
9. Werk `../CLAUDE.md` bij. De sectie "SoC Sweep — OCV-Based Step-Down" wordt
   "SoC Sweep — Ah-based step-down", met een korte reden (de analyse
   hierboven), de nieuwe constanten en de nieuwe bestandsnamen. Commit op de
   branch en laat de diff zien.

(kopieer tot hier)

## Effect op de testduur

Per blok komt er 10 × 28 min extra rust bij (≈ 4,7 h). De step-downs gaan op
C/5 in plaats van C/2. De totale Ah per sweep blijft ≈ 0,9 × C5, maar het
ontladen duurt langer: ≈ 1–2 h extra per blok. Bij ±16 blokken is dat
**≈ +4 dagen**. De verwachte totale duur gaat daarmee van ≈ 24 naar
**≈ 28 dagen** (bereik 23–33). Met `SWEEP_REST_S = 900` wordt het ≈ +2 dagen.
De rust moet wel voor elke run gelijk zijn, anders zijn de OCV-features
onderling niet vergelijkbaar.
