# Déploiement de l'Assistant IA : tableau de bord de pilotage

Prototype Django de suivi de l'Assistant IA au ministère de l'Éducation nationale, à partir de l'Open Data (data.education.gouv.fr).
Il mesure la **diffusion** (nouveaux comptes) et l'**activité** (messages envoyés par les utilisateurs), au niveau national et par académie, sur des fenêtres glissantes de 7 jours.
Les données sont importées automatiquement chaque jour dans une base locale ; **les pages ne lisent que cette base**, jamais l'API distante.

## Choix de conception

- **Un seul indicateur piloté : la diffusion** (nouveaux comptes sur 7 jours). Activité, intensité et parc total servent de contexte.
- **Une seule référence de temps :** les 7 derniers jours comparés aux 7 précédents, partout.
- **Une académie est comparée à la tendance nationale,** pas à zéro, et seulement au-delà d'un volume minimal : cela limite les faux signaux.
- **En cas de doute, un domaine n'est pas rattaché à une académie.** Il reste compté dans le total national.

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
| `DJANGO_SECRET_KEY` | Clé secrète Django. **Obligatoire en production** (le démarrage échoue sans elle sur Railway) ; une clé de développement sert en local. | clé de développement |
| `DJANGO_DEBUG` | `1` pour activer le mode debug, `0` pour le désactiver. | `1` en local, `0` sur Railway |
| `RAILWAY_VOLUME_MOUNT_PATH` | Fourni par Railway : dossier du volume où placer la base si `SQLITE_PATH` est vide. | dossier du projet |

Dépendances : Django et requests, plus gunicorn et whitenoise en production (`requirements.txt`). Leaflet 1.9 et Chart.js 4 sont chargés depuis cdnjs avec contrôle d'intégrité (`integrity`, à recalculer si la version change).

## Commandes

### `sync_assistant_data [--force-beta] [--if-new]`

Point d'entrée : `services.sync.run_sync`. Dans l'ordre :

1. **Bêta.** Import des 32 académies (`BetaLocation`). Dataset figé : étape ignorée si la table est remplie, sauf avec `--force-beta`.
2. **Contours.** Contours officiels (2020) rattachés par nom normalisé, coordonnées simplifiées.
3. **Production.** Récupération paginée, passage du format large au format long, `update_or_create` sur `(date, domain)`.
4. **Contrôles qualité** sur toute la série stockée, puis journal dans un `ImportRun`.

L'import est idempotent et ne supprime rien. La commande ne sort en erreur que sur un échec technique (API indisponible…) ; les anomalies de qualité sont des avertissements.

### `load_demo_data`

Série synthétique (5 académies, 1 opérateur, 100 jours), sans contours : les académies s'affichent en pastilles. La commande refuse une base qui contient déjà des observations ; utilisez une base dédiée :

```bash
SQLITE_PATH=demo.sqlite3 .venv/bin/python manage.py migrate
SQLITE_PATH=demo.sqlite3 .venv/bin/python manage.py load_demo_data
SQLITE_PATH=demo.sqlite3 .venv/bin/python manage.py runserver
```

### Automated data pipeline

Production data is checked automatically every day from 04:00 Europe/Paris. If the ministry dataset has not yet been updated, the application retries progressively at 04:30, 05:30, 07:30 and 11:30. As soon as a new publication is detected and validated, the indicators are recomputed and no further requests are made that day. The dashboard always keeps the last valid dataset available.

