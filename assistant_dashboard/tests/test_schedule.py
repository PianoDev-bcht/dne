from datetime import datetime

from django.test import SimpleTestCase

from assistant_dashboard.services.schedule import SLOTS, next_slot, seconds_until
from assistant_dashboard.services.sync import PARIS


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=PARIS)


class NextSlotTests(SimpleTestCase):
    def test_first_attempt_at_four(self):
        self.assertEqual(next_slot(at(5, 1)), at(5, 4))

    def test_progressive_retries(self):
        self.assertEqual([(s.hour, s.minute) for s in SLOTS], [(4, 0), (4, 30), (5, 30), (7, 30), (11, 30)])
        self.assertEqual(next_slot(at(5, 4)), at(5, 4, 30))  # strictement après le créneau courant
        self.assertEqual(next_slot(at(5, 6)), at(5, 7, 30))

    def test_delay_is_real_time_across_daylight_saving(self):
        autumn = datetime(2026, 10, 24, 23, 0, tzinfo=PARIS)  # nuit du passage à l'heure d'hiver
        self.assertEqual(seconds_until(next_slot(autumn), autumn), 6 * 3600)
        spring = datetime(2027, 3, 27, 23, 0, tzinfo=PARIS)   # nuit du passage à l'heure d'été
        self.assertEqual(seconds_until(next_slot(spring), spring), 4 * 3600)
        self.assertEqual(seconds_until(at(5, 4), at(5, 5)), 0)  # jamais négatif

    def test_after_last_retry_waits_for_next_day(self):
        self.assertEqual(next_slot(at(5, 11, 30)), at(6, 4))
        self.assertEqual(next_slot(at(5, 23, 59)), at(6, 4))
