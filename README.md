# nrc-grabber

Een kleine Docker-container die de dagelijkse NRC-krant (PDF, ePub of mobi)
voor een abonnee-account downloadt en oude exemplaren opruimt, met een aparte
bewaartermijn voor zaterdag- en doordeweekse edities.

Standaard draait de container een **ingebouwde dagelijkse scheduler** (daemon-
modus): bij het opstarten draait hij direct één keer als inhaalslag, waarna hij
elke dag op een instelbaar lokaal tijdstip downloadt, een instelbaar aantal
keren opnieuw probeert als de run mislukte of de verwachte editie nog niet was
opgehaald, en zondagen overslaat (maandag heeft geen eigen editie, dus een
maandag-run downloadt niets tenzij `LOOKBACK_DAYS>0`, in welk geval hij nog
steeds andere ontbrekende edities in het venster kan aanvullen). De inhaalslag
bij het opstarten wordt overgeslagen als vandaag een overgeslagen weekdag is,
en als het geplande tijdstip van vandaag nog moet komen, wordt ook dat tijdstip
overgeslagen (de inhaalslag dekt vandaag al) zodat hij op dag één niet twee
keer draait. Zet `RUN_ONCE=1` om in plaats daarvan één enkele run uit te voeren
en af te sluiten, voor externe schedulers (cron, Kubernetes, systemd).

`LOOKBACK_DAYS=N` maakt van elke run (gepland of eenmalig) een inhaalslag: hij
downloadt **elke** beschikbare editie in het venster van vandaag plus de
voorgaande `N` dagen, niet alleen de meest recente. Edities worden
gededupliceerd op editie-identiteit, zodat zondag (die de zaterdageditie
serveert) nooit een duplicaat oplevert, en edities die al op schijf staan
worden overgeslagen.

## Configuratie (omgevingsvariabelen)

| Variabele | Standaard | Beschrijving |
| --- | --- | --- |
| `NRC_USERNAME` | verplicht | e-mailadres van het NRC-abonnement |
| `NRC_PASSWORD` | verplicht | wachtwoord van het NRC-abonnement |
| `FORMAT` | `pdf` | `pdf`, `epub` of `mobi` |
| `OUTPUT_DIR` | `/downloads` | waar bestanden worden opgeslagen (volume-mount) |
| `KEEP_SATURDAY` | `8` | aantal te bewaren zaterdagedities |
| `KEEP_WEEKDAY` | `14` | aantal te bewaren doordeweekse edities |
| `LOOKBACK_DAYS` | `0` | downloadt elke beschikbare editie in het venster van vandaag plus dit aantal voorgaande dagen (inhaalslag), gededupliceerd per editie |
| `TZ` | `Europe/Amsterdam` | tijdzone voor het bepalen van "vandaag" en voor het dagelijkse tijdstip van de scheduler |
| `RUN_ONCE` | `0` (onwaar) | waar (`1`/`true`/`yes`/`on`) draait één run en sluit af; onwaar (`0`/`false`/`no`/`off`/leeg) draait de dagelijkse scheduler |
| `RUN_AT` | `06:00` | dagelijks tijdstip `HH:MM` (24-uurs), in `TZ`; genegeerd als `RUN_ONCE` waar is (de scheduler draait daarnaast direct één keer bij het opstarten, zie hierboven) |
| `RETRY_DELAY_MINUTES` | `120` | aantal minuten na de geplande run waarna een nieuwe poging volgt, indien nodig |
| `RETRY_ATTEMPTS` | `1` | aantal nieuwe pogingen na de eerste dagelijkse run (`0` schakelt pogingen uit) |
| `SKIP_WEEKDAYS` | `sun` | door komma's gescheiden weekdagen die volledig worden overgeslagen (`mon,tue,wed,thu,fri,sat,sun` en/of `0`-`6`); leeg = niets overslaan; alle zeven wordt geweigerd |

Een nieuwe poging volgt alleen als de run mislukte, of als de voor die dag
verwachte editie (di-za: die dag; zondag: de voorafgaande zaterdag; maandag:
geen) niet is opgehaald.

## Bouwen

```bash
docker build -t nrc-grabber .
```

## Draaien

Daemon-modus (standaard): blijft draaien en downloadt dagelijks om `RUN_AT`:

```bash
docker run --rm \
  -e NRC_USERNAME=you@example.com \
  -e NRC_PASSWORD=secret \
  -e FORMAT=pdf \
  -e KEEP_SATURDAY=8 \
  -e KEEP_WEEKDAY=14 \
  -e RUN_AT=06:00 \
  -v "$PWD/downloads:/downloads" \
  nrc-grabber
```

Eenmalige modus, voor een externe scheduler (cron/k8s/systemd):

```bash
docker run --rm \
  -e NRC_USERNAME=you@example.com \
  -e NRC_PASSWORD=secret \
  -e RUN_ONCE=1 \
  -v "$PWD/downloads:/downloads" \
  nrc-grabber
```

## Inplannen (cron-voorbeeld, eenmalige modus)

```cron
30 5 * * * TZ=Europe/Amsterdam docker run --rm \
  -e NRC_USERNAME=you@example.com \
  -e NRC_PASSWORD=secret \
  -e RUN_ONCE=1 \
  -v /path/to/downloads:/downloads \
  ghcr.io/<owner>/nrc-grabber:latest
```

## CI

Een push naar `main` bouwt en publiceert de image naar GHCR als
`ghcr.io/<owner>/nrc-grabber:latest` en `ghcr.io/<owner>/nrc-grabber:<sha>`.
Er zijn geen secrets nodig; de workflow gebruikt de automatisch beschikbare
`GITHUB_TOKEN`.

## Opmerkingen

- De tool downloadt alleen waar een abonnee recht op heeft. Het is bedoeld voor
  persoonlijk archiefgebruik; verspreid de gedownloade inhoud niet verder.
- Zaterdag en zondag verwijzen allebei naar de zaterdageditie. Maandag heeft
  geen eigen editie; in de eenmalige modus (`RUN_ONCE=1`, `LOOKBACK_DAYS=0`)
  eindigt een maandag-run netjes zonder bestand. Met `LOOKBACK_DAYS>0` (of in
  de standaard dagelijkse scheduler, die hoe dan ook blijft draaien) vult een
  maandag-run nog steeds eventuele andere ontbrekende edities in het
  lookback-venster aan.
- Inloggegevens worden uitsluitend uit omgevingsvariabelen gelezen en worden
  nooit gelogd.
