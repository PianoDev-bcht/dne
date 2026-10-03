from datetime import date, timedelta

from django.test import SimpleTestCase

from assistant_dashboard.services import metrics

D0 = date(2026, 9, 1)


def linear(days, users_per_day=10, messages_per_day=100, start_users=0, start_messages=0):
    return [(D0 + timedelta(i), start_users + users_per_day * i, start_messages + messages_per_day * i)
            for i in range(days)]


class DailySeriesTests(SimpleTestCase):
    def test_flows_are_differences_of_cumulative_counts(self):
        s = metrics.build_daily_series([(D0, 10, 100), (D0 + timedelta(1), 15, 130)])
        self.assertIsNone(s[0]["new_users"])
        self.assertEqual((s[1]["new_users"], s[1]["daily_messages"]), (5, 30))

    def test_missing_days_are_forward_filled_and_flagged(self):
        s = metrics.build_daily_series([(D0, 10, 100), (D0 + timedelta(3), 40, 400)])
        self.assertEqual(len(s), 4)
        self.assertEqual([p["is_gap"] for p in s], [False, True, True, False])
        self.assertEqual([p["new_users"] for p in s[1:]], [0, 0, 30])

    def test_decrease_is_kept_and_flagged(self):
        s = metrics.build_daily_series([(D0, 10, 100), (D0 + timedelta(1), 8, 90)])
        self.assertEqual(s[1]["new_users"], -2)
        self.assertTrue(s[1]["is_decrease"])

    def test_series_extended_to_end_keeps_last_cumulative(self):
        # Domaine absent des derniers snapshots : cumul reporté, flux nul, sans marquer de trou.
        s = metrics.build_daily_series([(D0, 10, 100), (D0 + timedelta(1), 15, 130)], end=D0 + timedelta(3))
        self.assertEqual([p["cumulative_users"] for p in s], [10, 15, 15, 15])
        self.assertEqual([p["new_users"] for p in s[2:]], [0, 0])
        self.assertFalse(any(p["is_gap"] for p in s))

    def test_empty(self):
        self.assertEqual(metrics.build_daily_series([]), [])
        self.assertIsNone(metrics.compute_kpis([])["new_users"])


class WindowTests(SimpleTestCase):
    def test_rolling_7_days_and_previous_period(self):
        # 15 jours : 14 flux de 10 comptes ; dernière semaine plus active
        pts = linear(8) + [(D0 + timedelta(8 + i), 70 + 20 * (i + 1), 700 + 50 * (i + 1)) for i in range(7)]
        k = metrics.compute_kpis(metrics.build_daily_series(pts))
        self.assertEqual(k["new_users"], 140)
        self.assertEqual(k["previous_new_users"], 70)
        self.assertEqual(k["new_users_change_abs"], 70)
        self.assertAlmostEqual(k["new_users_change_pct"], 1.0)
        self.assertEqual(k["messages"], 350)
        self.assertEqual(k["previous_messages"], 700)
        self.assertAlmostEqual(k["messages_change_pct"], -0.5)
        self.assertEqual(k["cumulative_users"], 210)
        # parc moyen sur la fenêtre (jours 8 à 14) : (90 + 110 + … + 210) / 7 = 150
        self.assertAlmostEqual(k["activity_per_100_accounts"], 100 * 350 / 150)

    def test_intensity_compared_to_previous_7_days(self):
        # 15 jours, 10 comptes et 100 messages par jour
        k = metrics.compute_kpis(metrics.build_daily_series(linear(15)))
        # parc moyen : 110 comptes (jours 8 à 14), 40 comptes la semaine précédente (jours 1 à 7)
        self.assertAlmostEqual(k["activity_per_100_accounts"], 100 * 700 / 110)
        self.assertAlmostEqual(k["previous_activity_per_100_accounts"], 100 * 700 / 40)
        self.assertAlmostEqual(k["activity_change_pct"], 40 / 110 - 1)  # même activité, parc moyen plus grand

    def test_intensity_uses_average_accounts_not_last_day(self):
        # Un gros afflux de comptes le dernier jour ne doit compter que pour 1/7 de la semaine.
        pts = [(D0 + timedelta(i), 100, 70 * i) for i in range(8)]
        pts[-1] = (pts[-1][0], 800, pts[-1][2])  # +700 comptes le dernier jour
        k = metrics.compute_kpis(metrics.build_daily_series(pts))
        self.assertAlmostEqual(k["activity_per_100_accounts"], 100 * 490 / 200)  # parc moyen (6×100 + 800) / 7 = 200

    def test_previous_period_unavailable_when_history_too_short(self):
        k = metrics.compute_kpis(metrics.build_daily_series(linear(10)))
        self.assertEqual(k["new_users"], 70)
        self.assertIsNone(k["previous_new_users"])
        self.assertIsNone(k["new_users_change_pct"])
        self.assertIsNone(k["new_users_change_abs"])

    def test_division_by_zero(self):
        self.assertIsNone(metrics.pct_change(10, 0))
        self.assertIsNone(metrics.pct_change(None, 5))
        self.assertEqual(metrics.pct_change(0, 0), None)
        self.assertIsNone(metrics.activity_per_100_accounts(50, 0))
        k = metrics.compute_kpis(metrics.build_daily_series(linear(15, users_per_day=0, messages_per_day=0)))
        self.assertEqual(k["new_users"], 0)
        self.assertIsNone(k["new_users_change_pct"])
        self.assertIsNone(k["activity_per_100_accounts"])

    def test_negative_flows_floored_in_windows(self):
        pts = linear(8)
        pts[-1] = (pts[-1][0], pts[-2][1] - 5, pts[-2][2])  # baisse le dernier jour
        k = metrics.compute_kpis(metrics.build_daily_series(pts))
        self.assertEqual(k["new_users"], 60)
        self.assertTrue(k["has_decrease"])

    def test_gap_in_window_flagged(self):
        pts = [p for p in linear(15) if p[0] != D0 + timedelta(12)]
        k = metrics.compute_kpis(metrics.build_daily_series(pts))
        self.assertTrue(k["window_has_gap"])
        self.assertEqual(k["new_users"], 70)  # le rattrapage conserve le total

    def test_rolling_series(self):
        r = metrics.rolling_series(metrics.build_daily_series(linear(10)), "new_users")
        self.assertEqual(len(r), 3)  # premier jour sans flux, puis fenêtres complètes
        self.assertTrue(all(p["value"] == 70 for p in r))
        self.assertEqual(len(metrics.rolling_series(metrics.build_daily_series(linear(20)), "new_users", last_n_days=5)), 5)


