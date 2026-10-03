"""Données synthétiques pour une démonstration hors ligne.

Refuse de s'exécuter si la base contient déjà des observations : on ne mélange
jamais données réelles et données de démonstration. Pour une démo à côté des
données réelles : SQLITE_PATH=demo.sqlite3 python manage.py migrate && ... load_demo_data
"""
import math
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from assistant_dashboard.models import BetaLocation, ImportRun, ProductionObservation
from assistant_dashboard.services.matching import normalize_name

DEMO_LOCATIONS = [
    # (nom, domaine bêta, lat, lon)
    ("Académie Alpha", "ac-alpha.fr", 48.85, 2.35),
    ("Académie Bêta", "ac-beta.fr", 45.76, 4.83),
    ("Académie Gamma", "ac-gamma.fr", 43.60, 1.44),
    ("Académie Delta", "ac-delta.fr", 47.22, -1.55),
    ("Académie Outre-mer", "ac-outremer.fr", -21.11, 55.53),
]
DEMO_NATIONAL = [("operateur_demo_fr", "Opérateur national (démo)")]


def generate_points(days=100, start=date(2026, 6, 23), seed=42, scale=1.0):
    """Série cumulée synthétique : rythme hebdomadaire et creux estival."""
    rng = random.Random(seed)
    users = messages = 0
    points = []
    for i in range(days):
        day = start + timedelta(days=i)
        summer = 0.25 if day.month in (7, 8) else 1.0
        weekday = 0.3 if day.weekday() >= 5 else 1.0
        users += int(scale * summer * weekday * rng.uniform(3, 12))
        messages += int(scale * summer * weekday * rng.uniform(60, 200) * (1 + math.log1p(i) / 3))
        points.append((day, users, messages))
    return points


class Command(BaseCommand):
    help = "Charge des données de démonstration synthétiques (base vide uniquement)."

    def handle(self, *args, **options):
        if ProductionObservation.objects.exists():
            raise CommandError("La base contient déjà des observations : utiliser une base dédiée (SQLITE_PATH).")
        paris = ZoneInfo("Europe/Paris")
        run = ImportRun.objects.create()
        created = 0
        for i, (name, email, lat, lon) in enumerate(DEMO_LOCATIONS):
            key = email.replace("-", "_").replace(".", "_")
            loc = BetaLocation.objects.create(
                name=name, normalized_name=normalize_name(name), email_domain=email, domain_key=key,
                latitude=lat, longitude=lon)
            for day, u, m in generate_points(seed=i, scale=1 + i / 2):
                ProductionObservation.objects.create(
                    date=day, domain=key, normalized_domain=key, label=name, category=ProductionObservation.CATEGORY_ACADEMIE,
                    cumulative_users=u, cumulative_messages=m, location=loc,
                    source_timestamp=datetime.combine(day, time(3), paris))
                created += 1
        for key, label in DEMO_NATIONAL:
            for day, u, m in generate_points(seed=99, scale=3):
                ProductionObservation.objects.create(
                    date=day, domain=key, normalized_domain=key, label=label, category=ProductionObservation.CATEGORY_NATIONAL,
                    cumulative_users=u, cumulative_messages=m,
                    source_timestamp=datetime.combine(day, time(3), paris))
                created += 1
        run.status = ImportRun.STATUS_SUCCESS
        run.number_of_records_created = created
        run.finished_at = timezone.now()
        run.error_message = "Données de démonstration synthétiques"
        run.save()
        self.stdout.write(self.style.SUCCESS(f"{created} observations de démonstration créées."))
