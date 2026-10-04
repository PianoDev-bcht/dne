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

    def test_daily_points(self):
        r = metrics.daily_points(metrics.build_daily_series(linear(10)), "new_users")
        self.assertEqual(len(r), 9)  # le premier jour n'a pas de fichier précédent
        self.assertTrue(all(p["value"] == 10 and not p["has_gap"] for p in r))
        self.assertEqual(len(metrics.daily_points(metrics.build_daily_series(linear(20)), "new_users", last_n_days=5)), 5)

    def test_daily_points_flag_missing_days_and_catch_up_day(self):
        # Fichiers absents pour D0+2 et D0+3 : le rattrapage tombe sur D0+4 (30 comptes pour 3 jours).
        pts = [p for p in linear(7) if p[0] not in (D0 + timedelta(2), D0 + timedelta(3))]
        r = metrics.daily_points(metrics.build_daily_series(pts), "new_users")
        self.assertEqual([p["has_gap"] for p in r], [False, True, True, True, False, False])
        self.assertEqual([p["value"] for p in r], [10, 0, 0, 30, 10, 10])

    def test_daily_points_floor_negative_flow(self):
        r = metrics.daily_points(metrics.build_daily_series([(D0, 10, 100), (D0 + timedelta(1), 8, 90)]), "new_users")
        self.assertEqual(r[0]["value"], 0)


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
    """7 jours pour détecter (à suivre), 14 jours pour agir (à analyser)."""
    NATIONAL = {"new_users_change_pct": 0.0, "messages_change_pct": 0.0, "activity_change_pct": 0.0}

    def kpis(self, users_pct=0.0, prev=200, cur=None, intensity_pct=0.0, msgs=5000):
        cur = round(prev * (1 + users_pct)) if cur is None else cur
        return {"new_users_change_pct": users_pct, "previous_new_users": prev, "new_users": cur,
                "messages_change_pct": 0.0, "previous_messages": msgs, "messages": msgs,
                "activity_change_pct": intensity_pct}

    def signals(self, k7=None, k14=None, n7=None, n14=None, blocked=None):
        return metrics.detect_signals(k7 or self.kpis(), n7 or self.NATIONAL,
                                      k14 or self.kpis(), n14 or self.NATIONAL, blocked)

    def summary(self, signals):
        return [(s["kind"], s["window"], s["level"]) for s in signals]

    def test_relative_change(self):
        self.assertAlmostEqual(metrics.relative_change(-0.10, 0.10), 0.9 / 1.1 - 1)  # exemple de la méthodologie
        self.assertAlmostEqual(metrics.relative_change(-0.55, -0.50), -0.10)       # vacances : effet commun neutralisé
        self.assertIsNone(metrics.relative_change(None, 0.1))

    def test_no_signal_when_in_line_with_national(self):
        self.assertEqual(self.signals(), [])

    def test_action_on_14_days_at_20_percent_confirmed_by_last_week(self):
        down7, up7 = self.kpis(-0.05), self.kpis(0.05, intensity_pct=0.1)
        self.assertEqual(self.summary(self.signals(k7=down7, k14=self.kpis(-0.20))), [("ecart_inferieur", 14, "strong")])
        self.assertEqual(self.summary(self.signals(k7=up7, k14=self.kpis(0.25, intensity_pct=0.1))),
                         [("ecart_superieur", 14, "strong")])
        self.assertEqual(self.signals(k7=down7, k14=self.kpis(-0.19)), [])

    def test_unconfirmed_14_day_trend_stays_watch(self):
        # Créteil : −21 % sur 14 jours mais +12 % sur la dernière semaine -> à suivre, pas d'action
        s = self.signals(k7=self.kpis(0.10), k14=self.kpis(-0.25))
        self.assertEqual(self.summary(s), [("ecart_inferieur", 14, "watch")])
        self.assertEqual(s[0]["reason"], "tendance sur 14 jours non confirmée par la dernière semaine")
        self.assertAlmostEqual(s[0]["relative7"], 0.10)

    def test_watch_on_7_days_at_15_percent(self):
        self.assertEqual(self.summary(self.signals(k7=self.kpis(-0.15, prev=60))), [("ecart_inferieur", 7, "watch")])
        self.assertEqual(self.signals(k7=self.kpis(-0.14, prev=60)), [])

    def test_14_day_signal_wins_over_7_day(self):
        s = self.signals(k7=self.kpis(-0.30), k14=self.kpis(-0.25))  # 7 j de même sens : confirmé
        self.assertEqual(self.summary(s), [("ecart_inferieur", 14, "strong")])

    def test_common_drop_is_not_a_signal(self):
        national = {"new_users_change_pct": -0.5, "messages_change_pct": -0.5, "activity_change_pct": 0.0}
        self.assertEqual(self.signals(k14=self.kpis(-0.55), n14=national, k7=self.kpis(-0.55), n7=national), [])

    def test_volume_required_on_each_period(self):
        self.assertEqual(metrics.MIN_PERIOD_VOLUME, {7: {"new_users": 40, "messages": 400},
                                                     14: {"new_users": 80, "messages": 800}})
        # Évolutions nettement au-delà des seuils : seul le volume peut empêcher le signal.
        self.assertEqual(self.signals(k14=self.kpis(0.9, prev=79, cur=150)), [])   # 79 avant : pas évalué
        self.assertEqual(self.signals(k14=self.kpis(-0.47, prev=150, cur=79)), [])  # 79 après : pas évalué
        self.assertEqual(self.signals(k7=self.kpis(2.36, prev=28, cur=94)), [])     # Grenoble : petite base
        # 56 < 80 sur la période récente : pas de signal sur 14 jours, seul le signal sur 7 jours reste.
        s = self.signals(k7=self.kpis(-0.30), k14=self.kpis(-0.30, prev=80, cur=56))
        self.assertEqual(self.summary(s), [("ecart_inferieur", 7, "watch")])

    def test_confirmation_needs_a_non_stable_week(self):
        # Normandie : −21 % sur 14 jours, mais −0,09 % sur 7 jours (zone stable) : pas d'action.
        s = self.signals(k7=self.kpis(-0.0009), k14=self.kpis(-0.21))
        self.assertEqual(self.summary(s), [("ecart_inferieur", 14, "watch")])
        self.assertEqual(s[0]["reason"], "tendance sur 14 jours non confirmée par la dernière semaine")

    def test_confirmation_needs_weekly_volume(self):
        # Grenoble : la semaine précédente ne compte que 28 comptes (< 40) : pas d'action.
        s = self.signals(k7=self.kpis(2.36, prev=28, cur=94), k14=self.kpis(0.49, prev=82, cur=122, intensity_pct=0.1))
        self.assertEqual(self.summary(s), [("ecart_superieur", 14, "watch")])
        self.assertEqual(s[0]["reason"], "volume insuffisant sur la dernière semaine")
        self.assertIsNone(s[0]["display"]["rel7"])

    def test_opposite_last_week_is_not_hidden(self):
        # Strasbourg : +28 % sur 14 jours, −26 % sur 7 jours : c'est le ralentissement récent qui est montré.
        s = self.signals(k7=self.kpis(-0.26), k14=self.kpis(0.28, intensity_pct=0.1))
        self.assertEqual(self.summary(s), [("ecart_inferieur", 7, "watch")])

    def test_display_is_rounded_toward_zero(self):
        self.assertEqual(metrics.display_pct(-0.1975), -19)               # Amiens : seuil de 20 % non atteint
        self.assertEqual(metrics.display_pct(-0.19999999999999996), -20)  # seuil atteint, même chiffre
        self.assertEqual(metrics.display_pct(0.29), 29)
        s = self.signals(k7=self.kpis(-0.10), k14=self.kpis(-0.25))
        self.assertEqual(s[0]["display"], {"rel14": -25, "rel7": -10})

    def test_activation_never_with_strong_slowdown(self):
        # Comptes +10 % mais −21 % par rapport aux académies (+40 %), confirmé sur 7 jours :
        # 02 Diagnostiquer, et pas 03 Activer en même temps, même si l'intensité baisse.
        ref14 = {"new_users_change_pct": 0.4, "messages_change_pct": 0.0, "activity_change_pct": 0.0}
        ref7 = {"new_users_change_pct": 0.1, "messages_change_pct": 0.0, "activity_change_pct": 0.0}
        s = self.signals(k7=self.kpis(-0.10), n7=ref7, k14=self.kpis(0.10, intensity_pct=-0.2), n14=ref14)
        self.assertEqual(self.summary(s), [("ecart_inferieur", 14, "strong")])

    def test_blocked_action_is_downgraded_with_reason(self):
        s = self.signals(k7=self.kpis(-0.10), k14=self.kpis(-0.30), blocked="vacances d'hiver")
        self.assertEqual(self.summary(s), [("ecart_inferieur", 14, "watch")])
        self.assertEqual(s[0]["reason"], "vacances d'hiver")

    def test_activation_on_14_days(self):
        def levels(users, intensity, blocked=None):
            national = {"new_users_change_pct": users, "messages_change_pct": 0.0, "activity_change_pct": 0.0}
            k14 = self.kpis(users, intensity_pct=intensity)
            return [(s["kind"], s["level"]) for s in self.signals(k14=k14, n14=national, blocked=blocked)]
        self.assertEqual(levels(0.09, -0.16), [("activation", "strong")])
        self.assertEqual(levels(0.25, -0.04), [("activation", "watch")])   # intensité stable
        self.assertEqual(levels(0.18, 0.13), [])                            # l'usage suit
        self.assertEqual(levels(0.04, -0.20), [])                           # diffusion stable
        self.assertEqual(levels(0.09, -0.16, blocked="x"), [("activation", "watch")])

    def test_activation_is_relative_to_national_intensity(self):
        def kinds(local, national):
            n14 = {"new_users_change_pct": 0.2, "messages_change_pct": 0.0, "activity_change_pct": national}
            return [(s["kind"], s["level"]) for s in self.signals(k14=self.kpis(0.2, intensity_pct=local), n14=n14)]
        self.assertEqual(kinds(-0.155, -0.207), [])                    # Aix-Marseille : mieux que la France
        self.assertEqual(kinds(-0.20, 0.0), [("activation", "strong")])
        self.assertEqual(kinds(-0.03, 0.0), [("activation", "watch")])

    def test_activation_needs_message_volume(self):
        national = {"new_users_change_pct": 0.2, "messages_change_pct": 0.0, "activity_change_pct": 0.0}
        k14 = self.kpis(0.2, intensity_pct=-0.2, msgs=700)
        self.assertEqual(self.signals(k14=k14, n14=national), [])

    def test_window_boundary_gap(self):
        end = date(2026, 10, 2)
        # Trous du 04 au 08/09 : la veille du début de la période précédente (05/09) est manquante.
        missing = {date(2026, 9, d) for d in range(4, 9)}
        self.assertTrue(metrics.window_boundary_gap(missing, end, 14))
        self.assertFalse(metrics.window_boundary_gap(missing, date(2026, 10, 7), 14))  # période dès le 10/09
        # Un trou entièrement à l'intérieur d'une période ne fausse pas son total.
        self.assertFalse(metrics.window_boundary_gap({date(2026, 9, 25)}, end, 14))
        self.assertTrue(metrics.window_boundary_gap({date(2026, 9, 18)}, end, 14))  # fin de la période précédente


class TrendThresholdTests(SimpleTestCase):
    def test_single_stable_threshold(self):
        self.assertEqual(metrics.STABLE_THRESHOLD, 0.05)
        self.assertEqual(metrics.trend(-0.049), "flat")
        self.assertEqual(metrics.trend(0.05), "up")
        self.assertEqual(metrics.trend(-0.05), "down")
        self.assertEqual(metrics.trend(None), "flat")
