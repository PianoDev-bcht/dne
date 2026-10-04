"""Lecture de la base locale et préparation des données du dashboard.

Fait le lien entre les modèles et `metrics` (qui reste sans accès base).
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

from ..models import BetaLocation, ImportRun, ProductionObservation, SchoolHoliday
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
        "users": _serialize(metrics.daily_points(series, "new_users", last_n_days=days)),
        "messages": _serialize(metrics.daily_points(series, "daily_messages", last_n_days=days)),
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
    "calendrier_non_apparie": "Lieu du calendrier scolaire sans académie",
    "calendrier_manquant": "Académie sans calendrier scolaire",
    "calendrier_indisponible": "Calendrier scolaire indisponible",
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
    """Volume trop faible sur l'une des deux semaines comparées : pourcentage peu fiable, jamais signalé."""
    return {m: not metrics.has_period_volume(kpis, m, metrics.WATCH_DAYS) for m in ("new_users", "messages")}


def missing_publication_dates():
    """Dates d'activité sans aucun fichier publié, entre le premier et le dernier snapshot."""
    published = {d - timedelta(days=1) for d in ProductionObservation.objects.values_list("date", flat=True).distinct()}
    if not published:
        return set()
    first, last = min(published), max(published)
    return {first + timedelta(days=i) for i in range((last - first).days + 1)} - published


MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]


def long_date(day, year=True):
    """Date en toutes lettres, ex. « 4 septembre 2026 » (« 1er » pour le premier du mois)."""
    return f"{day_number(day)} {MONTHS[day.month - 1]}" + (f" {day.year}" if year else "")


def day_number(day):
    return "1er" if day.day == 1 else str(day.day)


def date_range(first, last):
    """« le 4 septembre », « du 4 au 8 septembre », « du 30 septembre au 2 octobre »."""
    if first == last:
        return f"le {long_date(first, year=False)}"
    start = day_number(first) if first.month == last.month else long_date(first, year=False)
    return f"du {start} au {long_date(last, year=False)}"


def _missing_run(missing, day):
    """Suite de jours manquants contenant `day` : (premier, dernier)."""
    first = last = day
    while first - timedelta(days=1) in missing:
        first -= timedelta(days=1)
    while last + timedelta(days=1) in missing:
        last += timedelta(days=1)
    return first, last


def approximation_note(missing, end):
    """Note affichée au-dessus des actions quand une comparaison sur 14 jours traverse des jours
    non publiés (information, pas blocage). None si la comparaison est propre."""
    days = metrics.ACTION_DAYS
    if not end or not metrics.window_boundary_gap(missing, end, days):
        return None
    boundaries = (metrics.comparison_start(end, days) - timedelta(days=1), end - timedelta(days=days), end)
    first, last = _missing_run(missing, next(b for b in boundaries if b in missing))
    clean = next((end + timedelta(days=i) for i in range(1, 2 * days + 2)
                  if not metrics.window_boundary_gap(missing, end + timedelta(days=i), days)), None)
    # Dates de fichier = date d'activité + 1 jour ; le rattrapage est compté le premier jour publié suivant.
    note = (f"Les fichiers {date_range(first + timedelta(days=1), last + timedelta(days=1))} n’ont pas été publiés ; "
            f"l’activité {date_range(first, last)} est comptée le {long_date(last + timedelta(days=1), year=False)}.")
    if clean:
        note += f" Jusqu’au {long_date(clean - timedelta(days=1), year=False)}, les comparaisons sur quatorze jours sont donc approximatives."
    return {"text": note, "until": clean - timedelta(days=1) if clean else None}


def signal_context(series_by_domain=None):
    """Références et contraintes communes à toutes les académies.

    - `national7` : la France entière, pour l'indicateur principal et la carte ;
    - `reference7` / `reference14` : l'ensemble des académies (hors domaines nationaux,
      régionaux et opérateurs, dont l'évolution propre biaiserait la comparaison).
    """
    series_by_domain = series_by_domain or _domain_series(ProductionObservation.objects.all())
    located = {d for d, loc in ProductionObservation.objects.order_by("domain", "date")
               .values_list("domain", "location_id") if loc}  # la plus récente gagne
    located = {d for d in series_by_domain if d in located}
    national = metrics.aggregate_series(series_by_domain.values())
    academies = metrics.aggregate_series(series_by_domain[d] for d in located)
    missing = missing_publication_dates()
    n7 = metrics.compute_kpis(national)
    end = n7["end_date"]
    note = approximation_note(missing, end)
    # Dernière date d'activité de chaque domaine : un domaine absent des derniers fichiers est prolongé
    # dans les séries (cumul conservé) sans être marqué `is_gap` ; on le repère ici.
    last_seen = {row["domain"]: row["last"] - timedelta(days=1) for row in
                 ProductionObservation.objects.values("domain").annotate(last=Max("date"))}
    return {"national7": n7,
            "reference7": metrics.compute_kpis(academies),
            "reference14": metrics.compute_kpis(academies, days=metrics.ACTION_DAYS),
            "end": end, "missing": missing, "last_seen": last_seen,
            "action_status": {"ok": True, "note": note["text"] if note else None,
                              "approximate_until": note["until"] if note else None}}


# Un jour ouvré sans aucun nouveau compte ni message, pour une académie qui en reçoit
# habituellement au moins autant, signale un cumul figé ou un fichier incomplet.
FROZEN_MIN_MESSAGES = 50


