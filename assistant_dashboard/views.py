"""Vues du dashboard. Elles lisent uniquement la base locale, jamais l'API distante."""
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import BetaLocation
from .services.matching import normalize_name
from .services import dashboard_data as data
from .services import metrics
from .services.metrics import compute_kpis


def dashboard(request):
    return render(request, "assistant_dashboard/dashboard.html", {
        "kpis": compute_kpis(data.national_series()),
        "freshness": data.freshness(),
        "unlocated": data.unlocated_summary(),
        "academies": sorted(BetaLocation.objects.defer("geo_shape"), key=lambda l: normalize_name(l.display_name)),
        "signal_thresholds": {"min_base": metrics.SIGNAL_MIN_BASE, "min_gap_pts": round(metrics.SIGNAL_MIN_GAP * 100),
                              "divergence_pct": round(metrics.DIVERGENCE_MIN * 100),
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
    else:
        series = data.location_series(get_object_or_404(BetaLocation, pk=scope))
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
        # Contexte « France » du panneau, servi avec l'académie (pas de dépendance à l'ordre des requêtes).
        "national": {k: national[k] for k in ("new_users_change_pct", "messages_change_pct", "activity_per_100_accounts")},
        "chart": data.chart_payload(series, request.GET.get("period", data.DEFAULT_PERIOD)),
    })
