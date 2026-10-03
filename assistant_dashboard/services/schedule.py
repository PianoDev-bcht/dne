"""Calendrier des vérifications quotidiennes du dataset de production (fonctions pures).

Le fichier est publié vers 03:00. Première vérification à 04:00, puis des
reprises de plus en plus espacées tant que le fichier du jour n'est pas là.
Après 11:30, on attend le lendemain.
"""
from datetime import time, timedelta

SLOTS = (time(4, 0), time(4, 30), time(5, 30), time(7, 30), time(11, 30))  # Europe/Paris


def next_slot(now):
    """Prochaine vérification strictement après `now` (datetime en heure de Paris)."""
    for day in (now, now + timedelta(days=1)):
        for slot in SLOTS:
            candidate = day.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
            if candidate > now:
                return candidate


def seconds_until(slot, now):
    """Durée réelle jusqu'à `slot`.

    Passe par l'heure absolue : la soustraction de deux dates du même fuseau
    ignore le changement d'heure et se tromperait d'une heure ces nuits-là.
    """
    return max(slot.timestamp() - now.timestamp(), 0)
