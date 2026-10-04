from datetime import date
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from django.test import TestCase

from assistant_dashboard.models import BetaLocation, ImportRun, ProductionObservation, SchoolHoliday
from assistant_dashboard.services import dashboard_data, education_api, metrics, sync
from assistant_dashboard.services.education_api import (BETA_DATASET, CALENDAR_DATASET, CONTOURS_DATASET,
                                                         EducationAPIError)

from .factories import (BETA_RECORDS, CALENDAR_RECORDS, CONTOUR_RECORDS, LABELS, production_record,
                        production_series)


def fake_api(production_records, latest=None):
    """Remplace les appels HTTP par des données en mémoire.

    `latest` : horodatage renvoyé par la vérification de publication (défaut : le
    plus récent des enregistrements).
    """
    if latest is None:
        latest = max((r.get("timestamp") or "" for r in production_records), default=None) or None
    def fetch_all(dataset, session=None, **kwargs):
        if dataset == BETA_DATASET:
            return BETA_RECORDS
        if dataset == CALENDAR_DATASET:
            return CALENDAR_RECORDS
        if dataset == CONTOURS_DATASET:
            return CONTOUR_RECORDS
        return production_records
    return mock.patch.multiple(
        "assistant_dashboard.services.education_api",
        fetch_all_records=mock.Mock(side_effect=fetch_all),
        fetch_field_labels=mock.Mock(return_value=LABELS),
        fetch_latest_timestamp=mock.Mock(return_value=latest),
    )


class SyncTests(TestCase):
    def test_creates_one_observation_per_date_and_domain(self):
        with fake_api(production_series(3)):
            run = sync.run_sync()
        self.assertEqual(BetaLocation.objects.count(), 2)
        self.assertEqual(ProductionObservation.objects.count(), 3 * 4)
        self.assertEqual(run.number_of_records_fetched, 3)
        self.assertEqual(run.number_of_records_created, 12)
        self.assertIn(run.status, [ImportRun.STATUS_SUCCESS, ImportRun.STATUS_WARNING])

    def test_rerun_is_idempotent(self):
        with fake_api(production_series(3)):
            sync.run_sync()
            run = sync.run_sync()
        self.assertEqual(ProductionObservation.objects.count(), 12)
        self.assertEqual(run.number_of_records_created, 0)
        self.assertEqual(run.number_of_records_updated, 12)
        self.assertEqual(run.status, ImportRun.STATUS_SUCCESS)  # rien de nouveau : pas d'alerte

    def test_duplicate_synchronization_changes_nothing(self):
        with fake_api(production_series(2)):
            sync.run_sync()
            before = metrics.compute_kpis(dashboard_data.national_series())
            sync.run_sync()
            sync.run_sync()
        self.assertEqual(ProductionObservation.objects.count(), 2 * 4)
        self.assertEqual(metrics.compute_kpis(dashboard_data.national_series()), before)

    def test_utilisateurs_suffix_and_classification(self):
        with fake_api(production_series(1)):
            sync.run_sync()
        pix = ProductionObservation.objects.get(domain="pix_fr")
        self.assertEqual(pix.cumulative_users, 2)
        self.assertEqual(pix.category, "national")
        self.assertIsNone(pix.location)
        lyon = ProductionObservation.objects.get(domain="ac_lyon_fr")
        self.assertEqual(lyon.location.name, "Lyon")
        self.assertEqual(lyon.label, "Académie de Lyon")
        self.assertEqual(ProductionObservation.objects.get(domain="region_academique_paca_fr").category, "regional")

    def test_api_unavailable_logs_error_and_keeps_data(self):
        with fake_api(production_series(2)):
            sync.run_sync()
        with mock.patch("assistant_dashboard.services.education_api.fetch_all_records",
                        side_effect=EducationAPIError("HTTP 503")):
            run = sync.run_sync()
        self.assertEqual(run.status, ImportRun.STATUS_ERROR)
        self.assertIn("503", run.error_message)
        self.assertEqual(ProductionObservation.objects.count(), 8)

    def test_two_snapshots_same_day_keep_latest(self):
        day = date(2026, 9, 1)
        records = [production_record(day, lyon=(10, 100)),
                   production_record(day, lyon=(12, 120), hour="14:00:00")]
        with fake_api(records):
            run = sync.run_sync()
        self.assertEqual(ProductionObservation.objects.get(domain="ac_lyon_fr").cumulative_users, 12)
        self.assertIn("doublon", {a["type"] for a in run.anomalies})

    def test_quality_anomalies_are_reported_without_failing(self):
        records = [
            production_record(date(2026, 9, 1), lyon=(10, 100)),
            production_record(date(2026, 9, 2), lyon=(9, 90)),   # baisse
            production_record(date(2026, 9, 4), lyon=(11, 110)),  # trou le 03/09
            {"timestamp": None, "cree_le": None, "ac_lyon_fr_users": 1, "ac_lyon_fr_messages": 1},
        ]
        missing_domain = production_record(date(2026, 9, 5), lyon=(12, 120))
        del missing_domain["pix_fr_utilisateurs"], missing_domain["pix_fr_messages"]
        records.append(missing_domain)
        with fake_api(records):
            run = sync.run_sync()
        types = {a["type"] for a in run.anomalies}
        self.assertEqual(run.status, ImportRun.STATUS_WARNING)
        self.assertTrue({"baisse_utilisateurs", "baisse_messages", "date_manquante", "domaine_absent"} <= types)

    def test_new_and_unmatched_domain_flagged(self):
        with fake_api(production_series(1)):
            sync.run_sync()
        record = production_record(date(2026, 9, 2))
        record.update({"ac_inconnue_fr_users": 1, "ac_inconnue_fr_messages": 2})
        with fake_api([record]):
            run = sync.run_sync()
        types = {(a["type"], a["domain"]) for a in run.anomalies}
        self.assertIn(("nouveau_domaine", "ac_inconnue_fr"), types)
        self.assertIn(("domaine_non_apparie", "ac_inconnue_fr"), types)
        self.assertEqual(ProductionObservation.objects.get(domain="ac_inconnue_fr").category, "non_apparie")

    def test_snapshot_date_is_paris_date(self):
        day, _ = sync.snapshot_date({"timestamp": "2026-09-01T23:30:00+00:00"})
        self.assertEqual(day, date(2026, 9, 2))
        day, _ = sync.snapshot_date({"timestamp": None, "cree_le": "2026-09-01T03:00:00+00:00"})
        self.assertEqual(day, date(2026, 9, 1))


