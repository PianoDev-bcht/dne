"""Calcul des métriques dérivées à partir des compteurs cumulés (fonctions pures).

Convention : chaque point de série est indexé par sa *date d'activité*
(date du snapshot - 1 jour), car le snapshot de 03:00 le jour D contient les
cumuls à la fin du jour D-1.

Les flux quotidiens s'obtiennent par différence :
    new_users[t]      = cumulative_users[t]    - cumulative_users[t-1]
    daily_messages[t] = cumulative_messages[t] - cumulative_messages[t-1]

On ne calcule jamais daily_messages / new_users : les messages d'un jour
peuvent venir de comptes créés bien avant.
"""
from datetime import timedelta

WINDOW_DAYS = 7

# Une variation de moins de 5 % (en valeur absolue) est « stable » partout :
# flèches, phrase de synthèse et classe neutre de la carte.
STABLE_THRESHOLD = 0.05

# Signaux territoriaux. Même référence que tous les autres chiffres (7 derniers
# jours vs 7 précédents), comparée à la tendance nationale, sur des volumes
# suffisants. Deux niveaux : on repère tôt (à surveiller), on ne recommande
# d'agir que sur un écart net (à investiguer).
# Volume minimal sur la plus grande des deux semaines comparées : en dessous de
# 50 comptes, le hasard seul fait varier la semaine de plus de ±20 points.
SIGNAL_MIN_BASE = {"new_users": 50, "messages": 400}
SIGNAL_WATCH_GAP = 0.15  # écart à la tendance nationale, en points d'évolution : à surveiller
SIGNAL_MIN_GAP = 0.30    # à investiguer (signal fort)


def build_daily_series(points, end=None):
    """Construit une série quotidienne continue pour un domaine.

    `points` : itérable de (date, cumulative_users, cumulative_messages).
    Les jours sans donnée reprennent le dernier cumul connu (`is_gap=True`) :
    leur flux vaut 0 et le rattrapage tombe le jour où la donnée réapparaît.
    Le premier jour n'a pas de flux calculable (None).
    `end` : prolonge la série jusqu'à cette date (domaine absent des derniers
    snapshots), pour que son cumul reste compté dans les agrégats. Ces jours
    ne sont pas marqués `is_gap` : un domaine retiré du fichier ne doit pas
    interrompre indéfiniment les séries agrégées (l'absence est signalée par
    les contrôles qualité).
    """
    known = {d: (u, m) for d, u, m in points}
    if not known:
        return []
    day, last_known = min(known), max(known)
    last_day = max(last_known, end) if end else last_known
    series, previous = [], None
    while day <= last_day:
        missing = day not in known
        is_gap = missing and day < last_known
        users, messages = known[day] if not missing else previous[:2]
        if previous is None:
            new_users = daily_messages = None
        else:
            new_users = users - previous[0]
            daily_messages = messages - previous[1]
        series.append({
            "date": day,
            "cumulative_users": users,
            "cumulative_messages": messages,
            "new_users": new_users,
            "daily_messages": daily_messages,
            "is_gap": is_gap,
            "is_decrease": bool((new_users or 0) < 0 or (daily_messages or 0) < 0),
        })
        previous = (users, messages)
        day += timedelta(days=1)
    return series


def aggregate_series(all_series):
    """Somme plusieurs séries quotidiennes (ex. tous les domaines -> national).

    Un domaine n'est compté qu'à partir de sa première date : son premier
    cumul n'est pas compté comme flux (pas de point de référence).
    Les flux négatifs (baisse de cumul = anomalie) sont ramenés à 0 domaine
    par domaine, comme dans `window_total` : la somme des parties reste égale
    au total.
    """
    by_date = {}
    for series in all_series:
        for p in series:
            agg = by_date.setdefault(p["date"], {
                "date": p["date"], "cumulative_users": 0, "cumulative_messages": 0,
                "new_users": 0, "daily_messages": 0, "is_gap": False, "is_decrease": False,
                "_has_flow": False,
            })
            agg["cumulative_users"] += p["cumulative_users"]
            agg["cumulative_messages"] += p["cumulative_messages"]
            if p["new_users"] is not None:
                agg["new_users"] += max(p["new_users"], 0)
                agg["daily_messages"] += max(p["daily_messages"], 0)
                agg["_has_flow"] = True
            agg["is_gap"] |= p["is_gap"]
            agg["is_decrease"] |= p["is_decrease"]
    result = []
    for day in sorted(by_date):
        agg = by_date[day]
        if not agg.pop("_has_flow"):
            agg["new_users"] = agg["daily_messages"] = None
        result.append(agg)
    return result


