# Déploiement de l'Assistant IA : tableau de bord de pilotage

Prototype Django de suivi de l'Assistant IA au ministère de l'Éducation nationale, à partir de l'Open Data (data.education.gouv.fr).
Il mesure la **diffusion** (nouveaux comptes) et l'**activité** (messages envoyés par les utilisateurs), au niveau national et par académie, sur des fenêtres glissantes de 7 jours.
Les données sont importées une fois par jour dans une base locale ; **les pages ne lisent que cette base**, jamais l'API distante.

## Démarrage rapide

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py sync_assistant_data   # import complet, environ 10 s
.venv/bin/python manage.py runserver
```

Page : http://127.0.0.1:8000/

Paramètres d'URL :
- `?academie=<id>` : ouvre directement le détail d'une académie (`id` de `BetaLocation`) ;
- `?vue=intensite` : ouvre la carte sur la vue Intensité.

## Configuration

| Variable d'environnement | Rôle | Défaut |
|---|---|---|
| `API_KEY` | Clé data.education.gouv.fr, envoyée en `Authorization: Apikey …`. Optionnelle : les datasets sont publics. | vide |
| `SQLITE_PATH` | Chemin de la base SQLite (base de démonstration séparée, par exemple). | `db.sqlite3` |

Réglages propres au projet dans `pilote/settings.py` :
- `TIME_ZONE = "Europe/Paris"` et `LANGUAGE_CODE = "fr-fr"` ;
- `EDUCATION_API_BASE` : URL de l'API Explore v2.1 ;
- `GZipMiddleware` : compresse les contours des académies (environ 520 Ko, ramenés à environ 75 Ko) ;
- `LOGGING` : logger `assistant_dashboard` vers la console.

Dépendances : Django et requests (`requirements.txt`). Côté navigateur, Leaflet 1.9 et Chart.js 4 sont chargés depuis cdnjs.

## Commandes

### `sync_assistant_data [--force-beta]`

Point d'entrée : `services.sync.run_sync`. Étapes, dans l'ordre :

1. **Bêta.** Import des 32 académies (`BetaLocation`). Le dataset est figé : l'étape est ignorée si la table est déjà remplie, sauf avec `--force-beta`.
2. **Contours.** Rattachement des contours officiels (2020) aux académies par nom normalisé. Les coordonnées sont arrondies et simplifiées.
3. **Production.** Récupération paginée, passage du format large au format long, puis `update_or_create` sur `(date, domain)`.
4. **Contrôles qualité.** Exécutés sur toute la série stockée.
5. **Journal.** Résultat enregistré dans un `ImportRun`.

L'import est idempotent et ne supprime jamais rien. La commande sort en erreur uniquement en cas d'échec technique (API indisponible…) ; les anomalies de qualité sont des avertissements.

### `load_demo_data`

Génère une série synthétique : 5 académies et 1 opérateur, sur 100 jours. Elle refuse de s'exécuter si la base contient déjà des observations ; utilisez une base dédiée :

```bash
SQLITE_PATH=demo.sqlite3 .venv/bin/python manage.py migrate
SQLITE_PATH=demo.sqlite3 .venv/bin/python manage.py load_demo_data
SQLITE_PATH=demo.sqlite3 .venv/bin/python manage.py runserver
```

Les académies de démonstration n'ont pas de contour : elles s'affichent en pastilles sous la carte.

### Exécution quotidienne (12:00, Europe/Paris)

Le fichier est publié vers 03:00 ; à midi, la veille est disponible. Avec cron, sans dépendance supplémentaire (`mkdir -p logs`, puis `crontab -e`) :

```cron
CRON_TZ=Europe/Paris
0 12 * * * cd /chemin/vers/dne && .venv/bin/python manage.py sync_assistant_data >> logs/sync.log 2>&1
```

Le cron de macOS ignore `CRON_TZ`. Sur une machine à l'heure de Paris, `0 12 * * *` suffit ; sinon, utilisez `launchd` avec `StartCalendarInterval`.

### Tests

```bash
.venv/bin/python manage.py test assistant_dashboard
```

| Fichier | Couvre |
|---|---|
| `tests/test_api.py` | pagination, timeouts, nouvelle tentative sur 5xx, en-tête `API_KEY` |
| `tests/test_sync.py` | création, idempotence, API indisponible, suffixe `_utilisateurs`, doublons, anomalies, contours, anomalies nouvelles et connues |
| `tests/test_metrics.py` | différences de cumuls, fenêtres de 7 jours, division par zéro, jours manquants, baisse de cumul, agrégation, signaux, seuil « stable » |
| `tests/test_matching.py` | normalisation, variantes orthographiques, domaines inconnus, nationaux et régionaux, noms d'affichage |
| `tests/test_views.py` | page (base vide ou remplie), KPI, fenêtres secondaires, endpoints JSON, invariants de cohérence (`ConsistencyTests`) |

`tests/factories.py` fournit des enregistrements au format réel de l'API.

## Arborescence

```
pilote/                         projet Django : settings, urls racine
assistant_dashboard/            l'application
  models.py                     BetaLocation, ProductionObservation, ImportRun
  admin.py                      consultation des données et des ImportRun (/admin/)
  services/
    education_api.py            HTTP uniquement : fetch_all_records, fetch_field_labels
    sync.py                     écriture en base : run_sync, sync_beta, sync_contours, sync_production
    matching.py                 normalize_name, domain_key, classify_domain, display_name, full_name
    quality.py                  contrôles qualité (fonctions pures)
    metrics.py                  calculs (fonctions pures) : compute_kpis, rolling_series, detect_signals, trend
    dashboard_data.py           lecture de la base, appel à metrics, mise en forme pour les vues
  management/commands/          sync_assistant_data, load_demo_data
  views.py, urls.py             page et endpoints JSON
  templatetags/dashboard_format.py   formats français : fr_int, fr_pct, trend, trend_word…
  templates/assistant_dashboard/     dashboard.html, _offmap_table.html
  static/assistant_dashboard/        dashboard.css, dashboard.js
  tests/
  migrations/
