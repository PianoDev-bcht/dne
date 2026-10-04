"""Données de test : enregistrements au format réel de l'API (format large)."""
from datetime import date, timedelta

BETA_RECORDS = [
    {"libelle_aca": "Lyon", "email_academie": "ac-lyon.fr", "email_region_academique": None,
     "chefs_d_etablissement": 1, "enseignants": 2, "administration": 3, "inspecteurs": 4, "total": 10,
     "latitude": 45.7, "longitude": 4.8},
    {"libelle_aca": "Polynesie", "email_academie": "ac-polynesie.pf", "email_region_academique": "@ac-polynesie.pf",
     "chefs_d_etablissement": 0, "enseignants": 1, "administration": 1, "inspecteurs": 0, "total": 2,
     "latitude": -17.6, "longitude": -149.4},
]

CONTOUR_RECORDS = [
    {"name": "Lyon", "geo_shape": {"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[4.1234, 45.6789], [5.0, 45.7], [5.0, 46.0], [4.5, 46.1], [4.1234, 45.6789]]]}}},
    {"name": "Atlantide", "geo_shape": {"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}},
]

LABELS = {
    "ac_lyon_fr_users": "Nombre d'utilisateurs de l'académie de Lyon",
    "ac_polynesie_pf_users": "Nombre d'utilisateurs du vice-rectorat de Polynésie française",
    "pix_fr_utilisateurs": "pix.fr utilisateurs",
    "region_academique_paca_fr_users": "Nombre d'utilisateurs de la région académique PACA",
}


def production_record(day, lyon=(10, 100), polynesie=(1, 5), pix=(2, 3), paca=(0, 0), hour="01:00:01"):
    return {
        "cree_le": f"{day.isoformat()}T03:00:00+00:00",
        "timestamp": f"{day.isoformat()}T{hour}+00:00",
        "ac_lyon_fr_users": lyon[0], "ac_lyon_fr_messages": lyon[1],
        "ac_polynesie_pf_users": polynesie[0], "ac_polynesie_pf_messages": polynesie[1],
        "pix_fr_utilisateurs": pix[0], "pix_fr_messages": pix[1],
        "region_academique_paca_fr_users": paca[0], "region_academique_paca_fr_messages": paca[1],
    }


def production_series(days=3, start=date(2026, 9, 1)):
    return [production_record(start + timedelta(i), lyon=(10 + i, 100 + 10 * i)) for i in range(days)]


# Calendrier scolaire au format réel de l'API (dates en UTC, `end_date` = jour de reprise).
CALENDAR_RECORDS = [
    {"description": "Vacances de la Toussaint", "population": "-", "start_date": "2026-10-16T22:00:00+00:00",
     "end_date": "2026-11-01T23:00:00+00:00", "location": "Lyon", "zones": "Zone A", "annee_scolaire": "2026-2027"},
    {"description": "Pont de l'Ascension", "population": "-", "start_date": "2027-05-06T22:00:00+00:00",
     "end_date": "2027-05-06T22:00:00+00:00", "location": "Lyon", "zones": "Zone A", "annee_scolaire": "2026-2027"},
    {"description": "Rentrée scolaire des enseignants", "population": "Enseignants",
     "start_date": "2026-08-30T22:00:00+00:00", "end_date": "2026-08-30T22:00:00+00:00", "location": "Lyon",
     "zones": "Zone A", "annee_scolaire": "2026-2027"},
    {"description": "Vacances de Noël", "population": "-", "start_date": "2026-12-18T23:00:00+00:00",
     "end_date": "2027-01-03T23:00:00+00:00", "location": "Saint Pierre et Miquelon",
     "zones": "Saint Pierre et Miquelon", "annee_scolaire": "2026-2027"},
    {"description": "Vacances de Noël", "population": "-", "start_date": "2026-12-18T23:00:00+00:00",
     "end_date": "2027-01-03T23:00:00+00:00", "location": "Atlantide", "zones": "Zone Z", "annee_scolaire": "2026-2027"},
]
