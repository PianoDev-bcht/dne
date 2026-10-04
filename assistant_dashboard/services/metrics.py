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
import math
from datetime import timedelta

WINDOW_DAYS = 7

# Une variation de moins de 5 % (en valeur absolue) est « stable » partout :
# flèches, phrase de synthèse et classe neutre de la carte.
STABLE_THRESHOLD = 0.05

# Signaux territoriaux : 7 jours pour détecter, 14 jours pour agir.
# Mesure unique : l'évolution de l'académie relative à la tendance de l'ensemble des
# académies (hors domaines nationaux et opérateurs, dont l'évolution propre biaiserait
# la comparaison), (1 + évolution académie) / (1 + évolution de référence) − 1, soit la
# variation de sa part dans les nouveaux comptes des académies. Seuils = conventions de
# pilotage, à recalibrer après plusieurs semaines d'historique propre.
WATCH_DAYS = 7     # détection précoce : « à suivre », aucune action
ACTION_DAYS = 14   # décision : « à analyser », déclenche une action
WATCH_REL = 0.15
ACTION_REL = 0.20
# Volume minimal sur CHACUNE des deux périodes comparées (une rupture de périmètre
# ou une petite base ne peut pas se cacher derrière la période la plus grande).
MIN_PERIOD_VOLUME = {
    WATCH_DAYS: {"new_users": 40, "messages": 400},
    ACTION_DAYS: {"new_users": 80, "messages": 800},  # deux fois le minimum hebdomadaire
}


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


def daily_points(series, key, last_n_days=None):
    """Flux quotidien pour chaque date (pour les graphiques) : écart entre un fichier et le précédent.

    `has_gap` marque les jours sans fichier publié et le jour de rattrapage qui
    les suit : son écart couvre plusieurs jours, ce n'est pas une valeur quotidienne.
    Les flux négatifs (baisse de cumul) sont ramenés à 0, comme dans les sommes.
    """
    points = []
    for i, p in enumerate(series):
        if p[key] is None:  # premier jour : pas de fichier précédent
            continue
        catch_up = i > 0 and series[i - 1]["is_gap"]
        points.append({"date": p["date"], "value": max(p[key], 0), "has_gap": p["is_gap"] or catch_up})
    if last_n_days:
        points = points[-last_n_days:]
    return points


def trend(value, threshold=STABLE_THRESHOLD):
    """Sens d'une variation : "up", "down" ou "flat" (|variation| < seuil)."""
    if value is None or abs(value) < threshold:
        return "flat"
    return "up" if value > 0 else "down"


def relative_change(local_pct, national_pct):
    """Évolution relative à la tendance nationale ; None si non calculable."""
    if local_pct is None or national_pct is None or national_pct <= -1:
        return None
    return (1 + local_pct) / (1 + national_pct) - 1


def reaches(value, threshold):
    """|value| ≥ threshold, tolérant aux arrondis flottants (−20 % pile atteint le seuil de 20 %)."""
    return value is not None and abs(value) >= threshold - 1e-9


def has_period_volume(kpis, metric, days):
    """Vrai si les deux périodes comparées atteignent chacune le volume minimal."""
    minimum = MIN_PERIOD_VOLUME[days][metric]
    return all((kpis.get(k) or 0) >= minimum for k in (metric, f"previous_{metric}"))


def comparison_start(end_date, days):
    """Premier jour de la période précédente : la comparaison couvre 2 × `days` jours."""
    return end_date - timedelta(days=2 * days - 1)


def window_boundary_gap(missing_dates, end_date, days):
    """Vrai si un rattrapage de jour non publié traverse une limite des périodes comparées.

    Un jour manquant est rattrapé le premier jour publié suivant. Un trou situé
    entièrement dans une période ne fausse pas son total ; il le fausse s'il
    tombe la veille du début de la période précédente, le dernier jour de la
    période précédente (rattrapage dans la période récente) ou le dernier jour
    de la période récente (rattrapage après la fin).
    """
    boundaries = (comparison_start(end_date, days) - timedelta(days=1),
                  end_date - timedelta(days=days), end_date)
    return any(d in missing_dates for d in boundaries)


def display_pct(value):
    """Pourcentage entier pour l'affichage, arrondi vers zéro avec la même tolérance que `reaches` :
    −19,75 % s'affiche −19 % (le seuil de 20 % n'est pas atteint) ; −0,1999999… s'affiche −20 %."""
    if value is None:
        return None
    return math.trunc(round(value * 100, 6))


