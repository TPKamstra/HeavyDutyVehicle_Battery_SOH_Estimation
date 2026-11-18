# Opdracht voor Claude Code: features van unit3 beter uitwerken

**Doel.** Per feature moet in één oogopslag te zien zijn:
1. wat de feature meet;
2. of de waarde in een run geldig is;
3. hoe de feature zich gedraagt tegen SoC, ontladen lading en SOH;
4. of die SOH-relatie echt is of een artefact van de testopzet.

Werk alleen aan `ul18_12_unit3`: `analytics/battery_feature_dashboard_unit3.py` en
`analytics/testday_v2_features.py`. Lees eerst `analytics/CLAUDE.md` (vooral de
unit3-sectie) en `analytics/FEATURE_OVERVIEW.md`.

## Aanleiding: wat er nu misgaat

- **Ongeldige punten tellen gewoon mee.** Op 10 % SoC staat de accu na de
  gloeibougielast vlak boven de spanningsvloer van de sink (10,2 V). De koude
  crank trekt dan maar 20 A in plaats van 60 A (blok 4 en 5) of vrijwel niets
  (blok 7–9, V_pre = 10,197 V). `crank_cold_R_int_deltaI_mohm` komt dan uit op
  ~110 mΩ tegen 30–45 mΩ bij 20–100 % SoC. Dat is de vloer, geen weerstand. De
  run heet `voltage_limited` en houdt nu al zijn features. Dat is goed voor de
  spanningsfeatures, maar fout voor crankstroom, V_min en R_int.
- **Schijnbare SOH-correlaties.** SoC is een percentage van de capaciteit van
  elk blok. Een vast SoC-niveau betekent dus minder ontladen Ah naarmate de accu
  veroudert. De rust-OCV volgt de ontladen Ah (kwadratische fit: 15 mV RMS,
  tegen 101 mV op relatieve SoC). Absolute spanningen krijgen daardoor |r| = 1
  op blokgemiddelden. De voorspellertabellen hebben inmiddels `r_runs` en
  `r_charge_matched` (nog niet gecommit, zie `git diff`). Die moeten terugkomen
  in de nieuwe weergave.
- **Dubbele en constante kolommen.**
  - `total_ah_charged` is identiek aan `alternator_charge_after_cold_Ah`.
  - `crank_hot_R_int_apparent_mohm` is identiek aan `crank_hot_R_int_deltaI_mohm`
    (de warme crank start vanuit 0 A).
  - `total_ah_discharged`, `run_duration_s` en de crank-I_peak bij normale runs
    zijn vrijwel constant: ze volgen het profiel, niet de accu.

  Ze vervuilen de heatmap en de rankings.
- **Ruisniveau is onbekend.** De BDPS-spanning staat in stapjes van ~7 mV. Voor
  features die maar enkele mV bewegen (drift, sag) is niet duidelijk of het
  signaal boven die resolutie uitkomt.

## Wat er moet komen

### 1. Featurecatalogus als één bron (`testday_v2_features.py`)

Een dict `FEATURE_CATALOG` met per feature:
- event en meetvenster;
- formule en eenheid;
- fysische betekenis;
- verwachte richting bij veroudering;
- verwachte SoC-afhankelijkheid;
- geldigheidsregels (zie 2);
- veld-tegenhanger;
- `kind`: `feature` / `diagnostic` / `profile_constant` / `duplicate_of:<naam>`.

Neem de inhoud over uit `FEATURE_OVERVIEW.md` en controleer elke formule tegen
`extract_features()`. Het dashboard toont de catalogustekst bij de gekozen
feature. Een test controleert dat elke kolom die `extract_features()` oplevert
in de catalogus staat en andersom.

### 2. Geldigheid per feature per run (niet per run)

Leid per event een vlag af uit de setpointcheck en de ruwe data:
- event-status `voltage_limited` of `failed` (`_run_quality_impl`);
- `I_peak` < 90 % van het profiel-setpoint van dat event;
- V_pre binnen 0,3 V van de vloer van 10,2 V;
- |ΔI| te klein voor een weerstand (drempel kiezen en onderbouwen; geen vaste
  absolute waarde die met de veroudering wegloopt, zie de les in `CLAUDE.md`);
- te weinig samples in het venster.