- **Règle** (`services.sync.run_scheduled_sync`) : à chaque créneau, si le snapshot du jour n'est pas en base, une requête d'une seule ligne demande à la source sa date la plus récente ; l'import complet (`run_sync`) n'a lieu que si elle est plus récente. Une fois le snapshot du jour en base, les créneaux suivants ne font aucune requête. L'API n'expose ni `ETag` ni `Last-Modified` : cette requête légère en tient lieu.
- **Trois issues, journalisées dans `ImportRun` :** nouvelle publication importée (`success` ou `warning`), pas de nouvelle publication (`unchanged`, état normal, sans alerte), échec technique (`error`, les données en place sont conservées). Une source vide ou illisible n'écrit rien ; une valeur invalide ou un snapshot daté dans le futur est écarté et signalé, sans bloquer le reste.
- **Seule la production est vérifiée chaque jour :** la bêta et les contours, figés, ne sont importés qu'une fois.
- **Créneaux :** `services/schedule.py` (`SLOTS`). Les lignes cron ci-dessous doivent rester alignées dessus.
- **Planificateur.** Sur Railway : `manage.py run_sync_scheduler`, lancé par `railway.json` dans le même conteneur que gunicorn (la base SQLite est sur un volume, qu'un service cron séparé ne peut pas partager) et relancé s'il s'arrête. S'il ne tournait plus du tout, l'en-tête l'indiquerait (« pas de fichier aujourd'hui », puis « dernier fichier il y a N j »). Ailleurs, cron suffit (`mkdir -p logs`, puis `crontab -e`) :

```cron
CRON_TZ=Europe/Paris
0 4 * * *            cd /chemin/vers/dne && .venv/bin/python manage.py sync_assistant_data --if-new >> logs/sync.log 2>&1
30 4,5,7,11 * * *    cd /chemin/vers/dne && .venv/bin/python manage.py sync_assistant_data --if-new >> logs/sync.log 2>&1
```

- **Déclenchement manuel :** `manage.py sync_assistant_data` (import complet) ou `--if-new` (même règle que le planificateur).
- **État et journaux :** table `ImportRun` (`/admin/`), fenêtre *Qualité des données* (« dernière synchronisation le 03/10 à 04:03 » ou « prochaine vérification prévue à 05:30 »), `/api/dashboard/summary/` (`sync_state`, `next_check`, `last_sync`, `last_attempt`), et la console pour les logs. Le message d'erreur détaillé n'est pas publié : il reste dans `ImportRun` et dans les logs.

### Déploiement (Railway)

`railway.json` lance `migrate`, `collectstatic`, une synchronisation complète, le planificateur, puis gunicorn ; whitenoise sert les fichiers statiques.

- Définir `DJANGO_SECRET_KEY` et attacher un volume pour conserver la base SQLite.
- Laisser l'option « app sleeping » désactivée : un conteneur endormi ne lance pas les vérifications. La synchronisation de démarrage rattrape un créneau manqué.

### Tests

```bash
.venv/bin/python manage.py test assistant_dashboard
```

Un fichier de tests par service (`test_api`, `test_sync`, `test_metrics`, `test_matching`) et `test_views` pour la page, les endpoints et les invariants de cohérence (`ConsistencyTests`). `tests/factories.py` fournit des enregistrements au format réel de l'API.

## Arborescence

```
pilote/                         projet Django : settings, urls racine
assistant_dashboard/            l'application
  models.py                     BetaLocation, ProductionObservation, ImportRun
  admin.py                      consultation des données et des ImportRun (/admin/)
  services/
    education_api.py            HTTP uniquement : fetch_all_records, fetch_latest_timestamp, fetch_field_labels
    sync.py                     écriture en base : run_sync, run_scheduled_sync, sync_beta, sync_contours, sync_production
    schedule.py                 créneaux de vérification quotidienne (fonctions pures)
    matching.py                 normalize_name, domain_key, classify_domain, display_name, full_name
    quality.py                  contrôles qualité (fonctions pures)
    metrics.py                  calculs (fonctions pures) : compute_kpis, rolling_series, detect_signals, trend
    dashboard_data.py           lecture de la base, appel à metrics, mise en forme pour les vues
  management/commands/          sync_assistant_data, run_sync_scheduler, load_demo_data
  views.py, urls.py             page et endpoints JSON
  templatetags/dashboard_format.py   formats français : fr_int, fr_pct, trend, trend_word…
  templates/assistant_dashboard/     dashboard.html, _offmap_table.html, _reco_academies.html, _reco_chip.html
  static/assistant_dashboard/        dashboard.css, dashboard.js
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

- **`BetaLocation`** : une académie (nom, `domain_key` tel que `ac_nancy_metz_fr`, coordonnées, `geo_shape`, effectifs de la bêta, jamais présentés comme des comptes de production).
- **`ProductionObservation`** : cumuls de comptes et de messages pour une date et un domaine de messagerie, uniques sur `(date, domain)`. `category` vaut `academie`, `regional`, `national` ou `non_apparie` ; `location` n'est renseigné que pour les académies.
- **`ImportRun`** : une synchronisation ou une vérification (statut `success`, `warning`, `error` ou `unchanged`, compteurs, erreur, `anomalies`). Chaque anomalie porte `new`, vrai si elle était absente de l'import précédent.

## Sources

| Dataset | Contenu | Usage |
|---|---|---|
| `assistant-ia-dinum-deploiement-au-ministere-de-leducation-nationale-en-academie` | 32 académies : `libelle_aca`, `email_academie`, coordonnées, inscrits de la bêta | référentiel des académies (figé) |
| `fr-en-contour-academies-2020` | 30 contours d'académies (sans Nouvelle-Calédonie ni Polynésie) | aplats de la carte |
| `fr-en-assistant_ia_deploiement_menjs` | un snapshot par jour, cumuls par domaine | séries de production |

**Format de la production : large.** Une ligne par jour, deux colonnes par domaine (`<domaine>_users` ou `<domaine>_utilisateurs`, et `<domaine>_messages`). La date est celle du `timestamp` en heure de Paris, à défaut `cree_le`. Le snapshot du jour D contient les cumuls à la fin du jour D-1 : les séries sont indexées sur la date d'activité (D-1).

**Rapprochement territorial** (`services/matching.py`)
- **Clé :** `email_academie` normalisé, identique au préfixe des colonnes de production (`ac-nancy-metz.fr` donne `ac_nancy_metz_fr`).
- **Repli :** nom normalisé ; une correspondance multiple n'est jamais retenue.
- **Jamais localisés :** `NON_TERRITORIAL` (administration centrale, opérateurs, « autres ») et `AMBIGUOUS_REGIONAL` (régions académiques couvrant plusieurs académies).

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

Une seule page, `templates/assistant_dashboard/dashboard.html`, animée par `static/assistant_dashboard/dashboard.js`. Elle se lit en trois temps, puis l'historique :

- **1 Détecter · Indicateurs clés.** Diffusion (KPI principal), puis Activité, Intensité et Parc total, avec leur variation sur 7 jours.
- **2 Expliquer · Diagnostic territorial.** Carte des académies (Comptes, Messages ou Intensité, en évolution ou en volume ; outre-mer en pastilles) et panneau *Académies qui se démarquent*, alimenté par `metrics.detect_signals`. Un clic sur une académie affiche ses KPI face à la moyenne nationale.
- **3 Agir · Actions recommandées.** 01 Capitaliser, 02 Corriger, 03 Activer, avec les académies concernées. `views.reco_status` relit les signaux existants ; seuls les signaux forts déclenchent une action.
- **Évolution historique.** Nouveaux comptes et messages sur 12 semaines, indexés sur leur moyenne (100 = moyenne) ; le graphique suit l'académie sélectionnée.
- **Fenêtres secondaires** (`<dialog>`) : *Méthodologie*, *Qualité des données*, *Comptes hors académies*. Les nuances de méthode y sont, ainsi que dans les infobulles (i).

**Où modifier quoi dans `dashboard.js`**

| Constante | Rôle |
|---|---|
| `FAMILY` | définition des vues de la carte |
| `SEQUENTIAL` | palette des volumes et de l'intensité ; bornes calculées par `classBreaks` (comptes, messages) ou fixées dans `FAMILY.intensity.breaks` (100 / 150 / 200) |
| `DIVERGING` | palette des évolutions (bornes ±5 % et ±25 %) |
| `SERIES` | couleurs du graphique |
| `SIGNAL_BLOCKS` | blocs du panneau *Académies qui se démarquent* |
| `ANNOTATIONS` | bandes annotées : vacances scolaires, jours fériés (à compléter chaque année) |

## Méthode

- **Flux.** Les données sont des cumuls. `nouveaux_comptes[t] = comptes[t] − comptes[t−1]`, et de même pour les messages. On ne calcule jamais messages ÷ nouveaux comptes : les messages d'un jour viennent aussi de comptes plus anciens.
- **Fenêtres.** Chaque chiffre couvre les 7 derniers jours disponibles, comparés aux 7 précédents. La variation vaut « n.d. » si la base est nulle ou l'historique insuffisant.
- **Jours manquants.** Le dernier cumul est reporté, et le rattrapage tombe le jour où la donnée réapparaît : les totaux restent justes. Les fenêtres concernées sont signalées (`window_has_gap`) et ne sont pas tracées. Un domaine absent des derniers fichiers garde son dernier cumul dans le parc total.
- **Baisse d'un cumul.** Signalée comme anomalie et ramenée à 0 dans les sommes, domaine par domaine (`aggregate_series`, `window_total`).
- **Intensité.** `100 × messages des 7 jours / parc moyen sur ces 7 jours` (`metrics.activity_per_100_accounts`). Parc moyen, et non stock du dernier jour : un compte créé en fin de semaine n'a pas été disponible 7 jours. C'est un proxy, pas un nombre d'utilisateurs actifs.
- **Stabilité.** En dessous de ±5 % (`metrics.STABLE_THRESHOLD`), une évolution est « stable ».
- **Académies qui se démarquent** (`metrics.detect_signals`).
  - **Écart de diffusion :** l'évolution des nouveaux comptes de l'académie s'écarte de l'évolution nationale d'au moins `SIGNAL_WATCH_GAP` (« à surveiller ») ou `SIGNAL_MIN_GAP` (« à investiguer »). Ce qui touche tout le territoire en même temps s'annule.
  - **Activation (l'usage ne suit pas) :** nouveaux comptes en hausse et intensité stable (à surveiller) ou en baisse (à investiguer). L'activité (messages) n'est pas un signal : elle sert de contexte.
  - **Volume minimal :** `SIGNAL_MIN_BASE`, sur la plus grande des deux semaines comparées (`metrics.has_min_volume`). En dessous, pas de signal, et le détail indique « volume insuffisant » (`dashboard_data.low_volume`).
  - Ces seuils sont des conventions opérationnelles, pas des tests statistiques.
- **Contrôles qualité** (`services/quality.py`, et les contours dans `services/sync.py`). Date manquante, domaine absent, doublon, baisse de cumul, nouveau domaine, domaine non apparié, contour manquant, valeur invalide, date dans le futur. Un fichier du jour pas encore publié n'est pas une anomalie : c'est l'état « prochaine vérification ». L'en-tête ne compte que les anomalies nouvelles ; les points connus restent listés dans *Qualité des données*.

### Pourquoi un rolling 7 jours ?

Les chiffres quotidiens reflètent surtout le calendrier (week-ends, jours fériés). Une fenêtre de 7 jours contient toujours un week-end : deux fenêtres consécutives sont comparables. Les vacances scolaires font baisser l'activité : une baisse n'est pas en soi une contre-performance.

## Limites des données

- Ni utilisateurs actifs, ni rétention, ni fréquence d'usage par personne, ni impact sur les pratiques : seuls des cumuls agrégés par domaine sont publiés. 1 000 messages peuvent venir de 500 personnes ou de 10 ; l'activité ne mesure donc pas l'adoption.
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