def window_total(series, end_index, key, days=WINDOW_DAYS):
    """Somme d'un flux sur `days` jours se terminant à `end_index` (inclus).

    Les flux négatifs (baisse de cumul = anomalie) sont ramenés à 0.
    Retourne None si la fenêtre sort de la série ou contient un jour sans flux.
    """
    start = end_index - days + 1
    if start < 0 or end_index >= len(series):
        return None
    values = [series[i][key] for i in range(start, end_index + 1)]
    if any(v is None for v in values):
        return None
    return sum(max(v, 0) for v in values)


def window_has_gap(series, end_index, days=WINDOW_DAYS):
    """Vrai si la fenêtre (ou le jour qui la précède, où un rattrapage peut
    tomber) contient des jours sans donnée source."""
    start = max(end_index - days, 0)
    return any(p["is_gap"] for p in series[start:end_index + 1])


def pct_change(current, previous):
    """Variation relative ; None si non calculable (base nulle ou manquante)."""
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous


def abs_change(current, previous):
    if current is None or previous is None:
        return None
    return current - previous


def activity_per_100_accounts(messages_window, average_accounts):
    """Messages sur la fenêtre pour 100 comptes en moyenne sur cette fenêtre.

    Le dénominateur est le parc moyen de la période, pas le stock du dernier
    jour : un compte créé en fin de semaine n'a pas été disponible 7 jours.
    Proxy d'intensité, pas une mesure d'utilisateurs actifs.
    """
    if messages_window is None or not average_accounts:
        return None
    return 100 * messages_window / average_accounts


def window_mean(series, end_index, key, days=WINDOW_DAYS):
    """Moyenne d'un cumul sur les `days` jours se terminant à `end_index` (None si hors série)."""
    start = end_index - days + 1
    if start < 0 or end_index >= len(series):
        return None
    return sum(series[i][key] for i in range(start, end_index + 1)) / days


def compute_kpis(series, days=WINDOW_DAYS):
    """KPI rolling sur les `days` derniers jours disponibles vs les `days` précédents."""
    if not series:
        return {
            "end_date": None, "start_date": None, "cumulative_users": None, "cumulative_messages": None,
            "new_users": None, "previous_new_users": None, "new_users_change_abs": None,
            "new_users_change_pct": None, "messages": None, "previous_messages": None,
            "messages_change_abs": None, "messages_change_pct": None,
            "activity_per_100_accounts": None, "previous_activity_per_100_accounts": None,
            "activity_change_pct": None, "window_has_gap": False, "has_decrease": False,
        }
    end = len(series) - 1
    last = series[end]
    users_now = window_total(series, end, "new_users", days)
    users_prev = window_total(series, end - days, "new_users", days)
    msg_now = window_total(series, end, "daily_messages", days)
    msg_prev = window_total(series, end - days, "daily_messages", days)
    # Intensité : messages de la fenêtre / parc moyen sur cette même fenêtre (idem pour la précédente).
    activity_now = activity_per_100_accounts(msg_now, window_mean(series, end, "cumulative_users", days))
    activity_prev = activity_per_100_accounts(msg_prev, window_mean(series, end - days, "cumulative_users", days))
    return {
        "end_date": last["date"],
        "start_date": last["date"] - timedelta(days=days - 1),
        "cumulative_users": last["cumulative_users"],
        "cumulative_messages": last["cumulative_messages"],
        "new_users": users_now,
        "previous_new_users": users_prev,
        "new_users_change_abs": abs_change(users_now, users_prev),
        "new_users_change_pct": pct_change(users_now, users_prev),
        "messages": msg_now,
        "previous_messages": msg_prev,
        "messages_change_abs": abs_change(msg_now, msg_prev),
        "messages_change_pct": pct_change(msg_now, msg_prev),
        "activity_per_100_accounts": activity_now,
        "previous_activity_per_100_accounts": activity_prev,
        "activity_change_pct": pct_change(activity_now, activity_prev),
        "window_has_gap": window_has_gap(series, end, days) or (
            end >= days and window_has_gap(series, end - days, days)),
        "has_decrease": any(p["is_decrease"] for p in series[max(end - 2 * days + 1, 0):]),
    }


