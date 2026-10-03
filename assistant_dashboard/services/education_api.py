"""Client HTTP minimal pour l'API Explore v2.1 de data.education.gouv.fr.

Ce module ne fait que récupérer des données brutes : aucune transformation,
aucun accès à la base.
"""
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BETA_DATASET = "assistant-ia-dinum-deploiement-au-ministere-de-leducation-nationale-en-academie"
PRODUCTION_DATASET = "fr-en-assistant_ia_deploiement_menjs"
CONTOURS_DATASET = "fr-en-contour-academies-2020"

TIMEOUT = (5, 30)  # (connexion, lecture) en secondes
PAGE_SIZE = 100  # maximum autorisé par l'endpoint /records
MAX_ATTEMPTS = 2
MAX_OFFSET = 10_000  # limite de l'endpoint /records (offset + limit)


class EducationAPIError(Exception):
    pass


def _headers():
    headers = {"Accept": "application/json"}
    if settings.EDUCATION_API_KEY:
        headers["Authorization"] = f"Apikey {settings.EDUCATION_API_KEY}"
    return headers


def _get(url, params=None, session=None):
    """GET avec timeout et une nouvelle tentative sur erreur réseau ou 5xx."""
    session = session or requests.Session()
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, headers=_headers(), timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_error = f"Erreur réseau : {exc}"
        else:
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise EducationAPIError(f"Réponse non JSON pour {url}") from exc
            last_error = f"HTTP {response.status_code} pour {url}"
            if response.status_code < 500:
                break  # erreur client : inutile de réessayer
        logger.warning("Tentative %s/%s échouée : %s", attempt, MAX_ATTEMPTS, last_error)
        if attempt < MAX_ATTEMPTS:
            time.sleep(1)
    raise EducationAPIError(last_error)


def fetch_all_records(dataset, session=None, page_size=PAGE_SIZE):
    """Récupère tous les enregistrements d'un dataset en suivant la pagination."""
    url = f"{settings.EDUCATION_API_BASE}/{dataset}/records/"
    records, offset, total = [], 0, None
    while total is None or offset < total:
        if offset + page_size > MAX_OFFSET:
            raise EducationAPIError(
                f"Plus de {MAX_OFFSET} enregistrements : utiliser l'endpoint /exports."
            )
        payload = _get(url, {"limit": page_size, "offset": offset, "lang": "fr"}, session)
        total = payload.get("total_count", 0)
        page = payload.get("results", [])
        records.extend(page)
        if not page:
            break
        offset += len(page)
    logger.info("%s : %s enregistrements récupérés", dataset, len(records))
    return records


def fetch_field_labels(dataset, session=None):
    """Retourne {nom_colonne: libellé} à partir des métadonnées du dataset."""
    payload = _get(f"{settings.EDUCATION_API_BASE}/{dataset}", session=session)
    return {f["name"]: f.get("label") or f["name"] for f in payload.get("fields", [])}
