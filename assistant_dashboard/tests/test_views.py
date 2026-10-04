from datetime import date, datetime, time, timedelta
from functools import partial
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from assistant_dashboard.models import BetaLocation, ImportRun, ProductionObservation
from assistant_dashboard.services import dashboard_data
from assistant_dashboard.services.sync import PARIS


def at(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=PARIS)


def frozen(now):
    """Fige l'horloge de `freshness` (page et API) à `now`."""
    return mock.patch.object(dashboard_data, "freshness", partial(dashboard_data.freshness, now=now))


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
        self.assertContains(r, "Indicateur principal")
        self.assertContains(r, "Indicateurs secondaires")
        self.assertContains(r, "Parc total")
        self.assertContains(r, "depuis le lancement")
        self.assertContains(r, "messages pour 100 comptes")
        for name in ("Diffusion", "Activité", "Intensité", "Parc total"):  # mêmes noms partout
            self.assertContains(r, f'class="metric-name">{name}')

    def test_piloting_sections_in_order(self):
        seed()
        html = self.client.get(reverse("assistant_dashboard:dashboard")).content.decode()
        titles = ["Indicateur principal", "Indicateurs secondaires", "Diagnostic territorial",
                  "Actions recommandées", "Évolution historique"]
        positions = [html.index(t) for t in titles]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(html.count('<article class="reco'), 3)
        # Chaque section porte son étape, dans l'ordre : Détecter, Expliquer, Agir.
        steps = [html.index(f'<span class="step-name">{name}</span>') for name in ("Détecter", "Expliquer", "Agir")]
        self.assertEqual(steps, sorted(steps))
        self.assertLess(steps[0], html.index("Indicateur principal"))
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
            return {"kind": kind, "metric": metric, "level": level, "severity": severity,
                    "display": {"rel14": None, "rel7": None}, **values}
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
            # Signaux « à suivre » : visibles dans le diagnostic, jamais dans les actions
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
        self.assertNotIn("activation_national", status)  # les actions ne concernent que des académies

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
        self.assertContains(r, "19 comptes non rattachés à une académie")
        self.assertContains(r, "inclus dans le total national")

    def test_stale_data_is_flagged_at_display_time(self):
        seed()  # dernier snapshot : 20/09/2026
        url = reverse("assistant_dashboard:dashboard")
        with frozen(at(21, 3)):
            r = self.client.get(url)
            self.assertEqual(r.context["freshness"]["stale_days"], 1)  # avant la synchronisation du jour
            self.assertNotContains(r, "dernier fichier il y a")
        with frozen(at(27, 12)):
            self.assertContains(self.client.get(url), "dernier fichier publié il y a 7 jours")

    def test_sync_status_line(self):
        seed()  # dernier snapshot : 20/09/2026, données au 19/09
        ok = ImportRun.objects.create(status=ImportRun.STATUS_WARNING, anomalies=[
            {"type": "date_manquante", "domain": "", "date": "", "detail": "trou connu", "new": True}])
        ImportRun.objects.filter(pk=ok.pk).update(finished_at=at(20, 4, 3))
        url = reverse("assistant_dashboard:dashboard")

        def page(now):
            with frozen(now):
                return self.client.get(url)

        r = page(at(20, 9))
        self.assertEqual(r.context["freshness"]["sync_state"], "up_to_date")
        self.assertContains(r, "dernière synchronisation le 20/09 à 04:03")
        # Le lendemain, avant publication : état normal, sans alerte, avec la prochaine vérification.
        ImportRun.objects.create(status=ImportRun.STATUS_UNCHANGED)
        r = page(at(21, 5))
        self.assertEqual(r.context["freshness"]["sync_state"], "waiting_for_publication")
        self.assertContains(r, "prochaine vérification prévue à 05:30")
        self.assertNotContains(r, "pas de fichier")
        self.assertEqual(r.context["freshness"]["anomaly_count"], 1)  # les anomalies connues restent affichées
        # Dernier créneau passé sans fichier : la journée sans publication est signalée.
        r = page(at(21, 12))
        self.assertEqual(r.context["freshness"]["sync_state"], "no_publication_today")
        self.assertContains(r, "fichier du jour non publié · prochaine vérification demain à 04:00")
        self.assertContains(r, "⚠ fichier du jour non publié")

    def test_error_detail_is_not_published(self):
        seed()
        ImportRun.objects.create(status=ImportRun.STATUS_ERROR, finished_at=at(21, 4),
                                 error_message="OperationalError: /data/db.sqlite3 is locked")
        with frozen(at(21, 5)):
            r = self.client.get(reverse("assistant_dashboard:dashboard"))
            summary = self.client.get(reverse("assistant_dashboard:api_summary"))
        self.assertEqual(r.context["freshness"]["sync_state"], "error")
        self.assertContains(r, "source indisponible ou réponse illisible")
        self.assertContains(r, "échec de synchronisation")
        for response in (r, summary):
            self.assertNotContains(response, "OperationalError")
            self.assertNotContains(response, "db.sqlite3")

    def test_current_data_wins_over_an_older_error(self):
        seed()
        ImportRun.objects.create(status=ImportRun.STATUS_ERROR, finished_at=at(20, 4), error_message="HTTP 503")
        with frozen(at(20, 9)):  # le snapshot du jour est en base
            r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertEqual(r.context["freshness"]["sync_state"], "up_to_date")
        self.assertNotContains(r, "échec de synchronisation")

    def test_sync_in_progress(self):
        seed()
        ImportRun.objects.create()  # statut « running », démarré à l'instant
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertEqual(r.context["freshness"]["sync_state"], "syncing")
        self.assertContains(r, "synchronisation en cours")

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
        self.assertEqual(len(national["users"]), 20 - 1)  # une valeur par jour, sauf le premier
        self.assertEqual(national["users"][-1]["value"], 10 + 1)  # Lyon + Pix
        local = self.client.get(url, {"scope": self.lyon.pk, "period": "30j"}).json()
        self.assertEqual(local["users"][-1]["value"], 10)


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
        self.assertEqual(d["signal_thresholds"]["action_days"], 14)
        self.assertIn("action_status", d)