def has_frozen_day(series, start, end, missing):
    days = [p for p in series if start <= p["date"] <= end and p["date"] not in missing
            and p["new_users"] is not None]
    active = sorted(p["daily_messages"] for p in days if p["daily_messages"] > 0)
    if not active or active[len(active) // 2] < FROZEN_MIN_MESSAGES:
        return False
    return any(p["date"].weekday() < 5 and p["new_users"] == 0 and p["daily_messages"] == 0 for p in days)


def action_block_reason(location, domain_series, ctx):
    """Raison pour laquelle l'académie ne peut pas déclencher d'action (None si rien ne s'y oppose).

    `domain_series` : {domaine: série quotidienne} des domaines de l'académie.
    Contexte, pas exclusion : le signal reste visible en « à suivre ».
    """
    end = ctx["end"]
    start = metrics.comparison_start(end, metrics.ACTION_DAYS)
    before = start - timedelta(days=1)
    for domain, series in domain_series.items():
        if not series or series[0]["date"] >= start:
            return "modification du périmètre des données"
        if before <= ctx["last_seen"].get(domain, end) < end:
            return "données de l’académie incomplètes"
        # Les jours sans aucun fichier publié touchent tous les domaines : ce n'est pas propre à l'académie.
        if any((p["is_gap"] and p["date"] not in ctx["missing"]) or p["is_decrease"]
               for p in series if before <= p["date"] <= end):
            return "données de l’académie incomplètes"
    if has_frozen_day(metrics.aggregate_series(domain_series.values()), start, end, ctx["missing"]):
        return "données de l’académie incomplètes"
    for holiday in SchoolHoliday.objects.filter(location=location, start__lte=end, end__gte=start).order_by("start"):
        if not is_common_holiday(holiday):
            return f"{holiday.description.lower()} ({holiday.start:%d/%m}–{holiday.end:%d/%m})"
    return None


METROPOLITAN_ZONES = ("Zone A", "Zone B", "Zone C")
COMMON_HOLIDAY_TOLERANCE = timedelta(days=3)


def is_common_holiday(holiday):
    """Vacances communes aux zones A, B et C : mêmes dates à trois jours près, ou incluses dans une
    période commune (ex. pont de l'Ascension de la Corse). Elles touchent tout le pays en même temps
    et sont neutralisées par la comparaison entre académies. Seules les autres bloquent : hiver et
    printemps décalés par zone, outre-mer."""
    tol = COMMON_HOLIDAY_TOLERANCE
    candidates = SchoolHoliday.objects.filter(zone__in=METROPOLITAN_ZONES,
                                              start__lte=holiday.end + tol, end__gte=holiday.start - tol)
    zones = {h.zone for h in candidates
             if (abs(h.start - holiday.start) <= tol and abs(h.end - holiday.end) <= tol)
             or (h.start <= holiday.start and holiday.end <= h.end)}
    return zones == set(METROPOLITAN_ZONES)


def academy_signals(location, domain_series, ctx):
    """KPI 7 jours, KPI 14 jours et signaux d'une académie, avec les mêmes références partout."""
    series = metrics.aggregate_series(domain_series.values())
    k7, k14 = metrics.compute_kpis(series), metrics.compute_kpis(series, days=metrics.ACTION_DAYS)
    blocked = action_block_reason(location, domain_series, ctx)
    signals = metrics.detect_signals(k7, ctx["reference7"], k14, ctx["reference14"], blocked)
    until = ctx["action_status"]["approximate_until"]
    for s in signals:  # mention portée par chaque signal sur 14 jours tant que la comparaison est approximative
        s["approximate_until"] = until.isoformat() if until and s["window"] == metrics.ACTION_DAYS else None
    return series, k7, k14, signals


def signal_thresholds():
    return {
        "watch_days": metrics.WATCH_DAYS, "action_days": metrics.ACTION_DAYS,
        "watch_rel_pct": round(metrics.WATCH_REL * 100), "action_rel_pct": round(metrics.ACTION_REL * 100),
        "min_volume": {str(d): v for d, v in metrics.MIN_PERIOD_VOLUME.items()},
        "stable_pct": round(metrics.STABLE_THRESHOLD * 100),
    }


def locations_payload():
    """Métriques et signaux par académie appariée + domaines non localisés."""
    series_by_domain = _domain_series(ProductionObservation.objects.all())
    domain_info = {
        d: (cat, loc_id, label) for d, cat, loc_id, label in
        ProductionObservation.objects.order_by("domain", "date")  # la plus récente gagne
        .values_list("domain", "category", "location_id", "label")
    }
    by_location = defaultdict(dict)
    unlocated = []
    for domain, series in series_by_domain.items():
        category, loc_id, label = domain_info[domain]
        if loc_id:
            by_location[loc_id][domain] = series
        else:
            kpis = metrics.compute_kpis(series)
            unlocated.append({"domain": domain, "label": label, "category": category,
                              **serialize_kpis(kpis)})

    ctx = signal_context(series_by_domain)
    national = ctx["national7"]
    locations, signals = [], []
    for loc in BetaLocation.objects.filter(pk__in=by_location.keys()).defer("geo_shape"):
        _, kpis, kpis14, loc_signals = academy_signals(loc, by_location[loc.pk], ctx)
        locations.append({
            "id": loc.pk, "name": loc.display_name, "full_name": loc.full_name,
            "lat": loc.latitude, "lon": loc.longitude,
            "signals": loc_signals,
            "low_volume": low_volume(kpis),
            "previous_new_users_14": kpis14["previous_new_users"], "new_users_14": kpis14["new_users"],
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
        "action_status": serialize_kpis(ctx["action_status"]),
        "signal_thresholds": signal_thresholds(),
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