class AggregateTests(SimpleTestCase):
    def test_sum_of_domains(self):
        a = metrics.build_daily_series(linear(3))
        b = metrics.build_daily_series(linear(3, users_per_day=1, messages_per_day=1, start_users=5))
        agg = metrics.aggregate_series([a, b])
        self.assertEqual(agg[-1]["cumulative_users"], 20 + 7)
        self.assertEqual(agg[-1]["new_users"], 11)
        self.assertIsNone(agg[0]["new_users"])

    def test_decrease_floored_per_domain_so_parts_equal_total(self):
        a = metrics.build_daily_series(linear(8))
        b = metrics.build_daily_series([(D0 + timedelta(i), 100 - (5 if i == 7 else 0), 0) for i in range(8)])
        total = metrics.compute_kpis(metrics.aggregate_series([a, b]))["new_users"]
        self.assertEqual(total, sum(metrics.compute_kpis(s)["new_users"] for s in (a, b)))
        self.assertEqual(total, 70)

    def test_late_domain_first_value_not_counted_as_flow(self):
        a = metrics.build_daily_series(linear(3))
        late = metrics.build_daily_series([(D0 + timedelta(2), 500, 5000)])
        agg = metrics.aggregate_series([a, late])
        self.assertEqual(agg[2]["new_users"], 10)
        self.assertEqual(agg[2]["cumulative_users"], 520)