class ScheduledSyncTests(TestCase):
    """Vérification planifiée : on n'importe que si la source a publié un nouveau snapshot."""

    TODAY = date(2026, 9, 3)

    def setUp(self):
        with fake_api(production_series(2)):  # snapshots des 01/09 et 02/09
            sync.run_sync()

    def kpis(self):
        return metrics.compute_kpis(dashboard_data.national_series())

    def test_new_data_is_ingested(self):
        with fake_api(production_series(3)):
            run = sync.run_scheduled_sync(today=self.TODAY)
        self.assertEqual(run.status, ImportRun.STATUS_SUCCESS)  # données propres : aucune anomalie nouvelle
        self.assertEqual(run.number_of_records_created, 4)
        self.assertTrue(ProductionObservation.objects.filter(date=self.TODAY).exists())

    def test_no_new_data_is_a_normal_state(self):
        before = list(ProductionObservation.objects.values_list("pk", "updated_at"))
        with fake_api(production_series(2)):
            run = sync.run_scheduled_sync(today=self.TODAY)
            education_api.fetch_all_records.assert_not_called()  # rien n'est téléchargé
        self.assertEqual(run.status, ImportRun.STATUS_UNCHANGED)
        self.assertEqual(run.error_message, "")
        self.assertEqual(list(ProductionObservation.objects.values_list("pk", "updated_at")), before)

    def test_no_request_once_todays_snapshot_is_stored(self):
        runs = ImportRun.objects.count()
        with fake_api(production_series(2)):
            self.assertIsNone(sync.run_scheduled_sync(today=date(2026, 9, 2)))
            education_api.fetch_latest_timestamp.assert_not_called()
        self.assertEqual(ImportRun.objects.count(), runs)

    def test_source_unavailable_is_logged_and_keeps_data(self):
        before = self.kpis()
        with mock.patch("assistant_dashboard.services.education_api.fetch_latest_timestamp",
                        side_effect=EducationAPIError("HTTP 503")):
            run = sync.run_scheduled_sync(today=self.TODAY)
        self.assertEqual(run.status, ImportRun.STATUS_ERROR)
        self.assertIn("503", run.error_message)
        self.assertEqual(self.kpis(), before)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_malformed_timestamp_is_an_error_and_writes_nothing(self):
        bad = production_record(self.TODAY)
        bad["timestamp"] = "pas-une-date"
        with fake_api(production_series(2) + [bad], latest="2026-09-03T01:00:01+00:00"):
            run = sync.run_scheduled_sync(today=self.TODAY)
        self.assertEqual(run.status, ImportRun.STATUS_ERROR)
        self.assertEqual(ProductionObservation.objects.count(), 2 * 4)

    def test_empty_dataset_is_an_error_and_writes_nothing(self):
        with fake_api([], latest="2026-09-03T01:00:01+00:00"):
            run = sync.run_scheduled_sync(today=self.TODAY)
        self.assertEqual(run.status, ImportRun.STATUS_ERROR)
        self.assertIn("aucune observation", run.error_message)
        self.assertEqual(ProductionObservation.objects.count(), 2 * 4)

    def test_non_numeric_value_is_skipped_and_reported(self):
        bad = production_record(self.TODAY, lyon=("douze", 120))
        with fake_api(production_series(2) + [bad]):
            run = sync.run_scheduled_sync(today=self.TODAY)
        self.assertIn(("valeur_invalide", "ac_lyon_fr"), {(a["type"], a["domain"]) for a in run.anomalies})
        self.assertFalse(ProductionObservation.objects.filter(date=self.TODAY, domain="ac_lyon_fr").exists())
        self.assertTrue(ProductionObservation.objects.filter(date=self.TODAY, domain="pix_fr").exists())

    def test_out_of_range_value_skips_only_that_domain(self):
        bad = production_record(self.TODAY, lyon=(1e30, 120))
        with fake_api(production_series(2) + [bad]):
            run = sync.run_scheduled_sync(today=self.TODAY)
        self.assertNotEqual(run.status, ImportRun.STATUS_ERROR)
        self.assertIn(("valeur_invalide", "ac_lyon_fr"), {(a["type"], a["domain"]) for a in run.anomalies})
        self.assertTrue(ProductionObservation.objects.filter(date=self.TODAY, domain="pix_fr").exists())

    def test_future_dated_snapshot_is_rejected(self):
        # Sinon la base paraîtrait « à jour » et la source ne serait plus jamais interrogée.
        future = production_record(date(2099, 1, 1))
        with fake_api(production_series(3) + [future]):
            run = sync.run_scheduled_sync(today=self.TODAY)
        self.assertIn("date_invalide", {a["type"] for a in run.anomalies})
        self.assertEqual(ProductionObservation.latest_date(), self.TODAY)

    def test_anomaly_detail_is_bounded(self):
        bad = production_record(self.TODAY, lyon=("x" * 5000, 120))
        with fake_api(production_series(2) + [bad]):
            run = sync.run_scheduled_sync(today=self.TODAY)
        self.assertTrue(all(len(a["detail"]) <= 120 for a in run.anomalies))

    def test_command_refuses_if_new_with_force_beta(self):
        with self.assertRaises(CommandError):
            call_command("sync_assistant_data", "--if-new", "--force-beta")

    def test_scheduler_loop_survives_a_failure(self):
        sleeps = mock.Mock(side_effect=[None, KeyboardInterrupt])  # deux tours, puis arrêt
        with mock.patch("assistant_dashboard.management.commands.run_sync_scheduler.time.sleep", sleeps), \
                mock.patch("assistant_dashboard.management.commands.run_sync_scheduler.run_scheduled_sync",
                           side_effect=RuntimeError("boom")) as check, \
                self.assertLogs("assistant_dashboard", level="ERROR"), self.assertRaises(KeyboardInterrupt):
            call_command("run_sync_scheduler")
        self.assertEqual(check.call_count, 1)
        self.assertEqual(sleeps.call_count, 2)  # l'échec n'a pas arrêté la boucle

    def test_command_if_new(self):
        with fake_api(production_series(2)), mock.patch(
                "assistant_dashboard.services.sync.timezone.localdate", return_value=self.TODAY):
            call_command("sync_assistant_data", "--if-new", stdout=mock.Mock())
        self.assertEqual(ImportRun.objects.first().status, ImportRun.STATUS_UNCHANGED)


