"""Planificateur minimal : attend le prochain créneau, puis lance la synchronisation.

Tourne dans le même conteneur que le serveur web (la base SQLite est sur un
volume qui ne peut pas être partagé avec un autre service). Avec cron, ce
processus est inutile : voir le README.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from assistant_dashboard.services.schedule import next_slot, seconds_until
from assistant_dashboard.services.sync import run_scheduled_sync

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Vérifie chaque jour la publication du dataset de production (04:00, puis reprises espacées)."

    def handle(self, *args, **options):
        slot = None
        while True:
            now = timezone.localtime()
            # Jamais deux fois le même créneau, même si le réveil a quelques millisecondes d'avance.
            slot = next_slot(max(now, slot) if slot else now)
            logger.info("Prochaine vérification à %s", f"{slot:%d/%m/%Y %H:%M}")
            connection.close()  # pas de connexion ouverte pendant l'attente
            time.sleep(seconds_until(slot, now))
            try:
                run = run_scheduled_sync()
                logger.info("Vérification : %s", run.get_status_display() if run else "déjà à jour")
            except Exception:  # le planificateur ne doit jamais s'arrêter
                logger.exception("Échec de la vérification planifiée")