def detect_signals(kpis7, reference7, kpis14, reference14, blocked=None):
    """Signaux territoriaux d'une académie (liste vide si rien de notable).

    La semaine sert à voir, la quinzaine à décider. Référence : tendance de l'ensemble
    des académies.
    - diffusion, 14 jours : écart relatif d'au moins ACTION_REL, volumes suffisants sur
      les deux périodes, CONFIRMÉ par la dernière semaine (écart sur 7 jours hors zone
      stable, dans le même sens, avec au moins le volume hebdomadaire minimal) -> « strong » ;
      sinon « watch » avec sa raison ; si la dernière semaine va nettement en sens
      contraire, c'est ce signal récent qui est retenu ;
    - diffusion, 7 jours : sinon, écart relatif d'au moins WATCH_REL -> « watch » ;
    - activation, 14 jours : nouveaux comptes en hausse et intensité relative en baisse
      (« strong ») ou stable (« watch ») ; jamais en même temps qu'un ralentissement fort.
    `blocked` : raison pour laquelle aucune action ne peut être déclenchée (données
    incomplètes, périmètre modifié, vacances propres à l'académie) ; un signal fort est
    alors rétrogradé en « watch » avec cette raison.
    """
    def level(strong, reason=None):
        reason = reason or blocked
        return ("strong", None) if strong and not reason else ("watch", reason if strong else None)

    rel7 = relative_change(kpis7.get("new_users_change_pct"), reference7.get("new_users_change_pct"))
    rel14 = relative_change(kpis14.get("new_users_change_pct"), reference14.get("new_users_change_pct"))
    vol7 = has_period_volume(kpis7, "new_users", WATCH_DAYS)
    vol14 = has_period_volume(kpis14, "new_users", ACTION_DAYS)
    week7 = (WATCH_DAYS, rel7, kpis7, reference7, False, None)

    diffusion = None
    if reaches(rel14, ACTION_REL) and vol14:
        if not vol7:
            unconfirmed = "volume insuffisant sur la dernière semaine"
        elif trend(rel7) != trend(rel14):
            unconfirmed = "tendance sur 14 jours non confirmée par la dernière semaine"
        else:
            unconfirmed = None
        opposite = vol7 and reaches(rel7, WATCH_REL) and (rel7 > 0) != (rel14 > 0)
        # Une dernière semaine nettement contraire n'est pas masquée par la tendance sur 14 jours.
        diffusion = week7 if unconfirmed and opposite else (ACTION_DAYS, rel14, kpis14, reference14, True, unconfirmed)
    elif reaches(rel7, WATCH_REL) and vol7:
        diffusion = week7

    signals = []
    if diffusion:
        days, rel, k, ref, strong, unconfirmed = diffusion
        lvl, reason = level(strong, unconfirmed)
        signals.append({
            "kind": "ecart_superieur" if rel > 0 else "ecart_inferieur", "metric": "new_users",
            "window": days, "level": lvl, "reason": reason, "relative": rel, "relative7": rel7,
            # Les deux horizons, côte à côte (None si le volume ne permet pas de conclure).
            "relative14": rel14 if vol14 else None,
            "display": {"rel14": display_pct(rel14 if vol14 else None), "rel7": display_pct(rel7 if vol7 else None)},
            "evolution": k["new_users_change_pct"], "national": ref["new_users_change_pct"],
            "previous": k["previous_new_users"], "current": k["new_users"], "severity": abs(rel),
        })
    slowdown = any(s["kind"] == "ecart_inferieur" and s["level"] == "strong" for s in signals)
    users = kpis14.get("new_users_change_pct")
    intensity = relative_change(kpis14.get("activity_change_pct"), reference14.get("activity_change_pct"))
    if (not slowdown and intensity is not None and users is not None and users > 0
            and reaches(users, STABLE_THRESHOLD) and not (intensity > 0 and reaches(intensity, STABLE_THRESHOLD))
            and vol14 and has_period_volume(kpis14, "messages", ACTION_DAYS)):
        lvl, reason = level(intensity < 0 and reaches(intensity, STABLE_THRESHOLD))
        signals.append({
            "kind": "activation", "metric": "both", "window": ACTION_DAYS, "level": lvl, "reason": reason,
            "evolution_users": users, "evolution_intensity": intensity,
            "display": {"users": display_pct(users), "intensity": display_pct(intensity)},
            "previous": kpis14["previous_new_users"], "current": kpis14["new_users"],
            "severity": users - intensity,
        })
    return sorted(signals, key=lambda s: (s["level"] != "strong", -s["severity"]))