class ContoursAndQualityTests(TestCase):
    def test_contours_matched_by_name_vice_rectorat_exempt(self):
        with fake_api(production_series(2)):
            run = sync.run_sync()
        lyon = BetaLocation.objects.get(name="Lyon")
        self.assertEqual(lyon.geo_shape["type"], "Polygon")
        self.assertEqual(lyon.geo_shape["coordinates"][0][0], [4.12, 45.68])  # arrondi à 2 décimales
        self.assertIsNone(BetaLocation.objects.get(name="Polynesie").geo_shape)
        types = {a["type"] for a in run.anomalies}
        self.assertIn("contour_non_apparie", types)  # « Atlantide » n'existe pas
        self.assertNotIn("contour_manquant", types)  # la Polynésie n'a pas de contour attendu

    def test_known_anomalies_do_not_raise_status_again(self):
        records = [production_record(date(2026, 9, 1)), production_record(date(2026, 9, 3))]  # trou le 02/09
        with fake_api(records):
            first = sync.run_sync()
            second = sync.run_sync()
        gap = lambda run: [a for a in run.anomalies if a["type"] == "date_manquante"][0]
        self.assertTrue(gap(first)["new"])
        self.assertFalse(gap(second)["new"])
        self.assertEqual(second.status, ImportRun.STATUS_SUCCESS)


