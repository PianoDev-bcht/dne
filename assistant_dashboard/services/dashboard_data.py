"""Lecture de la base locale et préparation des données du dashboard.

Fait le lien entre les modèles et `metrics` (qui reste sans accès base).
"""
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from ..models import BetaLocation, ImportRun, ProductionObservation
from . import metrics, schedule

PERIODS = {"30j": 30, "12s": 84, "all": None}
DEFAULT_PERIOD = "12s"


def _domain_series(queryset):
    """{domaine: série quotidienne}, indexée par date d'activité (snapshot - 1 j).

    Toutes les séries vont jusqu'au dernier snapshot de la base : un domaine absent
    des derniers fichiers garde son dernier cumul dans les totaux.
    """
    latest = ProductionObservation.latest_date()
    end = latest - timedelta(days=1) if latest else None
    points = defaultdict(list)
    for day, domain, users, messages in queryset.values_list(
            "date", "domain", "cumulative_users", "cumulative_messages"):
        points[domain].append((day - timedelta(days=1), users, messages))
    return {d: metrics.build_daily_series(p, end=end) for d, p in points.items()}


def national_series():
    return metrics.aggregate_series(_domain_series(ProductionObservation.objects.all()).values())


def location_series(location):
    return metrics.aggregate_series(
        _domain_series(ProductionObservation.objects.filter(location=location)).values())


def chart_payload(series, period=DEFAULT_PERIOD):
    days = PERIODS.get(period, PERIODS[DEFAULT_PERIOD])
    return {
        "period": period if period in PERIODS else DEFAULT_PERIOD,
        "users": _serialize(metrics.rolling_series(series, "new_users", last_n_days=days)),
        "messages": _serialize(metrics.rolling_series(series, "daily_messages", last_n_days=days)),
    }


def _serialize(points):
    return [{"date": p["date"].isoformat(), "value": p["value"], "has_gap": p["has_gap"]} for p in points]


def serialize_kpis(kpis):
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in kpis.items()}


# États de synchronisation affichés (repris tels quels dans dashboard.html et /api/dashboard/summary/).
SYNC_RUNNING = "syncing"
SYNC_UP_TO_DATE = "up_to_date"
SYNC_ERROR = "error"
SYNC_WAITING = "waiting_for_publication"        # état normal tant que le fichier du jour n'est pas publié
SYNC_NO_PUBLICATION = "no_publication_today"    # dernier créneau passé sans fichier du jour


def freshness(now=None):
    """Fraîcheur des données, état de la synchronisation et anomalies du dernier import complet."""
    now = now or timezone.localtime()
    today = now.date()
    last_run = ImportRun.objects.exclude(status=ImportRun.STATUS_RUNNING).first()  # dernière tentative
    last_ok = ImportRun.objects.filter(status__in=ImportRun.COMPLETED).first()
    latest = ProductionObservation.latest_date()
    next_check = schedule.next_slot(now)
    # Un import interrompu reste « running » : au-delà de 10 minutes, il n'est plus considéré en cours.
    running = ImportRun.objects.filter(
        status=ImportRun.STATUS_RUNNING, started_at__gte=now - timedelta(minutes=10)).exists()
    if running:
        sync_state = SYNC_RUNNING
    elif latest and latest >= today:
        sync_state = SYNC_UP_TO_DATE  # prime sur un échec antérieur : les données du jour sont là
    elif last_run and last_run.status == ImportRun.STATUS_ERROR:
        sync_state = SYNC_ERROR
    elif next_check.date() > today:
        sync_state = SYNC_NO_PUBLICATION
    else:
        sync_state = SYNC_WAITING
    # Une vérification sans nouvelle publication (ou en échec) ne remplace pas les anomalies connues.
    anomalies = last_ok.anomalies if last_ok else []
    # Les anomalies déjà connues lors de l'import précédent ne relancent pas l'alerte.
    new = [a for a in anomalies if a.get("new", True)]
    known = [a for a in anomalies if not a.get("new", True)]
    return {
        # Le snapshot du jour D couvre l'activité jusqu'à la fin du jour D-1.
        "data_date": latest - timedelta(days=1) if latest else None,
        # Âge du dernier snapshot, évalué à l'affichage : une synchronisation qui ne
        # tourne plus ne doit pas laisser un indicateur vert sur des données anciennes.
        "stale_days": (today - latest).days if latest else None,
        "last_sync": last_ok.finished_at if last_ok else None,
        # Le message d'erreur détaillé reste dans ImportRun (admin, logs) : il n'est pas publié.
        "last_attempt": last_run.finished_at if last_run else None,
        "sync_state": sync_state,
        "next_check": next_check,
        "next_check_tomorrow": next_check.date() > today,
        "anomaly_count": len(new),
        "anomalies_new": group_anomalies(new),
        "anomalies_known": group_anomalies(known),
    }


