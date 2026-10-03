from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from assistant_dashboard.models import BetaLocation, ProductionObservation
from assistant_dashboard.services.sync import PARIS
from datetime import datetime, time


def seed():
    lyon = BetaLocation.objects.create(name="Lyon", normalized_name="lyon", email_domain="ac-lyon.fr",
                                       domain_key="ac_lyon_fr", latitude=45.7, longitude=4.8)
    for i in range(20):
        day = date(2026, 9, 1) + timedelta(i)
        ts = datetime.combine(day, time(3), PARIS)
        ProductionObservation.objects.create(date=day, domain="ac_lyon_fr", normalized_domain="ac_lyon_fr",
                                             label="Académie de Lyon", category="academie", location=lyon,
                                             cumulative_users=10 * i, cumulative_messages=100 * i, source_timestamp=ts)
        ProductionObservation.objects.create(date=day, domain="pix_fr", normalized_domain="pix_fr", label="pix.fr",
                                             category="national", cumulative_users=i, cumulative_messages=5 * i,
                                             source_timestamp=ts)
    return lyon


class DashboardPageTests(TestCase):
    def test_page_loads_with_empty_database(self):
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Aucune donnée.")

    def test_page_shows_kpis(self):
        seed()
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertEqual(r.status_code, 200)
        for kpi in ["new_users", "messages", "cumulative_users", "activity"]:
            self.assertContains(r, f'data-kpi="{kpi}"')
        self.assertContains(r, 'class="kpi kpi-main"', count=2)
        self.assertContains(r, "77")  # 7 × (10 + 1) nouveaux comptes, Lyon + Pix
        self.assertContains(r, "Données au")

    def test_headline_and_self_explanatory_secondary_kpis(self):
        seed()
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertContains(r, "Diffusion stable")
        self.assertContains(r, "Parc total de comptes")
        self.assertContains(r, "depuis le lancement")
        self.assertContains(r, "messages / 100 comptes")

    def test_map_has_intensity_view(self):
        seed()
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertContains(r, 'data-map-family="intensity"')
        self.assertContains(r, 'id="map-mode-group"')
        locations = self.client.get(reverse("assistant_dashboard:api_locations")).json()["locations"]
        self.assertTrue(all(l["activity_per_100_accounts"] is not None for l in locations))

    def test_right_panel_containers(self):
        seed()
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertContains(r, "Académies qui se démarquent")
        for element_id in ("signals", "location-signal-note", "academy-select"):
            self.assertContains(r, f'id="{element_id}"')
        self.assertNotContains(r, 'id="spark"')  # pas de doublon avec le graphique du bas
        national = self.client.get(reverse("assistant_dashboard:api_locations")).json()["national"]
        self.assertIsNotNone(national["activity_per_100_accounts"])
        lyon = BetaLocation.objects.get(name="Lyon")
        detail = self.client.get(reverse("assistant_dashboard:api_location", args=[lyon.pk])).json()
        self.assertEqual(detail["national"]["activity_per_100_accounts"], national["activity_per_100_accounts"])

    def test_secondary_content_behind_dialogs(self):
        seed()
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        for dialog in ["dialog-method", "dialog-quality", "dialog-offmap"]:
            self.assertContains(r, f'<dialog id="{dialog}"')
        html = r.content.decode()
        main = html[html.index("<main"):html.index("</main>")]
        self.assertNotIn("<table", main)  # pas de tableau hors carte dans le flux principal
        self.assertNotIn("Non mesuré", main)
        self.assertNotIn("—", html)  # pas de tiret cadratin dans l'interface

    def test_offmap_summary_total(self):
        seed()
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertEqual(r.context["unlocated"]["total_users"], 19)
        self.assertContains(r, "19 comptes hors académies")
        self.assertContains(r, "inclus dans le total national")

    def test_forbidden_wording_absent(self):
        seed()
        html = self.client.get(reverse("assistant_dashboard:dashboard")).content.decode().lower()
        for phrase in ["adoption", "engagement", "messages par utilisateur"]:
            self.assertNotIn(phrase, html)