class HolidaySyncTests(TestCase):
    def setUp(self):
        sync.sync_beta(records=BETA_RECORDS)
        self.today = date(2026, 10, 4)

    def test_import_vacations_only_with_paris_dates(self):
        sync.sync_holidays(records=CALENDAR_RECORDS, today=self.today)
        lyon = SchoolHoliday.objects.filter(location__name="Lyon")
        toussaint = lyon.get(description="Vacances de la Toussaint")
        # `end_date` = jour de reprise (02/11) : dernier jour de vacances le 01/11.
        self.assertEqual((toussaint.start, toussaint.end), (date(2026, 10, 17), date(2026, 11, 1)))
        self.assertEqual(toussaint.zone, "Zone A")
        pont = lyon.get(description="Pont de l'Ascension")
        self.assertEqual(pont.start, pont.end)                    # pont d'un jour
        self.assertFalse(lyon.filter(description__icontains="rentrée").exists())  # lignes enseignants ignorées

    def test_idempotent_and_unmatched_locations(self):
        anomalies = sync.sync_holidays(records=CALENDAR_RECORDS, today=self.today)
        count = SchoolHoliday.objects.count()
        sync.sync_holidays(records=CALENDAR_RECORDS, today=self.today)
        self.assertEqual(SchoolHoliday.objects.count(), count)
        kinds = {(a["type"], a["detail"]) for a in anomalies}
        self.assertIn(("calendrier_non_apparie", "lieu « Atlantide » sans académie"), kinds)
        self.assertFalse(any("Miquelon" in d for _, d in kinds))   # pas une académie : ignoré sans anomalie
        self.assertTrue(any(t == "calendrier_manquant" for t, _ in kinds))  # académies sans vacances importées

    def test_school_years(self):
        self.assertEqual(sync.school_years(date(2026, 10, 4)), ["2025-2026", "2026-2027"])
        self.assertEqual(sync.school_years(date(2027, 3, 1)), ["2025-2026", "2026-2027"])