def rolling_series(series, key, days=WINDOW_DAYS, last_n_days=None):
    """Somme glissante sur `days` jours pour chaque date (pour les graphiques)."""
    points = []
    for i, p in enumerate(series):
        value = window_total(series, i, key, days)
        if value is not None:
            points.append({"date": p["date"], "value": value, "has_gap": window_has_gap(series, i, days)})
    if last_n_days:
        points = points[-last_n_days:]
    return points


def trend(value, threshold=STABLE_THRESHOLD):
    """Sens d'une variation : "up", "down" ou "flat" (|variation| < seuil)."""
    if value is None or abs(value) < threshold:
        return "flat"
    return "up" if value > 0 else "down"


def has_min_volume(kpis, metric):
    """Vrai si l'une des deux semaines comparées atteint SIGNAL_MIN_BASE pour cette mesure."""
    weeks = (kpis.get(metric), kpis.get(f"previous_{metric}"))
    return max((w or 0) for w in weeks) >= SIGNAL_MIN_BASE[metric]


def detect_signals(location_kpis, national_kpis):
    """Signaux à examiner pour une académie (liste vide si rien de notable).

    Même référence que les KPI et la carte : 7 derniers jours vs 7 précédents.
    - écart de diffusion : l'évolution des nouveaux comptes de l'académie s'écarte
      de l'évolution nationale d'au moins SIGNAL_WATCH_GAP (« watch ») ou
      SIGNAL_MIN_GAP (« strong ») ; ce qui est commun à tout le territoire s'annule ;
    - activation (l'usage ne suit pas) : nouveaux comptes en hausse et intensité
      stable (« watch ») ou en baisse (« strong ») ; même règle qu'au niveau national.
    L'activité (messages) n'est pas un signal en soi : elle sert de contexte et
    de garde-fou de volume pour l'intensité.
    Les volumes trop faibles (voir `has_min_volume`) sont ignorés.
    """
    signals = []
    evolution = location_kpis.get("new_users_change_pct")
    national = national_kpis.get("new_users_change_pct")
    has_users = evolution is not None and has_min_volume(location_kpis, "new_users")
    if has_users and national is not None:
        gap = evolution - national
        if abs(gap) >= SIGNAL_WATCH_GAP:
            signals.append({
                "kind": "ecart_superieur" if gap > 0 else "ecart_inferieur",
                "metric": "new_users",
                "level": "strong" if abs(gap) >= SIGNAL_MIN_GAP else "watch",
                "evolution": evolution,
                "national": national,
                "gap": gap,
                "severity": abs(gap),
            })
    intensity = location_kpis.get("activity_change_pct")
    if has_users and has_min_volume(location_kpis, "messages") and intensity is not None \
            and trend(evolution) == "up" and trend(intensity) != "up":
        signals.append({
            "kind": "activation", "metric": "both",
            "level": "strong" if trend(intensity) == "down" else "watch",
            "evolution_users": evolution, "evolution_intensity": intensity,
            "severity": evolution - intensity,
        })
    return sorted(signals, key=lambda s: (s["level"] != "strong", -s["severity"]))
