"""Normalisation des noms et rapprochement domaine de production -> académie bêta.

Clé principale : le domaine de messagerie de l'académie (bêta : `email_academie`,
ex. "ac-nancy-metz.fr") normalisé, qui correspond au préfixe des colonnes de
production (ex. "ac_nancy_metz_fr_users").

Règle : en cas de doute, on n'apparie pas. Un domaine non apparié reste compté
dans les agrégats nationaux mais n'apparaît pas sur la carte.
"""
import re
import unicodedata

# Domaines nationaux, opérateurs ou établissements : jamais localisés sur la carte.
NON_TERRITORIAL = {
    "education_gouv_fr": "Administration centrale (education.gouv.fr)",
    "igesr_gouv_fr": "IGÉSR",
    "jeunesse_sports_gouv_fr": "Jeunesse, Vie associative et Sports",
    "reseau_canope_fr": "Réseau Canopé",
    "educagri_fr": "Enseignement agricole (Educagri)",
    "aefe_fr": "AEFE",
    "ac_cned_fr": "CNED",
    "onisep_fr": "Onisep",
    "parcoursup_fr": "Parcoursup",
    "pix_fr": "Pix",
    "clemi_fr": "CLEMI",
    "siec_education_fr": "SIEC",
    "externe_phm_education_gouv_fr": "externe.phm.education.gouv.fr",
    "unistra_fr": "Université de Strasbourg",
    "lycee_chateaubriand_eu": "Lycée Chateaubriand",
    "autres": "Domaines non identifiés",
}

# Régions académiques : elles couvrent plusieurs académies, on ne peut donc pas
# les attribuer à un point unique. Liste conservée pour la documentation.
AMBIGUOUS_REGIONAL = {
    "region_academique_paca_fr": ["Aix-Marseille", "Nice"],
    "region_academique_hdf_fr": ["Amiens", "Lille"],
    "region_academique_idf_fr": ["Créteil", "Paris", "Versailles"],
    "region_academique_nouvelle_aquitaine_fr": ["Bordeaux", "Limoges", "Poitiers"],
    "region_academique_auvergne_rhone_alpes_fr": ["Clermont-Ferrand", "Grenoble", "Lyon"],
}

# Variantes de noms connues -> nom normalisé canonique (repli si la clé domaine échoue).
NAME_ALIASES = {
    "polynesie francaise": "polynesie",
    "nouvelle caledonie": "noumea",
    "clermont": "clermont ferrand",
    "reunion": "la reunion",
}


def normalize_name(value):
    """Minuscules, sans accents, tirets/apostrophes -> espaces, espaces compactés."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()
    return NAME_ALIASES.get(value, value)


def domain_key(value):
    """Clé de domaine comparable aux préfixes de colonnes de production.

    "ac-nancy-metz.fr" -> "ac_nancy_metz_fr" ; "@ac-noumea.nc" -> "ac_noumea_nc".
    """
    return normalize_name(value).replace(" ", "_") if value else ""


def strip_academie_prefix(label):
    """Extrait le nom d'académie d'un libellé de production, ex.
    "Nombre d'utilisateurs de l'académie de Nancy-Metz" -> "nancy metz"."""
    match = re.search(r"academie (?:de la |de l |de |d )?(.+)$", normalize_name(label))
    return normalize_name(match.group(1)) if match else ""


def classify_domain(domain, locations_by_key, locations_by_name=None, label=""):
    """Retourne (catégorie, location ou None, raison).

    `locations_by_key` : {domain_key: location} ; `locations_by_name` :
    {normalized_name: [locations]} pour le repli sur le libellé.
    """
    if domain in NON_TERRITORIAL:
        return "national", None, "domaine national ou opérateur"
    if domain in AMBIGUOUS_REGIONAL:
        return "regional", None, "région académique couvrant plusieurs académies"
    if domain in locations_by_key:
        return "academie", locations_by_key[domain], "correspondance exacte du domaine"
    if locations_by_name and label:
        candidates = locations_by_name.get(strip_academie_prefix(label), [])
        if len(candidates) == 1:
            return "academie", candidates[0], "correspondance par nom normalisé"
        if len(candidates) > 1:
            return "non_apparie", None, "correspondance ambiguë : plusieurs académies"
    return "non_apparie", None, "aucune académie correspondante"


# Libellés d'affichage : la bêta fournit des noms sans accents pour les vice-rectorats.
DISPLAY_NAMES = {"noumea": "Nouvelle-Calédonie", "polynesie": "Polynésie française"}
VICE_RECTORATS = {"noumea", "polynesie"}


def display_name(name):
    return DISPLAY_NAMES.get(normalize_name(name), name)


def full_name(name):
    """« Académie de Lille », « Académie d'Amiens », « Vice-rectorat de Polynésie française »."""
    label = display_name(name)
    if normalize_name(name) in VICE_RECTORATS:
        return f"Vice-rectorat de {label}"
    first = normalize_name(label)[:1]
    return f"Académie d'{label}" if first in "aeiouy" else f"Académie de {label}"