class SignalTests(SimpleTestCase):
    NATIONAL = {"new_users_change_pct": 0.0, "messages_change_pct": 0.05}

    def kpis(self, users_pct=0.0, users_prev=50, msg_pct=0.05, msg_prev=1000, intensity_pct=0.0):
        return {"new_users_change_pct": users_pct, "previous_new_users": users_prev,
                "messages_change_pct": msg_pct, "previous_messages": msg_prev,
                "activity_change_pct": intensity_pct}

    def levels(self, signals):
        return [(s["kind"], s["metric"], s["level"]) for s in signals]

    def test_no_signal_when_in_line_with_national(self):
        self.assertEqual(metrics.detect_signals(self.kpis(), self.NATIONAL), [])

    def test_gap_to_national_trend_uses_same_7_day_change(self):
        k = self.kpis(users_pct=-0.44)
        signals = metrics.detect_signals(k, self.NATIONAL)
        self.assertEqual(self.levels(signals), [("ecart_inferieur", "new_users", "strong")])
        self.assertEqual(signals[0]["evolution"], k["new_users_change_pct"])  # cohérent avec le KPI
        self.assertAlmostEqual(signals[0]["gap"], -0.44)

    def test_two_levels(self):
        national = {"new_users_change_pct": -0.01, "messages_change_pct": 0.05}
        # France −1 %, académie +29 % : 30 points d'écart -> signal fort
        strong = metrics.detect_signals(self.kpis(users_pct=0.29, intensity_pct=0.1), national)
        self.assertEqual(self.levels(strong)[0], ("ecart_superieur", "new_users", "strong"))
        # 20 points -> à surveiller ; 10 points -> rien
        self.assertEqual(self.levels(metrics.detect_signals(self.kpis(users_pct=0.19, intensity_pct=0.1), national))[0],
                         ("ecart_superieur", "new_users", "watch"))
        self.assertEqual(metrics.detect_signals(self.kpis(users_pct=0.09, intensity_pct=0.1), national), [])

    def test_strong_signals_listed_first(self):
        national = {"new_users_change_pct": 0.0, "messages_change_pct": 0.05}
        # +20 % vs France 0 % -> diffusion à surveiller ; intensité −10 % -> activation forte
        signals = metrics.detect_signals(self.kpis(users_pct=0.20, intensity_pct=-0.10), national)
        self.assertEqual([(s["kind"], s["level"]) for s in signals],
                         [("activation", "strong"), ("ecart_superieur", "watch")])

    def test_messages_gap_is_not_a_signal(self):
        # L'activité est un contexte, pas un signal : +60 % de messages ne déclenche rien
        self.assertEqual(metrics.detect_signals(self.kpis(msg_pct=0.65), self.NATIONAL), [])

    def test_common_drop_is_not_a_signal(self):
        # Vacances : tout le territoire baisse de 50 % -> pas d'écart propre à l'académie
        national = {"new_users_change_pct": -0.5, "messages_change_pct": -0.5}
        self.assertEqual(metrics.detect_signals(self.kpis(users_pct=-0.55, msg_pct=-0.45), national), [])

    def test_minimum_volume_on_larger_of_two_weeks(self):
        national = {"new_users_change_pct": -0.01, "messages_change_pct": 0.05}
        def kinds(prev, cur):
            k = self.kpis(users_pct=cur / prev - 1, users_prev=prev, intensity_pct=0.5)
            k["new_users"] = cur
            return [(s["metric"], s["level"]) for s in metrics.detect_signals(k, national)]
        self.assertEqual(kinds(26, 82), [("new_users", "strong")])  # Grenoble : 82 cette semaine suffit
        self.assertEqual(kinds(39, 27), [])                         # Montpellier : aucune semaine à 50
        self.assertEqual(kinds(49, 49 * 1.7), [("new_users", "strong")])
        self.assertEqual(kinds(29, 49), [])                         # Dijon : 49 au plus

    def test_minimum_volume(self):
        self.assertEqual(metrics.SIGNAL_MIN_BASE, {"new_users": 50, "messages": 400})
        self.assertEqual(metrics.detect_signals(self.kpis(users_pct=2.0, users_prev=49), self.NATIONAL), [])

    def test_activation_when_usage_does_not_follow(self):
        def levels(users, intensity):
            # Écarts nationaux neutralisés : seul le signal d'activation est testé
            national = {"new_users_change_pct": users, "messages_change_pct": 0.05}
            return [(s["kind"], s["level"]) for s in
                    metrics.detect_signals(self.kpis(users_pct=users, intensity_pct=intensity), national)]
        self.assertEqual(levels(0.09, -0.16), [("activation", "strong")])  # Rennes : intensité en baisse
        self.assertEqual(levels(0.20, -0.05), [("activation", "strong")])  # −5 % = en baisse
        self.assertEqual(levels(0.25, -0.04), [("activation", "watch")])   # intensité stable
        self.assertEqual(levels(0.25, 0.0), [("activation", "watch")])
        self.assertEqual(levels(0.18, 0.13), [])                           # l'usage suit
        self.assertEqual(levels(0.04, -0.20), [])                          # diffusion stable
        self.assertEqual(levels(0.25, None), [])                           # intensité non calculable

    def test_activation_needs_both_volumes(self):
        national = {"new_users_change_pct": 0.25, "messages_change_pct": 0.05}
        self.assertEqual(metrics.detect_signals(self.kpis(users_pct=0.25, msg_prev=100), national), [])


class TrendThresholdTests(SimpleTestCase):
    def test_single_stable_threshold(self):
        self.assertEqual(metrics.STABLE_THRESHOLD, 0.05)
        self.assertEqual(metrics.trend(-0.049), "flat")
        self.assertEqual(metrics.trend(0.05), "up")
        self.assertEqual(metrics.trend(-0.05), "down")
        self.assertEqual(metrics.trend(None), "flat")