```

**Règles de dépendance**
- `metrics` et `quality` ne touchent ni la base ni le réseau.
- Seul `education_api` fait du HTTP ; seul `sync` écrit en base.
- Les vues passent par `dashboard_data`.
- Le JS affiche ce que calcule Python : seuils et signaux viennent du serveur.

## Flux de données

```
data.education.gouv.fr ─▶ education_api ─▶ sync ──(matching, quality)──▶ SQLite
                                                                           │
navigateur ◀── dashboard.js ◀── endpoints JSON / template ◀── dashboard_data + metrics
```

## Modèle de données

- **`BetaLocation`**. Une académie : nom, `domain_key` (ex. `ac_nancy_metz_fr`), latitude et longitude, effectifs de la bêta, `geo_shape` (GeoJSON). Les effectifs bêta ne sont jamais présentés comme des comptes de production.
- **`ProductionObservation`**. Cumuls (`cumulative_users`, `cumulative_messages`) pour une date et un domaine de messagerie. Contrainte d'unicité sur `(date, domain)`. `category` vaut `academie`, `regional`, `national` ou `non_apparie` ; `location` est renseigné pour les académies seulement.
- **`ImportRun`**. Une synchronisation : statut (`success`, `warning`, `error`), compteurs, message d'erreur et `anomalies`. Chaque anomalie porte un indicateur `new`, vrai si elle était absente de l'import précédent.

## Sources

| Dataset | Contenu | Usage |
|---|---|---|
| `assistant-ia-dinum-deploiement-au-ministere-de-leducation-nationale-en-academie` | 32 académies : `libelle_aca`, `email_academie`, coordonnées, inscrits de la bêta | référentiel des académies (figé) |
| `fr-en-contour-academies-2020` | 30 contours d'académies (sans Nouvelle-Calédonie ni Polynésie) | aplats de la carte |
| `fr-en-assistant_ia_deploiement_menjs` | un snapshot par jour, cumuls par domaine | séries de production |

**Format de la production : large.**
- Une ligne par jour, deux colonnes par domaine : `<domaine>_users` ou `<domaine>_utilisateurs`, et `<domaine>_messages`.
- La date retenue est celle du `timestamp`, convertie en heure de Paris. `cree_le` sert de repli, car il est vide sur les premières lignes.
- **Convention :** le snapshot du jour D contient les cumuls à la fin du jour D-1. Les séries sont donc indexées sur la date d'activité (D-1).

**Rapprochement territorial** (`services/matching.py`)
- **Clé :** `email_academie` normalisé, qui correspond exactement au préfixe des colonnes de production (`ac-nancy-metz.fr` donne `ac_nancy_metz_fr`).
- **Repli :** par nom normalisé (minuscules, sans accents, alias de variantes). Une correspondance multiple n'est jamais retenue.
- **Domaines non localisés,** listés explicitement : `NON_TERRITORIAL` (administration centrale, opérateurs, « autres ») et `AMBIGUOUS_REGIONAL` (régions académiques couvrant plusieurs académies). Ils restent dans les totaux nationaux, sans être géolocalisés.
- **Noms d'affichage :** `display_name` et `full_name` (« Académie d'Amiens », « Vice-rectorat de Nouvelle-Calédonie »).

## Endpoints internes

| URL | Contenu |
|---|---|
| `/` | page du dashboard |
| `/api/dashboard/summary/` | KPI nationaux, fraîcheur des données, anomalies |
| `/api/dashboard/timeseries/?scope=national\|<id>&period=30j\|12s\|all` | séries glissantes sur 7 jours (comptes et messages) |
| `/api/dashboard/locations/` | KPI et signaux par académie, domaines non localisés, seuils |
| `/api/dashboard/location/<id>/` | KPI, signaux et séries d'une académie |
| `/api/dashboard/shapes/` | contours GeoJSON (avec `Cache-Control` long) |

## Interface

Une seule page, `templates/assistant_dashboard/dashboard.html`, animée par `static/assistant_dashboard/dashboard.js`.

1. **Synthèse et KPI.**
   - Une phrase générée, par exemple « Diffusion stable · Activité en hausse ».
   - Deux KPI principaux : nouveaux comptes et messages sur 7 jours, avec leur variation.
   - Deux indicateurs secondaires : parc total de comptes et messages / 100 comptes.
2. **Carte des académies.**
   - Aplats sur fond blanc, sans fond de carte ; outre-mer en pastilles.
   - Vues : *Comptes | Messages | Intensité*, chacune en *Évolution* (vs 7 jours précédents) ou en *Volume* (le bouton s'appelle « Niveau » pour l'intensité, qui est un taux).
   - Interactions : clic sur une académie pour le détail, clic hors académie pour revenir au national, zoom au pavé tactile.
3. **Panneau de droite.**
   - Sans sélection : *Académies qui se démarquent*, c'est-à-dire celles dont l'évolution sur 7 jours est très différente de la moyenne nationale. Un bloc par mesure (nouveaux comptes, messages), avec la moyenne nationale (« Moyenne nat. ») une seule fois dans l'en-tête, plus un bloc « comptes et messages en sens inverse ». Exemple de lecture : « Bordeaux : +47 % de nouveaux comptes en 7 jours, contre −1 % en moyenne nationale ». Survoler une ligne surligne l'académie sur la carte ; un menu permet de choisir une académie.
   - Avec sélection : la raison pour laquelle l'académie se démarque, s'il y en a une, puis les KPI de l'académie, chacun avec sa variation sur 7 jours et la moyenne nationale. La tendance de l'académie s'affiche dans le graphique du bas, qui suit la sélection.
4. **Graphique sur 12 semaines.** Nouveaux comptes et messages, chacun indexé sur sa moyenne (100 = moyenne). La courbe est interrompue sur les fenêtres qui contiennent des jours non publiés.

Fenêtres secondaires (`<dialog>`) : *Méthodologie*, *Qualité des données*, *Comptes hors académies*.

**Où modifier quoi dans `dashboard.js`**

| Constante | Rôle |
|---|---|
| `FAMILY` | définition des vues de la carte |
| `SEQUENTIAL` | palette des volumes et de l'intensité ; bornes calculées par `classBreaks` (comptes, messages) ou fixées dans `FAMILY.intensity.breaks` (100 / 150 / 200) |
| `DIVERGING` | palette des évolutions (bornes ±5 % et ±25 %) |
| `SERIES` | couleurs du graphique |
| `ANNOTATIONS` | bandes annotées, par exemple les vacances d'été |

## Méthode

- **Flux.** Les données sont des cumuls. `nouveaux_comptes[t] = comptes[t] − comptes[t−1]`, et de même pour les messages. On ne calcule jamais messages ÷ nouveaux comptes : les messages d'un jour viennent aussi de comptes plus anciens.
- **Fenêtres.** Chaque chiffre couvre les 7 derniers jours complets disponibles, comparés aux 7 jours précédents. On affiche la variation relative et absolue ; elle vaut « n.d. » si la base est nulle ou l'historique insuffisant.
- **Jours manquants.** Le dernier cumul est reporté, et le rattrapage tombe le jour où la donnée réapparaît. Les totaux restent justes. Les fenêtres concernées sont signalées (`window_has_gap`) et ne sont pas tracées sur le graphique.
- **Baisse d'un cumul.** Elle est signalée comme anomalie et ramenée à 0 dans les sommes, sans faire planter le dashboard.
- **Messages / 100 comptes (intensité).** `100 × messages des 7 jours / parc moyen sur ces 7 jours` (`metrics.activity_per_100_accounts`, `window_mean`). Le dénominateur est le parc moyen et non le stock du dernier jour : un compte créé en fin de semaine n'a pas été disponible 7 jours. La semaine précédente est calculée de la même façon, ce qui donne la variation d'intensité (`activity_change_pct`). C'est un proxy d'intensité d'activité, pas un nombre d'utilisateurs actifs.
- **Stabilité.** En dessous de ±5 % (`metrics.STABLE_THRESHOLD`), une évolution est « stable » : flèches, synthèse et classe neutre de la carte. Le seuil est exposé au JS par le template.
- **Académies qui se démarquent** (`metrics.detect_signals`).
  - **Écart :** l'évolution sur 7 jours de l'académie s'écarte d'au moins `SIGNAL_MIN_GAP` (30 points) de l'évolution nationale. Les effets communs à tout le territoire (vacances, rentrée) s'annulent.
  - **Divergence :** comptes et messages évoluent en sens opposés d'au moins `DIVERGENCE_MIN` (15 %) chacun.
  - **Volume minimal :** `SIGNAL_MIN_BASE` (30 comptes ou 400 messages la semaine précédente).
  - **Cohérence :** les signaux utilisent la même référence que les KPI et la carte, donc ils ne peuvent pas les contredire.
- **Contrôles qualité** (`services/quality.py`, plus les contours dans `services/sync.py`). Ils détectent : date manquante, domaine absent, doublon date + domaine, baisse de cumul, nouveau domaine, domaine non apparié, contour manquant et absence de nouveau fichier. Le badge de l'en-tête ne compte que les anomalies nouvelles ; les points connus restent listés dans la fenêtre *Qualité des données*.

### Pourquoi un rolling 7 jours ?

Les chiffres quotidiens reflètent surtout le calendrier : creux le week-end, pics en début de semaine, jours fériés. Une fenêtre de 7 jours contient toujours exactement un week-end, si bien que deux fenêtres consécutives sont comparables et que la tendance de fond apparaît. Les vacances scolaires font baisser naturellement l'activité : une baisse n'est pas en soi une contre-performance.

### Pourquoi l'activité ne correspond pas à l'adoption ?

Les données sont agrégées par domaine : on connaît le total des comptes créés et des messages envoyés, pas qui les envoie. 1 000 messages peuvent venir de 500 personnes ou de 10. L'activité est un signal, pas une mesure de l'usage individuel ni de la valeur créée.

## Limites des données

- Ni utilisateurs actifs, ni rétention, ni fréquence d'usage par personne, ni impact sur les pratiques : seuls des cumuls agrégés par domaine sont publiés.
- Le nombre d'agents éligibles par académie n'est pas connu. Les volumes reflètent d'abord la taille des académies, et le parc n'est rapporté à aucune population cible.
- La répartition territoriale suit le domaine de messagerie, pas le lieu d'exercice. Les comptes des domaines nationaux et régionaux ne sont pas localisés.
- Des jours ne sont pas publiés ; ils sont rattrapés au fichier suivant.

## Invariants à préserver

- Les pages et les endpoints ne lisent que la base locale, jamais l'API.
- Tous les pourcentages comparent les 7 derniers jours aux 7 précédents.
- Un seul seuil « stable », défini en Python (`STABLE_THRESHOLD`) et transmis au JS.
- Académies + domaines hors académies = total national, pour les comptes, les messages et le parc.
- Aucun domaine ambigu ou national n'est géolocalisé.
- Aucun libellé « utilisateurs actifs », « adoption » ou « engagement », ni de conclusion plus forte que ce que permettent les données.
- `tests/test_views.py::ConsistencyTests` reste vert.
