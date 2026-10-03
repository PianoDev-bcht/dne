"""Vues du dashboard. Elles lisent uniquement la base locale, jamais l'API distante."""
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import BetaLocation
from .services.matching import normalize_name
from .services import dashboard_data as data
from .services import metrics
from .services.metrics import compute_kpis


def reco_status(kpis, signals):
    """Académies concernées par chaque recommandation cette semaine.

    Lecture seule des KPI et signaux existants (`detect_signals`), aucun nouveau calcul.
    """
    def academies(match):
        """Académies concernées avec le signal qui les déclenche, le plus marqué en premier."""
        found = [{"id": g["id"], "name": g["name"], **s}
                 for g in signals for s in g["signals"] if match(s)]
        return sorted(found, key=lambda a: -a["severity"])

    def strong_diffusion(kind):
        return lambda s: s["metric"] == "new_users" and s["kind"] == kind and s["level"] == "strong"

    # Seuls les signaux forts déclenchent une action ; les signaux « à surveiller » restent dans le diagnostic.
    return {
        "acceleration": academies(strong_diffusion("ecart_superieur")),
        "slowdown": academies(strong_diffusion("ecart_inferieur")),
        # Comptes en hausse, intensité en baisse : l'usage ne suit pas la diffusion.
        "activation": academies(lambda s: s["kind"] == "activation" and s["level"] == "strong"),
        # Au niveau national : diffusion en hausse, intensité stable ou en baisse.
        "activation_national": metrics.trend(kpis["new_users_change_pct"]) == "up"
        and kpis["activity_change_pct"] is not None and metrics.trend(kpis["activity_change_pct"]) != "up",
    }


def dashboard(request):
    kpis = compute_kpis(data.national_series())
    payload = data.locations_payload()
    return render(request, "assistant_dashboard/dashboard.html", {
        "kpis": kpis,
        "freshness": data.freshness(),
        "unlocated": data.unlocated_summary(payload),
        "reco": reco_status(kpis, payload["signals"]),
        "academies": sorted(BetaLocation.objects.defer("geo_shape"), key=lambda l: normalize_name(l.display_name)),
        "signal_thresholds": {"min_base": metrics.SIGNAL_MIN_BASE, "min_gap_pts": round(metrics.SIGNAL_MIN_GAP * 100),
                              "watch_gap_pts": round(metrics.SIGNAL_WATCH_GAP * 100),
                              "stable_pct": round(metrics.STABLE_THRESHOLD * 100)},
    })


def api_summary(request):
    freshness = data.freshness()
    return JsonResponse({
        "scope": "national",
        "name": "France entière",
        "kpis": data.serialize_kpis(compute_kpis(data.national_series())),
        "freshness": data.serialize_kpis(freshness),
    })


def api_timeseries(request):
    scope = request.GET.get("scope", "national")
    period = request.GET.get("period", data.DEFAULT_PERIOD)
    if scope == "national":
        series = data.national_series()
    elif scope.isdigit():
        series = data.location_series(get_object_or_404(BetaLocation, pk=scope))
    else:
        raise Http404("Périmètre inconnu")
    return JsonResponse(data.chart_payload(series, period))


def api_locations(request):
    return JsonResponse(data.locations_payload())


def api_shapes(request):
    response = JsonResponse(data.shapes_payload())
    response["Cache-Control"] = "public, max-age=86400"  # contours statiques
    return response


def api_location(request, pk):
    location = get_object_or_404(BetaLocation, pk=pk)
    series = data.location_series(location)
    kpis = compute_kpis(series)
    national = compute_kpis(data.national_series())
    return JsonResponse({
        "scope": "location",
        "id": location.pk,
        "name": location.display_name,
        "full_name": location.full_name,
        "kpis": data.serialize_kpis(kpis),
        "signals": metrics.detect_signals(kpis, national),
        "low_volume": data.low_volume(kpis),
        # Contexte « France » du panneau, servi avec l'académie (pas de dépendance à l'ordre des requêtes).
        "national": {k: national[k] for k in ("new_users_change_pct", "messages_change_pct", "activity_per_100_accounts",
                                              "activity_change_pct")},
        "chart": data.chart_payload(series, request.GET.get("period", data.DEFAULT_PERIOD)),
    })