ANOMALY_LABELS = {
    "date_manquante": "Périodes sans données publiées",
    "domaine_absent": "Domaine absent d'un snapshot",
    "doublon": "Plusieurs snapshots le même jour",
    "baisse_utilisateurs": "Baisse d'un cumul de comptes",
    "baisse_messages": "Baisse d'un cumul de messages",
    "nouveau_domaine": "Nouveau domaine apparu",
    "domaine_non_apparie": "Domaine sans correspondance territoriale",
    "contour_non_apparie": "Contour d'académie sans correspondance",
    "contour_manquant": "Académie sans contour géographique",
    "valeur_manquante": "Valeur manquante",
    "valeur_invalide": "Valeur invalide",
    "date_invalide": "Snapshot daté dans le futur",
    "colonne_inconnue": "Colonne non reconnue",
}


def group_anomalies(anomalies, max_details=5):
    """Regroupe les anomalies par type pour l'affichage (libellé, nombre, exemples)."""
    groups = defaultdict(list)
    for a in anomalies:
        groups[a["type"]].append(a)
    return [{
        "type": kind,
        "label": ANOMALY_LABELS.get(kind, kind),
        "count": len(items),
        "more": max(len(items) - max_details, 0),
        "details": [" : ".join(x for x in (i.get("domain"), i.get("detail")) if x) for i in items[:max_details]],
    } for kind, items in sorted(groups.items())]


def low_volume(kpis):
    """Volume trop faible sur les deux semaines comparées : pourcentage peu fiable, jamais signalé."""
    return {m: not metrics.has_min_volume(kpis, m) for m in ("new_users", "messages")}


def locations_payload():
    """Métriques et signaux par académie appariée + domaines non localisés."""
    series_by_domain = _domain_series(ProductionObservation.objects.all())
    domain_info = {
        d: (cat, loc_id, label) for d, cat, loc_id, label in
        ProductionObservation.objects.order_by("domain", "date")  # la plus récente gagne
        .values_list("domain", "category", "location_id", "label")
    }
    by_location = defaultdict(list)
    unlocated = []
    for domain, series in series_by_domain.items():
        category, loc_id, label = domain_info[domain]
        if loc_id:
            by_location[loc_id].append(series)
        else:
            kpis = metrics.compute_kpis(series)
            unlocated.append({"domain": domain, "label": label, "category": category,
                              **serialize_kpis(kpis)})

    national = metrics.compute_kpis(metrics.aggregate_series(series_by_domain.values()))
    locations, signals = [], []
    for loc in BetaLocation.objects.filter(pk__in=by_location.keys()).defer("geo_shape"):
        kpis = metrics.compute_kpis(metrics.aggregate_series(by_location[loc.pk]))
        loc_signals = metrics.detect_signals(kpis, national)
        locations.append({
            "id": loc.pk, "name": loc.display_name, "full_name": loc.full_name,
            "lat": loc.latitude, "lon": loc.longitude,
            "signals": loc_signals,
            "low_volume": low_volume(kpis),
            **serialize_kpis(kpis),
        })
        if loc_signals:
            signals.append({"id": loc.pk, "name": loc.display_name, "full_name": loc.full_name,
                            "strong": loc_signals[0]["level"] == "strong",
                            "severity": loc_signals[0]["severity"], "signals": loc_signals})
    category_order = {"regional": 0, "national": 1, "non_apparie": 2}
    unlocated.sort(key=lambda u: (category_order.get(u["category"], 9), -(u["cumulative_users"] or 0)))
    signals.sort(key=lambda s: (not s["strong"], -s["severity"]))
    return {
        "locations": locations,
        "unlocated": unlocated,
        "signals": signals,
        "national": {k: national[k] for k in ("new_users_change_pct", "messages_change_pct", "cumulative_users",
                                            "activity_per_100_accounts", "activity_change_pct")},
        "signal_thresholds": {
            "min_base": metrics.SIGNAL_MIN_BASE, "min_gap": metrics.SIGNAL_MIN_GAP,
            "watch_gap": metrics.SIGNAL_WATCH_GAP, "stable": metrics.STABLE_THRESHOLD,
        },
    }


def shapes_payload():
    """Contours des académies (GeoJSON), sans métriques : mis en cache côté navigateur."""
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": pk, "properties": {"id": pk}, "geometry": shape}
            for pk, shape in BetaLocation.objects.exclude(geo_shape=None).values_list("pk", "geo_shape")
        ],
    }


def unlocated_summary(payload=None):
    """Synthèse des domaines hors carte : total, part du parc, deux groupes."""
    payload = payload or locations_payload()
    unlocated = payload["unlocated"]
    by_size = sorted(unlocated, key=lambda u: -(u["cumulative_users"] or 0))
    total = sum(u["cumulative_users"] or 0 for u in unlocated)
    national_total = payload["national"]["cumulative_users"] or 0
    return {
        "total_users": total,
        "share": total / national_total if national_total else None,
        "count": len(unlocated),
        "regional": [u for u in by_size if u["category"] == "regional"],
        "national": [u for u in by_size if u["category"] != "regional"],
    }
