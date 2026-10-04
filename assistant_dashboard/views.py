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
        found = [{"id": g["id"], "name": g["name"], **s,
                  # Valeurs affichées : entiers calculés par `metrics.display_pct`, en ratio pour les filtres.
                  "shown": {k: (v / 100 if v is not None else None) for k, v in s["display"].items()}}
                 for g in signals for s in g["signals"] if match(s)]
        return sorted(found, key=lambda a: -a["severity"])

    def strong_diffusion(kind):
        return lambda s: s["metric"] == "new_users" and s["kind"] == kind and s["level"] == "strong"

    # Seuls les signaux forts déclenchent une action ; les signaux « à suivre » restent dans le diagnostic.
    return {
        "acceleration": academies(strong_diffusion("ecart_superieur")),
        "slowdown": academies(strong_diffusion("ecart_inferieur")),
        # Comptes en hausse, intensité en baisse relativement à la tendance nationale.
        # Les actions ne concernent que des académies (jamais la France entière).
        "activation": academies(lambda s: s["kind"] == "activation" and s["level"] == "strong"),
    }


def dashboard(request):
    kpis = compute_kpis(data.national_series())
    payload = data.locations_payload()
    return render(request, "assistant_dashboard/dashboard.html", {
        "kpis": kpis,
        "freshness": data.freshness(),
        "unlocated": data.unlocated_summary(payload),
        "reco": reco_status(kpis, payload["signals"]),
        "action_status": payload["action_status"],
        "academies": sorted(BetaLocation.objects.defer("geo_shape"), key=lambda l: normalize_name(l.display_name)),
        "signal_thresholds": data.signal_thresholds(),
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
    series_by_domain = data._domain_series(data.ProductionObservation.objects.all())
    ctx = data.signal_context(series_by_domain)
    domains = data._domain_series(data.ProductionObservation.objects.filter(location=location))
    series, kpis, kpis14, signals = data.academy_signals(location, domains, ctx)
    national = ctx["national7"]
    return JsonResponse({
        "scope": "location",
        "id": location.pk,
        "name": location.display_name,
        "full_name": location.full_name,
        "kpis": data.serialize_kpis(kpis),
        "signals": signals,
        "kpis14": data.serialize_kpis(kpis14),
        "low_volume": data.low_volume(kpis),
        # Contexte « France » du panneau, servi avec l'académie (pas de dépendance à l'ordre des requêtes).
        "national": {k: national[k] for k in ("new_users_change_pct", "messages_change_pct", "activity_per_100_accounts",
                                              "activity_change_pct")},
        # Le chiffre qui décide des actions : 14 jours, relativement à la tendance nationale.
        # Écart à la tendance des académies sur 14 jours, affiché arrondi vers zéro ; None si le volume
        # ne permet pas de conclure (ex. 2 comptes contre 55).
        "relative_14": metrics.display_pct(metrics.relative_change(
            kpis14["new_users_change_pct"], ctx["reference14"]["new_users_change_pct"]))
        if metrics.has_period_volume(kpis14, "new_users", metrics.ACTION_DAYS) else None,
        "chart": data.chart_payload(series, request.GET.get("period", data.DEFAULT_PERIOD)),
    })
