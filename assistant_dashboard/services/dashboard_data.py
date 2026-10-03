"""Lecture de la base locale et préparation des données du dashboard.

Fait le lien entre les modèles et `metrics` (qui reste sans accès base).
"""
from collections import defaultdict
from datetime import timedelta

from ..models import BetaLocation, ImportRun, ProductionObservation
from . import metrics

PERIODS = {"30j": 30, "12s": 84, "all": None}
DEFAULT_PERIOD = "12s"


def _domain_series(queryset):
    """{domaine: série quotidienne}, indexée par date d'activité (snapshot - 1 j)."""
    points = defaultdict(list)
    for day, domain, users, messages in queryset.values_list(
            "date", "domain", "cumulative_users", "cumulative_messages"):
        points[domain].append((day - timedelta(days=1), users, messages))
    return {d: metrics.build_daily_series(p) for d, p in points.items()}


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


def freshness():
    last_run = ImportRun.objects.exclude(status=ImportRun.STATUS_RUNNING).first()
    last_ok = ImportRun.objects.filter(
        status__in=[ImportRun.STATUS_SUCCESS, ImportRun.STATUS_WARNING]).first()
    latest = ProductionObservation.objects.order_by("-date").values_list("date", flat=True).first()
    anomalies = last_run.anomalies if last_run else []
    # Les anomalies déjà connues lors de l'import précédent ne relancent pas l'alerte.
    new = [a for a in anomalies if a.get("new", True)]
    known = [a for a in anomalies if not a.get("new", True)]
    return {
        # Le snapshot du jour D couvre l'activité jusqu'à la fin du jour D-1.
        "data_date": latest - timedelta(days=1) if latest else None,
        "last_sync": last_ok.finished_at if last_ok else None,
        "last_run_status": last_run.status if last_run else None,
        "last_run_error": last_run.error_message if last_run else "",
        "anomaly_count": len(new),
        "anomaly_types": sorted({a["type"] for a in new}),
        "anomalies_new": group_anomalies(new),
        "anomalies_known": group_anomalies(known),
    }


ANOMALY_LABELS = {
    "date_manquante": "Jours sans données publiées",
    "domaine_absent": "Domaine absent d'un snapshot",
    "doublon": "Plusieurs snapshots le même jour",
    "baisse_utilisateurs": "Baisse d'un cumul de comptes",
    "baisse_messages": "Baisse d'un cumul de messages",
    "nouveau_domaine": "Nouveau domaine apparu",
    "domaine_non_apparie": "Domaine sans correspondance territoriale",
    "pas_de_nouvelles_donnees": "Pas de nouveau fichier aujourd'hui",
    "contour_non_apparie": "Contour d'académie sans correspondance",
    "contour_manquant": "Académie sans contour géographique",
    "valeur_manquante": "Valeur manquante",
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
        "details": [" : ".join(x for x in (i.get("domain"), i.get("detail")) if x) for i in items[:max_details]],
    } for kind, items in sorted(groups.items())]


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
    for loc in BetaLocation.objects.filter(pk__in=by_location.keys()):
        kpis = metrics.compute_kpis(metrics.aggregate_series(by_location[loc.pk]))
        loc_signals = metrics.detect_signals(kpis, national)
        locations.append({
            "id": loc.pk, "name": loc.display_name, "full_name": loc.full_name,
            "lat": loc.latitude, "lon": loc.longitude, "has_shape": bool(loc.geo_shape),
            "signals": loc_signals,
            **serialize_kpis(kpis),
        })
        if loc_signals:
            signals.append({"id": loc.pk, "name": loc.display_name, "full_name": loc.full_name,
                            "severity": loc_signals[0]["severity"], "signals": loc_signals})
    category_order = {"regional": 0, "national": 1, "non_apparie": 2}
    unlocated.sort(key=lambda u: (category_order.get(u["category"], 9), -(u["cumulative_users"] or 0)))
    signals.sort(key=lambda s: -s["severity"])
    return {
        "locations": locations,
        "unlocated": unlocated,
        "signals": signals,
        "national": {k: national[k] for k in ("new_users_change_pct", "messages_change_pct", "cumulative_users",
                                            "activity_per_100_accounts")},
        "signal_thresholds": {
            "min_base": metrics.SIGNAL_MIN_BASE, "min_gap": metrics.SIGNAL_MIN_GAP,
            "divergence": metrics.DIVERGENCE_MIN, "stable": metrics.STABLE_THRESHOLD,
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
    """Synthèse des domaines hors carte : total, part du parc, 3 principaux, deux groupes."""
    payload = payload or locations_payload()
    unlocated = payload["unlocated"]
    by_size = sorted(unlocated, key=lambda u: -(u["cumulative_users"] or 0))
    total = sum(u["cumulative_users"] or 0 for u in unlocated)
    national_total = payload["national"]["cumulative_users"] or 0
    return {
        "total_users": total,
        "share": total / national_total if national_total else None,
        "count": len(unlocated),
        "top3": [u for u in by_size if u["category"] == "national" and u["domain"] != "autres"][:3],
        "regional": [u for u in by_size if u["category"] == "regional"],
        "national": [u for u in by_size if u["category"] != "regional"],
    }