Koppel features via de catalogus aan hun event. Een ongeldige feature wordt in
analyses NaN, met een kolom `<feature>__invalid_reason`. De spanningsfeatures van
hetzelfde event blijven geldig als hun eigen regels dat toelaten. Het bestaande
vinkje "Also show runs that failed the setpoint check" toont ook deze punten,
als grijze ✕ (`FLAGGED_MARKER`-conventie).

**Controle:**
- unit3 blok 3, 4, 5 en 10 op 10 % SoC: koude-crankweerstand ongeldig;
- unit3 blok 7–9 op 10 % SoC: crank ongeldig;
- alle runs op 20–100 % SoC: geldig.

### 3. Nieuw tabblad "Feature Explorer"

Eén feature kiezen, daarna onder elkaar:
1. **Definitie**: tekst uit de catalogus.
2. **Ruw signaal**: V en I van het bijbehorende event voor alle blokken op één
   gekozen SoC, over elkaar gelegd. Het meetvenster is gearceerd, zodat zichtbaar
   is waar de feature vandaan komt. Hergebruik `load_run()` en lees alleen de
   benodigde runs (uncached, zie de cache-les in `CLAUDE.md`).
3. **Feature tegen SoC**: één lijn per blok.
4. **Feature tegen ontladen Ah vanaf vol**: één lijn per blok. Bij een
   ladingsartefact vallen de lijnen hier samen.
5. **Feature tegen SOH**: één lijn per SoC-niveau.
6. **Kerngetallen**:
   - n geldig / n totaal;
   - r_soc, r_runs, r_charge_matched (uit de voorspellertabel);
   - verschuiving over de campagne tegenover de spreiding binnen een blok en
     tegenover de ADC-stap (een eenvoudige signaal-ruisverhouding);
   - een korte conclusie: "volgt lading", "volgt SOH", "ruis", "ongeldig bij lage SoC".

Ongeldige punten staan in elke grafiek als grijze ✕ en tellen niet mee in de
statistiek.

### 4. Opschonen

- Laat `profile_constant`- en `duplicate`-kolommen weg uit de heatmap, de
  scatter-keuzelijsten en de voorspellertabellen. Ze blijven wel in de
  Excel-export.
- Werk `FEATURE_OVERVIEW.md` bij vanuit de catalogus: met de hand, of genereer
  de tabel en plak hem in. Werk de unit3-sectie van `analytics/CLAUDE.md` bij,
  inclusief het vervallen van de regel "voltage_limited runs keep their features"
  voor crankfeatures.

## Randvoorwaarden

- Nooit batterijen poolen. Unit2 en het oude dashboard niet aanpassen, tenzij dat
  nodig is voor gedeelde code in `testday_v2_features.py`. Controleer dan dat die
  dashboards nog laden.
- Draai Python met `C:\Users\TPKam\miniconda3\envs\ddsm\python.exe`. Data staat in
  `C:\Users\TPKam\Documents\BatPi_Download\LOGBATTEST\ul18_12_unit3`. Niets op de
  Pi aanpassen: daar loopt een test.
- De featurecache (`analytics/.v2_feature_cache/`) kan oude rijen bevatten.
  Verander je `extract_features()`, verhoog dan de cache-sleutel of tag.
- Een draaiende `panel serve` toont geen nieuwe code. Herstart het dashboard op
  poort 5011 (`panel serve battery_feature_dashboard_unit3.py --port 5011`) en
  controleer elke nieuwe grafiek in de browser.
- Schrijf tests in `analytics/tests/` voor de geldigheidsregels: synthetische
  events plus de echte runs van blok 4 op 10 % en 20 % SoC.
- Commit pas na akkoord van de gebruiker, op een eigen branch.

## Klaar wanneer

- De 10 %-SoC-uitschieters van blok 3, 4, 5 en 10 staan als grijze ✕ in
  Degradation Trends, en de 10 %-lijn van de koude-crankweerstand springt niet
  meer.
- Feature Explorer werkt voor elke feature in de catalogus zonder fout.
- `rest_baseline_Vmean` toont in Feature Explorer zichtbaar dat de lijnen
  samenvallen tegen ontladen Ah, met conclusie "volgt lading".
- De tests slagen. `CLAUDE.md` en `FEATURE_OVERVIEW.md` zijn bijgewerkt.
