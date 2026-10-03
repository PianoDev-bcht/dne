"""Contrôles de qualité sur les observations de production (fonctions pures).

Chaque contrôle renvoie une liste d'anomalies {type, domain, date, detail}.
Une anomalie est un signal pour l'équipe : elle ne bloque jamais l'import
ni l'affichage du dashboard.
"""
from datetime import timedelta


MAX_DETAIL = 120  # le détail peut reprendre une valeur de la source : on borne ce qui est stocké et affiché


def anomaly(kind, detail, domain="", date=None):
    if len(detail) > MAX_DETAIL:
        detail = detail[:MAX_DETAIL - 1] + "…"
    return {"type": kind, "domain": domain, "date": date.isoformat() if date else "", "detail": detail}


def check_calendar_gaps(dates):
    """Jours sans snapshot entre la première et la dernière date."""
    dates = sorted(set(dates))
    found = []
    for previous, current in zip(dates, dates[1:]):
        missing = (current - previous).days - 1
        if missing > 0:
            first_missing = previous + timedelta(days=1)
            found.append(anomaly(
                "date_manquante",
                f"{missing} jour(s) sans données du {first_missing:%d/%m/%Y} au "
                f"{current - timedelta(days=1):%d/%m/%Y}",
                date=first_missing,
            ))
    return found


def check_missing_domains(values_by_date):
    """Domaines présents dans certains snapshots mais absents d'autres.

    `values_by_date` : {date: {domain: ...}}.
    """
    all_domains = set().union(*values_by_date.values()) if values_by_date else set()
    found = []
    for day in sorted(values_by_date):
        for domain in sorted(all_domains - set(values_by_date[day])):
            found.append(anomaly("domaine_absent", "domaine absent de ce snapshot", domain, day))
    return found


def check_decreases(series_by_domain):
    """Baisse d'un compteur cumulé d'un snapshot au suivant.

    `series_by_domain` : {domain: [(date, users, messages), ...]} trié par date.
    """
    found = []
    for domain, series in series_by_domain.items():
        for (_, u0, m0), (day, u1, m1) in zip(series, series[1:]):
            if u1 < u0:
                found.append(anomaly("baisse_utilisateurs", f"comptes cumulés {u0} -> {u1}", domain, day))
            if m1 < m0:
                found.append(anomaly("baisse_messages", f"messages cumulés {m0} -> {m1}", domain, day))
    return found


def check_new_domains(domains, known_domains):
    """Domaines jamais vus lors des imports précédents (ignoré au premier import)."""
    if not known_domains:
        return []
    return [anomaly("nouveau_domaine", "domaine apparu dans les données", d)
            for d in sorted(set(domains) - set(known_domains))]