def seed_long(days=32):
    """Jeu sur 32 jours (assez pour comparer 14 jours aux 14 précédents) :
    Lyon, Paris et Pix réguliers, un gros domaine national stable, et Nancy-Metz dont la
    diffusion ralentit nettement sur les 14 derniers jours (15, puis 12 et 8 comptes/jour)."""
    lyon = BetaLocation.objects.create(name="Lyon", normalized_name="lyon", email_domain="ac-lyon.fr",
                                       domain_key="ac_lyon_fr", latitude=45.7, longitude=4.8)
    nancy = BetaLocation.objects.create(name="Nancy-Metz", normalized_name="nancy metz",
                                        email_domain="ac-nancy-metz.fr", domain_key="ac_nancy_metz_fr")
    # Grande académie stable : elle porte la tendance de l'ensemble des académies (référence des signaux).
    paris = BetaLocation.objects.create(name="Paris", normalized_name="paris", email_domain="ac-paris.fr",
                                        domain_key="ac_paris_fr")
    nancy_users = 0
    for i in range(days):
        day = date(2026, 9, 1) + timedelta(i)
        ts = datetime.combine(day, time(3), PARIS)
        nancy_users += 15 if i < days - 14 else (12 if i < days - 7 else 8)  # ralentit, et encore la dernière semaine
        rows = [("ac_lyon_fr", "academie", lyon, 10 * i, 100 * i),
                ("pix_fr", "national", None, i, 5 * i),
                ("autres", "national", None, 50 * i, 500 * i),
                ("ac_paris_fr", "academie", paris, 50 * i, 500 * i),
                ("ac_nancy_metz_fr", "academie", nancy, nancy_users, 100 * i)]
        for domain, category, loc, users, messages in rows:
            ProductionObservation.objects.create(date=day, domain=domain, normalized_domain=domain, label=domain,
                                                 category=category, location=loc, cumulative_users=users,
                                                 cumulative_messages=messages, source_timestamp=ts)
    return lyon, nancy


