from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from assistant_dashboard.models import ImportRun
from assistant_dashboard.services.sync import run_sync


class Command(BaseCommand):
    help = "Synchronise les données Open Data de l'Assistant IA (bêta + production) dans la base locale."

    def add_arguments(self, parser):
        parser.add_argument("--force-beta", action="store_true", help="Réimporte la bêta même si elle est déjà présente.")

    def handle(self, *args, **options):
        run = run_sync(force_beta=options["force_beta"])
        if run.status == ImportRun.STATUS_ERROR:
            raise CommandError(f"Synchronisation en échec : {run.error_message}")
        self.stdout.write(
            f"{run.get_status_display()} — récupérés : {run.number_of_records_fetched}, "
            f"créés : {run.number_of_records_created}, mis à jour : {run.number_of_records_updated}"
        )
        for kind, n in sorted(Counter(a["type"] for a in run.anomalies).items()):
            self.stdout.write(self.style.WARNING(f"  anomalie {kind} : {n}"))
