"""Synchronisation des datasets Open Data vers la base locale.

Le dataset de production est au format « large » : une ligne par snapshot
quotidien et deux colonnes par domaine (`<domaine>_users` ou
`<domaine>_utilisateurs`, et `<domaine>_messages`). On le transforme ici en
une ligne par (date, domaine).
"""
import logging
import re
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from ..models import BetaLocation, ImportRun, ProductionObservation
from . import education_api, matching, quality

logger = logging.getLogger(__name__)

PARIS = ZoneInfo("Europe/Paris")
COLUMN_RE = re.compile(r"^(?P<domain>.+)_(?P<kind>users|utilisateurs|messages)$")
META_COLUMNS = {"timestamp", "cree_le"}


# --- Bêta -----------------------------------------------------------------

def sync_beta(records=None, force=False, session=None):
    """Importe les académies de la bêta. Ignoré si déjà présentes (dataset figé)."""
    if BetaLocation.objects.exists() and not force:
        return 0
    if records is None:
        records = education_api.fetch_all_records(education_api.BETA_DATASET, session=session)
    count = 0
    with transaction.atomic():
        for r in records:
            email = r.get("email_academie")
            if not email:
                logger.warning("Ligne bêta sans email_academie ignorée : %s", r.get("libelle_aca"))
                continue
            BetaLocation.objects.update_or_create(
                domain_key=matching.domain_key(email),
                defaults={
                    "name": r.get("libelle_aca") or email,
                    "normalized_name": matching.normalize_name(r.get("libelle_aca")),
                    "email_domain": email,
                    "region_email": (r.get("email_region_academique") or "").lstrip("@"),
                    "latitude": r.get("latitude"),
                    "longitude": r.get("longitude"),
                    "beta_chefs_etablissement": r.get("chefs_d_etablissement") or 0,
                    "beta_enseignants": r.get("enseignants") or 0,
                    "beta_administration": r.get("administration") or 0,
                    "beta_inspecteurs": r.get("inspecteurs") or 0,
                    "beta_total": r.get("total") or 0,
                },
            )
            count += 1
    return count


