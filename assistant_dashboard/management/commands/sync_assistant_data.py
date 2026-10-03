from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from assistant_dashboard.models import ImportRun
from assistant_dashboard.services.sync import run_scheduled_sync, run_sync


class Command(BaseCommand):
    help = "Synchronise les données Open Data de l'Assistant IA (bêta + production) dans la base locale."

    def add_arguments(self, parser):
        parser.add_argument("--force-beta", action="store_true", help="Réimporte la bêta même si elle est déjà présente.")
        parser.add_argument("--if-new", action="store_true",
                            help="Vérification planifiée : n'importe que si un nouveau snapshot est publié.")

    def handle(self, *args, **options):
        if options["if_new"] and options["force_beta"]:
            raise CommandError("--if-new et --force-beta ne se combinent pas.")
        if options["if_new"]:
            run = run_scheduled_sync()
            if run is None:
                self.stdout.write("Déjà à jour : le snapshot du jour est en base, aucune requête.")
                return
        else:
            run = run_sync(force_beta=options["force_beta"])
        if run.status == ImportRun.STATUS_ERROR:
            raise CommandError(f"Synchronisation en échec : {run.error_message}")
        if run.status == ImportRun.STATUS_UNCHANGED:
            self.stdout.write("Pas de nouvelle publication : rien à importer.")
            return
        self.stdout.write(
            f"{run.get_status_display()} — récupérés : {run.number_of_records_fetched}, "
            f"créés : {run.number_of_records_created}, mis à jour : {run.number_of_records_updated}"
        )
        for kind, n in sorted(Counter(a["type"] for a in run.anomalies).items()):
            self.stdout.write(self.style.WARNING(f"  anomalie {kind} : {n}"))