class ConsistencyTests(TestCase):
    """Invariants : un même chiffre a la même valeur partout où il apparaît."""

    def setUp(self):
        self.lyon, self.nancy = seed_long()
        self.payload = self.client.get(reverse("assistant_dashboard:api_locations")).json()
        self.summary = self.client.get(reverse("assistant_dashboard:api_summary")).json()["kpis"]

    def test_signals_match_kpis_and_map(self):
        flagged = [l for l in self.payload["locations"] if l["signals"]]
        self.assertTrue(flagged, "le jeu de test doit produire au moins un signal")
        for loc in self.payload["locations"]:
            detail = self.client.get(reverse("assistant_dashboard:api_location", args=[loc["id"]])).json()
            for key in ("new_users", "messages", "new_users_change_pct", "messages_change_pct", "cumulative_users"):
                self.assertEqual(detail["kpis"][key], loc[key], f"{loc['name']} {key}")
            self.assertEqual(detail["signals"], loc["signals"])  # panneau et détail : mêmes signaux
            for sig in loc["signals"]:
                if sig["kind"] == "activation":
                    continue
                # Le signal porte les chiffres de sa fenêtre : 7 jours = KPI de la carte, 14 jours = détail 14 j.
                kpis = loc if sig["window"] == 7 else detail["kpis14"]
                self.assertEqual(sig["evolution"], kpis["new_users_change_pct"])
                self.assertEqual(sig["current"], kpis["new_users"])
                if sig["window"] == 14:
                    self.assertEqual(sig["display"]["rel14"], detail["relative_14"])  # même chiffre affiché partout

    def test_progressive_slowdown_triggers_diagnostiquer_on_14_days(self):
        nancy = next(l for l in self.payload["locations"] if l["name"] == "Nancy-Metz")
        sig = nancy["signals"][0]
        self.assertEqual((sig["kind"], sig["window"], sig["level"]), ("ecart_inferieur", 14, "strong"))
        self.assertEqual((sig["previous"], sig["current"]), (210, 140))
        self.assertLessEqual(sig["relative"], -0.20)
        self.assertTrue(self.payload["action_status"]["ok"])

    def test_academies_plus_offmap_equal_national(self):
        rows = self.payload["locations"] + self.payload["unlocated"]
        for key in ("new_users", "messages", "cumulative_users"):
            self.assertEqual(sum(r[key] or 0 for r in rows), self.summary[key], key)

    def test_invariant_holds_with_decrease_and_missing_domain(self):
        # Baisse de cumul chez Pix (flux ramené à 0) et Nancy-Metz absente du dernier snapshot.
        last = date(2026, 10, 2)
        ProductionObservation.objects.filter(domain="pix_fr", date=last).update(cumulative_users=5)
        ProductionObservation.objects.filter(domain="ac_nancy_metz_fr", date=last).delete()
        payload = self.client.get(reverse("assistant_dashboard:api_locations")).json()
        summary = self.client.get(reverse("assistant_dashboard:api_summary")).json()["kpis"]
        rows = payload["locations"] + payload["unlocated"]
        self.assertEqual({r["end_date"] for r in rows}, {summary["end_date"]})
        for key in ("new_users", "messages", "cumulative_users"):
            self.assertEqual(sum(r[key] or 0 for r in rows), summary[key], key)
        # Nancy-Metz incomplète : son ralentissement ne déclenche pas d'action, il reste à suivre avec sa raison.
        nancy = next(r for r in payload["locations"] if r["name"] == "Nancy-Metz")
        self.assertTrue(all(s["level"] == "watch" for s in nancy["signals"]))
        self.assertIn("incomplètes", nancy["signals"][0]["reason"])

    def test_recommendation_lists_flagged_academies(self):
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        slowdown = r.context["reco"]["slowdown"]
        self.assertTrue(slowdown, "Nancy-Metz doit ralentir dans ce jeu de test")
        for a in slowdown:  # pastilles visibles, cliquables, avec le chiffre
            self.assertContains(r, f'data-academy="{a["id"]}"')
        self.assertContains(r, 'class="reco is-empty"')  # une carte sans académie est en retrait
        self.assertNotContains(r, 'class="reco-more"')    # repli seulement au-delà de 4 académies

    def test_actions_list_only_academies(self):
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertContains(r, "02</span> Diagnostiquer")
        self.assertNotContains(r, "reco-chip is-national")
        html = r.content.decode()
        actions = html[html.index('id="actions"'):html.index('id="trend-title"')]
        self.assertNotIn(">France <", actions)

    def test_common_holidays_do_not_block(self):
        from assistant_dashboard.models import SchoolHoliday
        # Toussaint aux mêmes dates en zones A, B et C : neutralisée par la comparaison nationale.
        others = [BetaLocation.objects.create(name=n, normalized_name=n.lower(), email_domain=f"ac-{n.lower()}.fr",
                                              domain_key=f"ac_{n.lower()}_fr") for n in ("Rennes", "Lille")]
        for loc, zone in zip([self.nancy, *others], ("Zone B", "Zone A", "Zone C")):
            SchoolHoliday.objects.create(location=loc, description="Vacances de la Toussaint", zone=zone,
                                         start=date(2026, 9, 25), end=date(2026, 9, 27), school_year="2026-2027")
        payload = self.client.get(reverse("assistant_dashboard:api_locations")).json()
        nancy = next(l for l in payload["locations"] if l["name"] == "Nancy-Metz")
        self.assertEqual(nancy["signals"][0]["level"], "strong")

    def test_school_holidays_downgrade_action(self):
        from assistant_dashboard.models import SchoolHoliday
        # Vacances d'hiver propres à la zone B : bloquent l'action.
        SchoolHoliday.objects.create(location=self.nancy, description="Vacances d'Hiver", zone="Zone B",
                                     start=date(2026, 9, 25), end=date(2026, 9, 27), school_year="2026-2027")
        payload = self.client.get(reverse("assistant_dashboard:api_locations")).json()
        nancy = next(l for l in payload["locations"] if l["name"] == "Nancy-Metz")
        self.assertEqual(nancy["signals"][0]["level"], "watch")
        self.assertIn("hiver", nancy["signals"][0]["reason"])
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertEqual(r.context["reco"]["slowdown"], [])

    def test_missing_days_add_note_but_keep_actions(self):
        # La comparaison couvre le 04/09 au 01/10. Aucun fichier publié le 04/09 (activité du 03/09) :
        # le rattrapage tombe dans la période précédente. Règle simple : une note, pas de blocage.
        ProductionObservation.objects.filter(date=date(2026, 9, 4)).delete()
        payload = self.client.get(reverse("assistant_dashboard:api_locations")).json()
        self.assertTrue(payload["action_status"]["ok"])
        self.assertIn("3 septembre", payload["action_status"]["note"])
        nancy = next(l for l in payload["locations"] if l["name"] == "Nancy-Metz")
        self.assertEqual(nancy["signals"][0]["level"], "strong")  # jour manquant global : pas « données incomplètes »
        r = self.client.get(reverse("assistant_dashboard:dashboard"))
        self.assertContains(r, "comparaisons sur quatorze jours sont donc approximatives")
        self.assertEqual([a["name"] for a in r.context["reco"]["slowdown"]], ["Nancy-Metz"])


