from datetime import date, datetime, time, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Appointment, PersonRoutine
from core.today import month_summary

from .base import BaseTestCase
from .test_tabbar_today import finish


def at(day, hour=10):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


class MonthSummaryTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.today = date(2026, 10, 17)   # a Saturday
        self.routine = self.alice_routine

    def workout(self, day, hour=10, routine=None):
        return finish(routine or self.routine, at(day, hour))

    def summary(self, today=None):
        return month_summary(self.person, today or self.today, now=at(today or self.today, 12))

    def test_counts_workouts_and_distinct_days_this_month(self):
        self.workout(date(2026, 10, 2)), self.workout(date(2026, 10, 2), 18), self.workout(date(2026, 10, 15))
        self.workout(date(2026, 9, 30))   # last month does not count
        data = self.summary()
        self.assertEqual((data['workouts'], data['days_trained']), (3, 2))
        self.assertEqual(data['month'], date(2026, 10, 1))

    def test_last_month_is_compared_over_the_same_stretch(self):
        for day in (3, 10, 17, 18, 25):   # September: up to the 17th there are three
            self.workout(date(2026, 9, day))
        data = self.summary()
        self.assertEqual(data['previous'], {'month': date(2026, 9, 1), 'workouts': 3, 'until': 17})

    def test_a_shorter_previous_month_and_the_new_year(self):
        self.workout(date(2026, 2, 27)), self.workout(date(2026, 2, 28))
        data = self.summary(date(2026, 3, 31))
        self.assertEqual((data['previous']['until'], data['previous']['workouts']), (28, 2))
        self.workout(date(2025, 12, 5))
        january = self.summary(date(2026, 1, 20))
        self.assertEqual((january['previous']['month'], january['previous']['workouts']), (date(2025, 12, 1), 1))

    def test_late_night_workout_counts_on_its_local_day(self):
        self.workout(date(2026, 10, 1), 23)   # 23:00 local, still the 1st
        data = self.summary()
        self.assertEqual(data['workouts'], 1)
        self.assertTrue(data['cells'][3]['trained'])   # the 1st of October 2026 is a Thursday: three blank cells first

    def test_unfinished_workouts_do_not_count(self):
        from core.models import WorkoutSession
        WorkoutSession.objects.create(person_routine=self.routine)
        self.assertEqual(self.summary()['workouts'], 0)

    def test_classes_done_and_still_scheduled(self):
        Appointment.objects.create(person=self.person, starts_at=at(date(2026, 10, 5)), status=Appointment.Status.ATTENDED)
        Appointment.objects.create(person=self.person, starts_at=at(date(2026, 10, 7)), status=Appointment.Status.MISSED)
        Appointment.objects.create(person=self.person, starts_at=at(date(2026, 10, 20)), status=Appointment.Status.SCHEDULED)
        Appointment.objects.create(person=self.person, starts_at=at(date(2026, 10, 12)), status=Appointment.Status.SCHEDULED)   # in the past
        Appointment.objects.create(person=self.person, starts_at=at(date(2026, 11, 2)), status=Appointment.Status.SCHEDULED)   # next month
        data = self.summary()
        self.assertEqual((data['classes_done'], data['classes_upcoming']), (1, 1))

    def test_grid_lays_the_month_out_from_monday(self):
        cells = self.summary()['cells']
        self.assertEqual([c for c in cells[:3]], [None, None, None])
        self.assertEqual(cells[3]['day'], 1)
        self.assertEqual(len([c for c in cells if c]), 31)
        today = [c for c in cells if c and c['is_today']]
        self.assertEqual([c['day'] for c in today], [17])
        self.assertTrue(all(c['future'] for c in cells if c and c['day'] > 17))

    def test_only_her_own_data(self):
        self.workout(date(2026, 10, 3), routine=self.bob_routine)
        self.assertEqual(self.summary()['workouts'], 0)

    def test_home_shows_the_card_and_the_comparison_only_with_motivation(self):
        finish(self.routine, timezone.now())
        self.client.force_login(self.alice)
        page = self.client.get(reverse('core:my_workouts'))
        home = self.client.get(reverse('core:home'))
        for response in (home,):
            self.assertContains(response, 'data-month-card')
            self.assertContains(response, 'data-month-grid')
        self.assertContains(home, 'data-month-previous')
        self.person.motivation_enabled = False
        self.person.save()
        self.assertNotContains(self.client.get(reverse('core:home')), 'data-month-previous')
        self.assertContains(self.client.get(reverse('core:home')), 'data-month-card')
        self.assertTrue(page.status_code == 200)

    def test_the_trainer_does_not_get_the_card(self):
        self.client.force_login(self.trainer)
        self.assertNotContains(self.client.get(reverse('core:home')), 'data-month-card')

    def test_extra_queries_do_not_grow_with_the_history(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('core:home'))
            return len(ctx)
        self.client.force_login(self.alice)
        few = count()
        for i in range(6):
            routine = PersonRoutine.objects.create(person=self.person, routine_name=f'T{i}')
            finish(routine), finish(routine)
            Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=i + 1))
        self.assertEqual(count(), few)