class ApiTests(TestCase):
    def setUp(self):
        self.lyon = seed()

    def test_summary_is_national(self):
        k = self.client.get(reverse("assistant_dashboard:api_summary")).json()["kpis"]
        self.assertEqual(k["new_users"], 77)
        self.assertEqual(k["cumulative_users"], 190 + 19)

    def test_select_location(self):
        d = self.client.get(reverse("assistant_dashboard:api_location", args=[self.lyon.pk])).json()
        self.assertEqual(d["name"], "Lyon")
        self.assertEqual(d["kpis"]["new_users"], 70)
        self.assertEqual(d["kpis"]["previous_new_users"], 70)
        self.assertEqual(d["kpis"]["new_users_change_pct"], 0)
        self.assertTrue(d["chart"]["users"])

    def test_unknown_location_404(self):
        self.assertEqual(self.client.get(reverse("assistant_dashboard:api_location", args=[999])).status_code, 404)

    def test_locations_only_contain_matched_domains(self):
        d = self.client.get(reverse("assistant_dashboard:api_locations")).json()
        self.assertEqual([l["name"] for l in d["locations"]], ["Lyon"])
        self.assertEqual([u["domain"] for u in d["unlocated"]], ["pix_fr"])

    def test_timeseries_periods(self):
        url = reverse("assistant_dashboard:api_timeseries")
        national = self.client.get(url, {"scope": "national", "period": "all"}).json()
        self.assertEqual(len(national["users"]), 20 - 7)
        local = self.client.get(url, {"scope": self.lyon.pk, "period": "30j"}).json()
        self.assertEqual(local["users"][-1]["value"], 70)


class MapApiTests(TestCase):
    def setUp(self):
        self.lyon = seed()
        self.lyon.geo_shape = {"type": "Polygon", "coordinates": [[[4, 45], [5, 45], [5, 46], [4, 45]]]}
        self.lyon.save()

    def test_shapes_endpoint(self):
        r = self.client.get(reverse("assistant_dashboard:api_shapes"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("max-age", r["Cache-Control"])
        features = r.json()["features"]
        self.assertEqual([f["id"] for f in features], [self.lyon.pk])

    def test_locations_expose_signals_and_display_names(self):
        d = self.client.get(reverse("assistant_dashboard:api_locations")).json()
        self.assertIn("signals", d)
        self.assertEqual(d["locations"][0]["full_name"], "Académie de Lyon")
        self.assertIn("min_gap", d["signal_thresholds"])


class ConsistencyTests(TestCase):
    """Invariants : un même chiffre a la même valeur partout où il apparaît."""

    def setUp(self):
        lyon = seed()  # Lyon (académie) + Pix (national), flux réguliers
        # Une 2e académie dont l'activité décroche la dernière semaine -> signal
        nancy = BetaLocation.objects.create(name="Nancy-Metz", normalized_name="nancy metz",
                                            email_domain="ac-nancy-metz.fr", domain_key="ac_nancy_metz_fr")
        users = 0
        for i in range(20):
            day = date(2026, 9, 1) + timedelta(i)
            users += 10 if i < 13 else 2
            ProductionObservation.objects.create(
                date=day, domain="ac_nancy_metz_fr", normalized_domain="ac_nancy_metz_fr", label="Nancy-Metz",
                category="academie", location=nancy, cumulative_users=users, cumulative_messages=100 * i,
                source_timestamp=datetime.combine(day, time(3), PARIS))
        self.payload = self.client.get(reverse("assistant_dashboard:api_locations")).json()
        self.summary = self.client.get(reverse("assistant_dashboard:api_summary")).json()["kpis"]

    def test_signals_match_kpis_and_map(self):
        flagged = [l for l in self.payload["locations"] if l["signals"]]
        self.assertTrue(flagged, "le jeu de test doit produire au moins un signal")
        for loc in self.payload["locations"]:
            detail = self.client.get(reverse("assistant_dashboard:api_location", args=[loc["id"]])).json()
            for key in ("new_users", "messages", "new_users_change_pct", "messages_change_pct", "cumulative_users"):
                self.assertEqual(detail["kpis"][key], loc[key], f"{loc['name']} {key}")
            for sig in loc["signals"]:
                if sig["kind"] != "divergence":
                    self.assertEqual(sig["evolution"], loc[f"{sig['metric']}_change_pct"])
                    self.assertEqual(sig["national"], self.summary[f"{sig['metric']}_change_pct"])

    def test_academies_plus_offmap_equal_national(self):
        rows = self.payload["locations"] + self.payload["unlocated"]
        for key in ("new_users", "messages", "cumulative_users"):
            self.assertEqual(sum(r[key] or 0 for r in rows), self.summary[key], key)