class GuardsAndReferenceTests(TestCase):
    """Référence des académies, garde-fous de données et de calendrier, note d'approximation."""

    def setUp(self):
        self.lyon, self.nancy = seed_long()

    def ctx(self):
        return dashboard_data.signal_context()

    def test_reference_excludes_national_domains(self):
        ctx = self.ctx()
        # Pix et « autres » (nationaux) ne comptent que dans la France entière, pas dans la référence.
        self.assertNotEqual(ctx["reference14"]["new_users"], ctx["national7"]["new_users"])
        academies = sum(r["new_users"] for r in self.client.get(
            reverse("assistant_dashboard:api_locations")).json()["locations"])
        self.assertEqual(ctx["reference7"]["new_users"], academies)

    def test_frozen_day_blocks_actions(self):
        # Cumul de Nancy-Metz figé un mardi (aucun compte ni message) : données incomplètes.
        frozen = date(2026, 9, 22)
        previous = ProductionObservation.objects.get(domain="ac_nancy_metz_fr", date=frozen - timedelta(1))
        ProductionObservation.objects.filter(domain="ac_nancy_metz_fr", date=frozen).update(
            cumulative_users=previous.cumulative_users, cumulative_messages=previous.cumulative_messages)
        domains = dashboard_data._domain_series(ProductionObservation.objects.filter(location=self.nancy))
        series = dashboard_data.metrics.aggregate_series(domains.values())
        ctx = self.ctx()
        start = dashboard_data.metrics.comparison_start(ctx["end"], 14)
        self.assertTrue(dashboard_data.has_frozen_day(series, start, ctx["end"], ctx["missing"]))
        self.assertEqual(dashboard_data.action_block_reason(self.nancy, domains, ctx), "données de l’académie incomplètes")

    def test_domain_disappearance_only_counts_inside_the_window(self):
        ctx = self.ctx()
        domains = dashboard_data._domain_series(ProductionObservation.objects.filter(location=self.nancy))
        start = dashboard_data.metrics.comparison_start(ctx["end"], 14)
        ctx["last_seen"]["ac_nancy_metz_fr"] = start - timedelta(days=10)  # disparu bien avant la période
        self.assertIsNone(dashboard_data.action_block_reason(self.nancy, domains, ctx))
        ctx["last_seen"]["ac_nancy_metz_fr"] = start + timedelta(days=3)   # disparu pendant la période
        self.assertEqual(dashboard_data.action_block_reason(self.nancy, domains, ctx), "données de l’académie incomplètes")

    def test_domain_starting_on_first_day_is_a_perimeter_change(self):
        ctx = self.ctx()
        start = dashboard_data.metrics.comparison_start(ctx["end"], 14)
        series = [{"date": start + timedelta(days=i), "new_users": 1, "daily_messages": 1,
                   "is_gap": False, "is_decrease": False} for i in range(28)]
        self.assertEqual(dashboard_data.action_block_reason(self.nancy, {"nouveau_fr": series}, ctx),
                         "modification du périmètre des données")

    def test_common_holiday_tolerates_a_few_days(self):
        from assistant_dashboard.models import SchoolHoliday
        locs = [BetaLocation.objects.create(name=n, normalized_name=n.lower(), email_domain=f"ac-{n.lower()}.fr",
                                            domain_key=f"ac_{n.lower()}_fr") for n in ("Rennes", "Lille", "Creteil", "Corse")]
        for loc, zone in zip(locs, ("Zone A", "Zone B", "Zone C")):
            SchoolHoliday.objects.create(location=loc, description="Vacances d'Été", zone=zone,
                                         start=date(2026, 7, 4), end=date(2026, 8, 31), school_year="2025-2026")
        corse = SchoolHoliday.objects.create(location=locs[3], description="Vacances d'Été", zone="Corse",
                                             start=date(2026, 7, 4), end=date(2026, 9, 2), school_year="2025-2026")
        self.assertTrue(dashboard_data.is_common_holiday(corse))  # deux jours d'écart : vacances communes
        winter = SchoolHoliday.objects.create(location=locs[0], description="Vacances d'Hiver", zone="Zone A",
                                              start=date(2027, 2, 13), end=date(2027, 2, 28), school_year="2026-2027")
        self.assertFalse(dashboard_data.is_common_holiday(winter))

    def test_note_uses_file_and_activity_dates(self):
        end = date(2026, 10, 2)
        missing = {date(2026, 9, d) for d in range(4, 9)}
        note = dashboard_data.approximation_note(missing, end)
        self.assertEqual(note["until"], date(2026, 10, 6))  # dernier jour approximatif
        self.assertIn("Les fichiers du 5 au 9 septembre n’ont pas été publiés", note["text"])
        self.assertIn("l’activité du 4 au 8 septembre est comptée le 9 septembre", note["text"])
        self.assertIn("Jusqu’au 6 octobre", note["text"])
        self.assertIsNone(dashboard_data.approximation_note(missing, date(2026, 10, 7)))

    def test_long_dates(self):
        self.assertEqual(dashboard_data.long_date(date(2026, 10, 1)), "1er octobre 2026")
        self.assertEqual(dashboard_data.date_range(date(2026, 10, 1), date(2026, 10, 3)), "du 1er au 3 octobre")
        self.assertEqual(dashboard_data.date_range(date(2026, 9, 30), date(2026, 10, 2)), "du 30 septembre au 2 octobre")
        self.assertEqual(dashboard_data.date_range(date(2026, 9, 4), date(2026, 9, 4)), "le 4 septembre")
