from datetime import date, datetime, time, timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from assistant_dashboard.models import BetaLocation, ProductionObservation
from assistant_dashboard.services.sync import PARIS


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
        self.assertContains(r, 'class="kpi-primary metric"', count=1)  # un seul KPI piloté : la diffusion
        self.assertContains(r, 'class="kpi-small metric"', count=3)
        self.assertContains(r, "77")  # 7 × (10 + 1) nouveaux comptes, Lyon + Pix
        self.assertContains(r, "Données au")

    def test_headline_and_self_explanatory_secondary_kpis(self):
        seed()
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertContains(r, "Diffusion stable")
        self.assertContains(r, "KPI principal")
        self.assertContains(r, "Indicateurs secondaires")
        self.assertContains(r, "Parc total")
        self.assertContains(r, "depuis le lancement")
        self.assertContains(r, "messages pour 100 comptes")
        for name in ("Diffusion", "Activité", "Intensité", "Parc total"):  # mêmes noms partout
            self.assertContains(r, f'class="metric-name">{name}')

    def test_piloting_sections_in_order(self):
        seed()
        html = self.client.get(reverse("assistant_dashboard:dashboard")).content.decode()
        titles = ["KPI principal", "Indicateurs secondaires", "Diagnostic territorial",
                  "Actions recommandées", "Évolution historique"]
        positions = [html.index(t) for t in titles]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(html.count('<article class="reco'), 3)
        # Chaque section porte son étape, dans l'ordre : Détecter, Expliquer, Agir.
        steps = [html.index(f'<span class="step-name">{name}</span>') for name in ("Détecter", "Expliquer", "Agir")]
        self.assertEqual(steps, sorted(steps))
        self.assertLess(steps[0], html.index("KPI principal"))
        self.assertLess(steps[1], html.index("Diagnostic territorial"))
        self.assertLess(steps[2], html.index("Actions recommandées"))

    def test_cards_stay_short(self):
        seed()
        html = self.client.get(reverse("assistant_dashboard:dashboard")).content.decode()
        main = html[html.index("<main"):html.index("</main>")]
        self.assertNotIn("kpi-purpose", main)  # les nuances passent en infobulle
        self.assertNotIn("Analyse à mener", main)

    def test_recommendation_status(self):
        from assistant_dashboard.views import reco_status
        kpis = {"new_users_change_pct": 0.2, "activity_change_pct": -0.1}
        def sig(kind, metric, level, severity, **values):
            return {"kind": kind, "metric": metric, "level": level, "severity": severity, **values}
        signals = [
            {"id": 1, "name": "Bordeaux", "signals": [
                sig("ecart_superieur", "new_users", "strong", 0.48, evolution=0.47, national=-0.01),
                sig("ecart_inferieur", "messages", "strong", 0.40, evolution=-0.35, national=0.05)]},
            {"id": 6, "name": "Grenoble", "signals": [
                sig("ecart_superieur", "new_users", "strong", 2.16, evolution=2.15, national=-0.01)]},
            {"id": 2, "name": "Nancy-Metz", "signals": [sig("ecart_inferieur", "new_users", "strong", 0.33)]},
            {"id": 3, "name": "Rennes", "signals": [
                sig("activation", "both", "strong", 0.25, evolution_users=0.09, evolution_intensity=-0.16)]},
            {"id": 7, "name": "Orléans-Tours", "signals": [
                sig("activation", "both", "watch", 0.27, evolution_users=0.27, evolution_intensity=0.0)]},
            # Signaux « à surveiller » : visibles dans le diagnostic, jamais dans les actions
            {"id": 4, "name": "Lyon", "signals": [sig("ecart_superieur", "new_users", "watch", 0.2)]},
            {"id": 5, "name": "Nantes", "signals": [sig("ecart_inferieur", "new_users", "watch", 0.18)]},
        ]
        status = reco_status(kpis, signals)
        names = lambda key: [a["name"] for a in status[key]]
        self.assertEqual(names("acceleration"), ["Grenoble", "Bordeaux"])  # le plus marqué en premier
        self.assertEqual(status["acceleration"][1]["evolution"], 0.47)       # le chiffre affiché sur la pastille
        self.assertEqual(names("slowdown"), ["Nancy-Metz"])
        self.assertEqual(names("activation"), ["Rennes"])
        self.assertEqual(status["activation"][0]["evolution_intensity"], -0.16)
        self.assertTrue(status["activation_national"])
        self.assertFalse(reco_status({"new_users_change_pct": 0.2, "activity_change_pct": 0.1}, [])["activation_national"])
        self.assertFalse(reco_status({"new_users_change_pct": 0.01, "activity_change_pct": -0.1}, [])["activation_national"])

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

    def test_stale_data_is_flagged_at_display_time(self):
        seed()  # dernier snapshot : 20/09/2026
        url = reverse("assistant_dashboard:dashboard")
        with mock.patch("assistant_dashboard.services.dashboard_data.timezone.localdate",
                        return_value=date(2026, 9, 21)):
            r = self.client.get(url)
            self.assertEqual(r.context["freshness"]["stale_days"], 1)  # avant la synchronisation du jour
            self.assertNotContains(r, "dernier fichier il y a")
        with mock.patch("assistant_dashboard.services.dashboard_data.timezone.localdate",
                        return_value=date(2026, 9, 27)):
            self.assertContains(self.client.get(url), "dernier fichier il y a 7 j")

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

    def test_invalid_scope_404(self):
        url = reverse("assistant_dashboard:api_timeseries")
        self.assertEqual(self.client.get(url, {"scope": "abc"}).status_code, 404)
        self.assertEqual(self.client.get(url, {"scope": "999"}).status_code, 404)

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
                if sig["kind"] != "activation":
                    self.assertEqual(sig["evolution"], loc[f"{sig['metric']}_change_pct"])
                    self.assertEqual(sig["national"], self.summary[f"{sig['metric']}_change_pct"])

    def test_academies_plus_offmap_equal_national(self):
        rows = self.payload["locations"] + self.payload["unlocated"]
        for key in ("new_users", "messages", "cumulative_users"):
            self.assertEqual(sum(r[key] or 0 for r in rows), self.summary[key], key)

    def test_invariant_holds_with_decrease_and_missing_domain(self):
        # Baisse de cumul chez Pix (flux ramené à 0) et Nancy-Metz absente du dernier snapshot.
        last = date(2026, 9, 20)
        ProductionObservation.objects.filter(domain="pix_fr", date=last).update(cumulative_users=5)
        ProductionObservation.objects.filter(domain="ac_nancy_metz_fr", date=last).delete()
        payload = self.client.get(reverse("assistant_dashboard:api_locations")).json()
        summary = self.client.get(reverse("assistant_dashboard:api_summary")).json()["kpis"]
        rows = payload["locations"] + payload["unlocated"]
        self.assertEqual({r["end_date"] for r in rows}, {summary["end_date"]})
        for key in ("new_users", "messages", "cumulative_users"):
            self.assertEqual(sum(r[key] or 0 for r in rows), summary[key], key)
        nancy = next(r for r in rows if r.get("name") == "Nancy-Metz")
        self.assertEqual(summary["cumulative_users"], 190 + 5 + nancy["cumulative_users"])

    def test_recommendation_lists_flagged_academies(self):
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        slowdown = r.context["reco"]["slowdown"]
        self.assertTrue(slowdown, "Nancy-Metz doit ralentir dans ce jeu de test")
        for a in slowdown:  # pastilles visibles, cliquables, avec le chiffre
            self.assertContains(r, f'data-academy="{a["id"]}"')
        self.assertContains(r, 'class="reco is-empty"')  # une carte sans académie est en retrait
        self.assertNotContains(r, 'class="reco-more"')    # repli seulement au-delà de 4 académies
