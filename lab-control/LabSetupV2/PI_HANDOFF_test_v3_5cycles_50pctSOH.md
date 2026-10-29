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