def simplify_geometry(geometry, precision=2):
    """Arrondit les coordonnées et supprime les points consécutifs identiques."""
    def ring(points):
        out = []
        for lon, lat, *_ in points:
            p = [round(lon, precision), round(lat, precision)]
            if not out or out[-1] != p:
                out.append(p)
        return out if len(out) >= 4 else [[round(x, precision) for x in pt[:2]] for pt in points]

    if geometry["type"] == "Polygon":
        coords = [ring(r) for r in geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        coords = [[ring(r) for r in poly] for poly in geometry["coordinates"]]
    else:
        return geometry
    return {"type": geometry["type"], "coordinates": coords}


def sync_contours(records=None, force=False, session=None):
    """Associe les contours officiels des académies (2020) aux académies de la bêta.

    Rapprochement par nom normalisé ; un contour ou une académie sans
    correspondance est signalé, jamais attribué par approximation.
    """
    locations = list(BetaLocation.objects.all())
    if not locations or (not force and all(loc.geo_shape for loc in locations
                                           if loc.normalized_name not in matching.VICE_RECTORATS)):
        return []
    if records is None:
        records = education_api.fetch_all_records(education_api.CONTOURS_DATASET, session=session)
    by_name = defaultdict(list)
    for loc in locations:
        by_name[loc.normalized_name].append(loc)
    anomalies, matched = [], set()
    with transaction.atomic():
        for r in records:
            name = r.get("name") or r.get("libelle_aca_majuscules")
            shape = r.get("geo_shape") or {}
            geometry = shape.get("geometry") if shape.get("type") == "Feature" else shape
            candidates = by_name.get(matching.normalize_name(name), [])
            if len(candidates) != 1 or not geometry:
                anomalies.append(quality.anomaly("contour_non_apparie", f"contour « {name} » sans académie unique"))
                continue
            loc = candidates[0]
            loc.geo_shape = simplify_geometry(geometry)
            loc.save(update_fields=["geo_shape"])
            matched.add(loc.pk)
    for loc in locations:
        if loc.pk not in matched and not loc.geo_shape and loc.normalized_name not in matching.VICE_RECTORATS:
            anomalies.append(quality.anomaly("contour_manquant", f"pas de contour pour {loc.name} (affichée en pastille)"))
    return anomalies


# --- Production -----------------------------------------------------------

def snapshot_date(record):
    """Date Europe/Paris du snapshot (`timestamp`, à défaut `cree_le`)."""
    raw = record.get("timestamp") or record.get("cree_le")
    if not raw:
        return None, None
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    return ts.astimezone(PARIS).date(), ts


MAX_COUNT = 2**31 - 1  # borne de PositiveIntegerField


def _count(value):
    """Cumul valide (entier entre 0 et MAX_COUNT), sinon None."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_COUNT:
        return value
    return None


def parse_production_records(records):
    """Transforme les lignes larges en {(date, domaine): {...}} + anomalies."""
    values, anomalies = {}, []
    today = timezone.localdate()
    for record in sorted(records, key=lambda r: r.get("timestamp") or r.get("cree_le") or ""):
        day, ts = snapshot_date(record)
        if day is None:
            anomalies.append(quality.anomaly("date_manquante", "snapshot sans timestamp ni cree_le, ignoré"))
            continue
        if day > today:
            # Un snapshot daté dans le futur ferait croire que la base est à jour : il est écarté.
            anomalies.append(quality.anomaly("date_invalide", "snapshot daté dans le futur, ignoré", date=day))
            continue
        per_domain = defaultdict(dict)
        for column, value in record.items():
            match = COLUMN_RE.match(column)
            if not match:
                if column not in META_COLUMNS:
                    anomalies.append(quality.anomaly("colonne_inconnue", f"colonne ignorée : {column}", date=day))
                continue
            kind = "messages" if match["kind"] == "messages" else "users"
            per_domain[match["domain"]][kind] = value
        for domain, pair in per_domain.items():
            if pair.get("users") is None or pair.get("messages") is None:
                anomalies.append(quality.anomaly("valeur_manquante", f"valeurs incomplètes : {pair}", domain, day))
                continue
            users, messages = _count(pair["users"]), _count(pair["messages"])
            if users is None or messages is None:
                anomalies.append(quality.anomaly("valeur_invalide", f"valeurs invalides : {pair}", domain, day))
                continue
            if (day, domain) in values:
                anomalies.append(quality.anomaly(
                    "doublon", "plusieurs snapshots le même jour : le plus récent est conservé", domain, day))
            values[(day, domain)] = {"users": users, "messages": messages, "ts": ts}
    return values, anomalies


def _label_for(domain, labels):
    """Libellé lisible : nom court curé pour les domaines nationaux, sinon métadonnées."""
    if domain in matching.NON_TERRITORIAL:
        return matching.NON_TERRITORIAL[domain]
    for suffix in ("users", "utilisateurs"):
        label = labels.get(f"{domain}_{suffix}")
        if label:
            label = re.sub(r"^Nombre d'utilisateurs (de l'|de la |du |de |d')?", "", label)
            label = re.sub(r"\s+utilisateurs$", "", label)
            return label[:1].upper() + label[1:]
    return matching.NON_TERRITORIAL.get(domain, domain)


def sync_production(records, labels, run):
    """Upsert idempotent des observations. Ne supprime jamais rien."""
    known_domains = set(ProductionObservation.objects.values_list("domain", flat=True).distinct())

    values, anomalies = parse_production_records(records)
    if not values:  # validation structurelle : rien n'est écrit si la source est vide ou illisible
        raise ValueError("aucune observation exploitable dans le dataset de production")

    locations = list(BetaLocation.objects.all())
    by_key = {loc.domain_key: loc for loc in locations}
    by_name = defaultdict(list)
    for loc in locations:
        by_name[loc.normalized_name].append(loc)

    domains = sorted({d for _, d in values})
    classification = {}
    for domain in domains:
        label = _label_for(domain, labels)
        raw_label = labels.get(f"{domain}_users") or labels.get(f"{domain}_utilisateurs", "")
        category, location, reason = matching.classify_domain(domain, by_key, by_name, raw_label)
        classification[domain] = (category, location, label)
        if category == "non_apparie":
            anomalies.append(quality.anomaly("domaine_non_apparie", reason, domain))

    created = updated = 0
    with transaction.atomic():
        for (day, domain), v in sorted(values.items()):
            category, location, label = classification[domain]
            _, was_created = ProductionObservation.objects.update_or_create(
                date=day, domain=domain,
                defaults={
                    "normalized_domain": matching.domain_key(domain),
                    "label": label,
                    "category": category,
                    "cumulative_users": v["users"],
                    "cumulative_messages": v["messages"],
                    "location": location,
                    "source_timestamp": v["ts"],
                },
            )
            created += was_created
            updated += not was_created

    # Contrôles sur l'ensemble de la série stockée (ancienne + nouvelle).
    by_date, series = defaultdict(dict), defaultdict(list)
    for o in ProductionObservation.objects.order_by("date").values_list(
            "date", "domain", "cumulative_users", "cumulative_messages"):
        by_date[o[0]][o[1]] = True
        series[o[1]].append((o[0], o[2], o[3]))
    anomalies += quality.check_calendar_gaps(by_date.keys())
    anomalies += quality.check_missing_domains(by_date)
    anomalies += quality.check_decreases(series)
    anomalies += quality.check_new_domains(domains, known_domains)

    run.number_of_records_fetched = len(records)
    run.number_of_records_created = created
    run.number_of_records_updated = updated
    run.anomalies = anomalies
    return run


def run_sync(force_beta=False, session=None):
    """Point d'entrée : bêta, production, contrôles, journal ImportRun."""
    run = ImportRun.objects.create()
    try:
        sync_beta(force=force_beta, session=session)
        contour_anomalies = sync_contours(force=force_beta, session=session)
        ds = education_api.PRODUCTION_DATASET
        records = education_api.fetch_all_records(ds, session=session)
        labels = education_api.fetch_field_labels(ds, session=session)
        sync_production(records, labels, run)
        run.anomalies = contour_anomalies + run.anomalies
        mark_new_anomalies(run)
        has_new = any(a["new"] for a in run.anomalies)
        run.status = ImportRun.STATUS_WARNING if has_new else ImportRun.STATUS_SUCCESS
    except Exception as exc:  # on journalise toute erreur dans ImportRun
        logger.exception("Échec de la synchronisation")
        run.status = ImportRun.STATUS_ERROR
        run.error_message = str(exc)
    run.finished_at = timezone.now()
    run.save()
    return run


def run_scheduled_sync(today=None):
    """Vérification planifiée : n'importe que si la source a publié un nouveau snapshot.

    - snapshot du jour déjà en base : rien à faire, aucune requête (retourne None) ;
    - source joignable mais sans nouveau snapshot : état normal, journalisé « unchanged » ;
    - source injoignable ou illisible : journalisé « error », les données restent en place ;
    - nouveau snapshot : synchronisation complète (`run_sync`).
    """
    today = today or timezone.localdate()
    latest = ProductionObservation.latest_date()
    if latest and latest >= today:
        return None
    try:
        raw = education_api.fetch_latest_timestamp(education_api.PRODUCTION_DATASET)
        published, _ = snapshot_date({"timestamp": raw})
    except Exception as exc:
        logger.exception("Échec de la vérification de publication")
        return ImportRun.objects.create(
            status=ImportRun.STATUS_ERROR, error_message=str(exc), finished_at=timezone.now())
    if latest and published and published <= latest:
        return ImportRun.objects.create(status=ImportRun.STATUS_UNCHANGED, finished_at=timezone.now())
    return run_sync()


def mark_new_anomalies(run):
    """Marque `new=True` les anomalies absentes du dernier import réussi.

    Les points déjà connus (ex. trous de publication historiques) restent
    listés mais ne relancent pas l'alerte à chaque synchronisation.
    """
    previous = (ImportRun.objects.exclude(pk=run.pk)
                .filter(status__in=ImportRun.COMPLETED).first())
    key = lambda a: (a["type"], a.get("domain", ""), a.get("date", ""), a.get("detail", ""))
    known = {key(a) for a in previous.anomalies} if previous else set()
    for a in run.anomalies:
        a["new"] = key(a) not in known
