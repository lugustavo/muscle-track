from datetime import date, datetime, time, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import consistency as c
from core.models import Person, Plan

from .base import BaseTestCase
from .test_tabbar_today import finish

TODAY = date(2026, 10, 14)            # a Wednesday
MONDAY = date(2026, 10, 12)


def at(day, hour=9):
    return timezone.make_aware(datetime.combine(day, time(hour)))


def weeks_ago(n):
    return MONDAY - timedelta(weeks=n)


class StreakTests(BaseTestCase):
    def counts(self, **per_week):
        return {weeks_ago(int(k[1:])): v for k, v in per_week.items()}

    def test_consecutive_weeks_that_reached_the_goal(self):
        counts = self.counts(w0=3, w1=3, w2=4, w3=2, w4=3)
        result = c.streak(counts, goal=3, today=TODAY)
        self.assertEqual((result['weeks'], result['met_this_week'], result['keeping']), (3, True, False))

    def test_week_in_progress_does_not_break_the_streak(self):
        counts = self.counts(w0=1, w1=3, w2=3)
        result = c.streak(counts, goal=3, today=TODAY)
        self.assertEqual(result['weeks'], 2)
        self.assertTrue(result['keeping'])
        self.assertEqual(result['missing_this_week'], 2)

    def test_a_missed_week_ends_it(self):
        counts = self.counts(w0=3, w1=1, w2=3, w3=3)
        self.assertEqual(c.streak(counts, goal=3, today=TODAY)['weeks'], 1)
        self.assertEqual(c.streak(self.counts(w1=0, w2=3), goal=3, today=TODAY)['weeks'], 0)

    def test_without_a_goal_one_workout_a_week_counts(self):
        counts = self.counts(w0=1, w1=1, w2=2)
        self.assertEqual(c.streak(counts, goal=None, today=TODAY)['weeks'], 3)

    def test_empty_history(self):
        result = c.streak({}, goal=3, today=TODAY)
        self.assertEqual((result['weeks'], result['keeping']), (0, False))

    def test_milestones(self):
        self.assertEqual(c.next_milestone(0), (10, 0))
        self.assertEqual(c.next_milestone(9), (10, 0))
        self.assertEqual(c.next_milestone(10), (25, 10))
        self.assertEqual(c.next_milestone(99), (100, 50))
        self.assertEqual(c.next_milestone(1000), (None, 1000))

    def test_ring(self):
        self.assertIsNone(c.ring(2, None))
        ring = c.ring(1, 4)
        self.assertEqual((ring['percent'], ring['complete']), (25, False))
        self.assertAlmostEqual(ring['offset'], 226.19 * 0.75, places=1)
        self.assertTrue(c.ring(5, 3)['complete'])
        self.assertEqual(c.ring(5, 3)['percent'], 100)


class GoalTests(BaseTestCase):
    def test_goal_comes_from_the_plan_then_from_the_student(self):
        person = self.alice.person
        self.assertIsNone(c.effective_goal(person))
        person.weekly_goal = 2
        self.assertEqual(c.effective_goal(person), 2)
        plan = Plan.objects.create(name='Mensal 4x', sessions_per_week=4)
        person.plan = plan
        self.assertEqual(c.effective_goal(person), 4)
        plan.sessions_per_week = None
        plan.save()
        self.assertEqual(c.effective_goal(person), 2)


class MotivationScreenTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.url = reverse('core:home')
        self.login(self.alice)

    def test_ring_streak_and_milestone_on_hoje(self):
        Person.objects.filter(pk=self.person.pk).update(weekly_goal=2)
        now = timezone.now()
        monday = now.date() - timedelta(days=now.date().weekday())
        for week in (1, 2):
            for day in (0, 2):
                finish(self.alice_routine, at(monday - timedelta(weeks=week) + timedelta(days=day)))
        finish(self.alice_routine, at(monday))      # one this week
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-motivation', html)
        self.assertIn('Meta da semana: 1 de 2 treinos', html)
        self.assertIn('Faltam 1 treino para a meta', html)
        self.assertIn('2 semanas seguidas', html)
        self.assertIn('treine 1 vez para manter', html)
        self.assertIn('Próximo marco: <strong>10 treinos</strong>', html)

    def test_no_ring_without_a_goal_but_the_milestone_stays(self):
        html = self.client.get(self.url).content.decode()
        self.assertNotIn('data-motivation', html)
        self.assertIn('data-milestone', html)

    def test_goal_met_message(self):
        Person.objects.filter(pk=self.person.pk).update(weekly_goal=1)
        finish(self.alice_routine, timezone.now())
        html = self.client.get(self.url).content.decode()
        self.assertIn('Meta batida!', html)
        self.assertIn('goal-ring complete', html)

    def test_switching_it_off_hides_everything(self):
        Person.objects.filter(pk=self.person.pk).update(weekly_goal=2, motivation_enabled=False)
        finish(self.alice_routine, timezone.now())
        html = self.client.get(self.url).content.decode()
        for marker in ('data-motivation', 'data-milestone', 'data-streak', 'goal-ring'):
            self.assertNotIn(marker, html)
        self.assertIn('Sua semana', html)               # the plain week dots stay
        self.assertIn('1 de 2 treinos', html)

    def test_other_students_do_not_influence_it(self):
        for _ in range(12):
            finish(self.bob_routine, timezone.now())
        self.assertEqual(self.client.get(self.url).context['today']['motivation']['total'], 0)

    def test_query_count_is_small_and_constant(self):
        with CaptureQueriesContext(connection) as few:
            self.client.get(self.url)
        for i in range(15):
            finish(self.alice_routine, timezone.now() - timedelta(days=i))
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url)
        self.assertEqual(len(few), len(many))


class SettingsTests(BaseTestCase):
    def setUp(self):
        self.login(self.alice)
        self.person = self.alice.person
        self.url = reverse('core:my_profile')

    def post(self, **extra):
        data = {'person_name': 'Alice', 'address': '', 'city': '', 'state': '', 'zip_code': '', 'phone': '', 'birth_date': ''}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_profile_shows_the_two_settings(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('Metas e motivação', html)
        self.assertIn('name="motivation_enabled"', html)
        self.assertIn('name="weekly_goal"', html)

    def test_saving_the_goal_and_the_switch(self):
        self.assertEqual(self.post(weekly_goal='4', motivation_enabled='on').status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual((self.person.weekly_goal, self.person.motivation_enabled), (4, True))
        self.post(weekly_goal='')                    # unchecked box = off, empty goal = none
        self.person.refresh_from_db()
        self.assertEqual((self.person.weekly_goal, self.person.motivation_enabled), (None, False))

    def test_goal_must_be_between_one_and_seven(self):
        for bad in ('0', '8', '-1'):
            self.assertEqual(self.post(weekly_goal=bad, motivation_enabled='on').status_code, 200, bad)
        self.person.refresh_from_db()
        self.assertIsNone(self.person.weekly_goal)

    def test_both_settings_are_in_the_students_data_export(self):
        from core.exports import build_data
        data = build_data(self.person, include_internal_notes=False)
        self.assertIn('motivation_enabled', data['profile'])
        self.assertIn('weekly_goal', data['profile'])


class SummaryCelebrationTests(BaseTestCase):
    def setUp(self):
        self.login(self.alice)

    def summary(self, session):
        return self.client.get(reverse('core:workout_summary', kwargs={'pk': session.pk})).content.decode()

    def test_tenth_workout_is_a_milestone(self):
        sessions = [finish(self.alice_routine, timezone.now() - timedelta(days=20 - i)) for i in range(10)]
        self.assertIn('Marco: 10 treinos concluídos!', self.summary(sessions[-1]))
        self.assertNotIn('Marco:', self.summary(sessions[-2]))

    def test_the_workout_that_reaches_the_weekly_goal(self):
        Person.objects.filter(pk=self.alice.person.pk).update(weekly_goal=2)
        now = timezone.now()
        monday = now.date() - timedelta(days=now.date().weekday())
        first = finish(self.alice_routine, at(monday, 7))
        second = finish(self.alice_routine, at(monday, 18))
        self.assertNotIn('Meta da semana batida', self.summary(first))
        self.assertIn('Meta da semana batida', self.summary(second))
        self.assertEqual(self.summary(second).count('data-celebration'), 1)

    def test_nothing_when_the_layer_is_off(self):
        Person.objects.filter(pk=self.alice.person.pk).update(weekly_goal=1, motivation_enabled=False)
        self.assertNotIn('data-celebration', self.summary(finish(self.alice_routine, timezone.now())))
