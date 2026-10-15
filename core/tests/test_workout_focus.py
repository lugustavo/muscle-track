from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Exercise, PersonRoutine, PersonRoutineExercise, SessionExercise, SessionSet, WorkoutSession,
)
from core.workout_summary import session_summary, session_volume

from .base import BaseTestCase


def start(client, routine):
    client.post(reverse('core:workout_start', kwargs={'pk': routine.pk}))
    return WorkoutSession.objects.filter(person_routine=routine, is_completed=False).first()


def sets_of(session, exercise):
    return list(SessionSet.objects.filter(
        session_exercise__session=session, session_exercise__person_routine_exercise__exercise=exercise
    ).order_by('number'))


def finish_set(s, weight, reps):
    SessionSet.objects.filter(pk=s.pk).update(is_done=True, weight=Decimal(str(weight)), reps=reps)


def close(session, minutes=40, start_ago=None):
    """Mark a session finished `minutes` long, optionally started `start_ago` ago."""
    started = timezone.now() - (start_ago or timedelta(minutes=minutes))
    WorkoutSession.objects.filter(pk=session.pk).update(
        start_time=started, end_time=started + timedelta(minutes=minutes), duration_minutes=minutes, is_completed=True)


class QueueOrderTests(BaseTestCase):
    """Finished exercises go to the end of the queue; the trainer's order is kept inside each group."""

    def setUp(self):
        # alice's routine: Supino (order 1), Crucifixo (order 2); add two more
        group = self.group
        self.remada = Exercise.objects.create(exercise_name='Remada', muscle_group=group)
        self.agacho = Exercise.objects.create(exercise_name='Agachamento', muscle_group=group)
        PersonRoutineExercise.objects.create(person_routine=self.alice_routine, exercise=self.remada, order=3, sets=3, reps=10)
        PersonRoutineExercise.objects.create(person_routine=self.alice_routine, exercise=self.agacho, order=4, sets=3, reps=10)
        self.login(self.alice)
        self.session = start(self.client, self.alice_routine)
        self.url = reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk})

    def names(self, response=None):
        response = response or self.client.get(self.url)
        return [e['prx'].exercise.exercise_name for e in response.context['exercises']]

    def complete(self, exercise):
        for s in sets_of(self.session, exercise):
            finish_set(s, 20, 10)

    def test_untouched_workout_keeps_the_trainers_order(self):
        self.client.get(self.url)  # creates the sets
        self.assertEqual(self.names(), ['Supino', 'Crucifixo', 'Remada', 'Agachamento'])

    def test_finished_exercise_moves_to_the_end(self):
        self.client.get(self.url)
        self.complete(self.supino)
        self.assertEqual(self.names(), ['Crucifixo', 'Remada', 'Agachamento', 'Supino'])

    def test_finished_group_keeps_its_original_order(self):
        self.client.get(self.url)
        self.complete(self.remada)
        self.complete(self.supino)
        self.assertEqual(self.names(), ['Crucifixo', 'Agachamento', 'Supino', 'Remada'])

    def test_unfinishing_a_set_brings_it_back(self):
        self.client.get(self.url)
        self.complete(self.supino)
        SessionSet.objects.filter(pk=sets_of(self.session, self.supino)[0].pk).update(is_done=False)
        self.assertEqual(self.names()[0], 'Supino')

    def test_everything_finished(self):
        self.client.get(self.url)
        for e in (self.supino, self.crucifixo, self.remada, self.agacho):
            self.complete(e)
        response = self.client.get(self.url)
        self.assertEqual(self.names(response), ['Supino', 'Crucifixo', 'Remada', 'Agachamento'])
        self.assertTrue(response.context['all_done'])
        self.assertContains(response, 'Todos os exercícios concluídos')

    def test_page_renders_the_queue_in_that_order_with_a_divider(self):
        self.client.get(self.url)
        self.complete(self.supino)
        text = self.client.get(self.url).content.decode()
        order = [text.index(f'data-name="{n}"') for n in ('Crucifixo', 'Remada', 'Agachamento', 'Supino')]
        self.assertEqual(order, sorted(order))
        divider = text.index('id="doneDivider"')
        self.assertTrue(text.index('data-name="Agachamento"') < divider < text.index('data-name="Supino"'))
        self.assertEqual(text.count('id="doneDivider"'), 1)

    def test_no_divider_without_finished_exercises(self):
        self.assertNotIn('id="doneDivider"', self.client.get(self.url).content.decode())

    def test_only_the_current_and_started_exercises_are_open(self):
        self.client.get(self.url)
        finish_set(sets_of(self.session, self.remada)[0], 30, 10)  # Remada already in progress
        response = self.client.get(self.url)
        opened = {e['prx'].exercise.exercise_name: e['expanded'] for e in response.context['exercises']}
        self.assertEqual(opened, {'Supino': True, 'Crucifixo': False, 'Remada': True, 'Agachamento': False})

    def test_finished_exercise_is_collapsed(self):
        self.client.get(self.url)
        self.complete(self.supino)
        opened = {e['prx'].exercise.exercise_name: e['expanded'] for e in self.client.get(self.url).context['exercises']}
        self.assertFalse(opened['Supino'])
        self.assertTrue(opened['Crucifixo'])

    def test_header_shows_exercise_progress_and_sets(self):
        self.client.get(self.url)
        self.complete(self.supino)
        finish_set(sets_of(self.session, self.crucifixo)[0], 15, 12)
        response = self.client.get(self.url)
        self.assertEqual((response.context['exercise_total'], response.context['exercises_done']), (4, 1))
        self.assertEqual((response.context['sets_done'], response.context['sets_total']), (4, 12))
        self.assertEqual(response.context['sets_percent'], 33)
        self.assertContains(response, 'Exercício <strong id="progressCurrent">2</strong> de 4')
        self.assertContains(response, 'role="progressbar"')

    def test_focus_markup(self):
        text = self.client.get(self.url).content.decode()
        self.assertIn('data-step="-1"', text)
        self.assertIn('data-step="1"', text)
        self.assertIn('id="prefSound"', text)
        self.assertIn('id="prefVibrate"', text)
        self.assertIn('data-order="1"', text)
        self.assertIn('body > .navbar { display: none; }', text)  # the site's top menu gives way on phones
        self.assertIn('class="tabbar d-lg-none"', text)           # the bottom bar stays
        self.assertNotIn('SÉRI', text)

    def test_javascript_is_served_and_has_no_syntax_problems(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'workout.js').read_text()
        for needle in ('reorderQueue', 'stepWeight', 'copyWeightToNext', 'mt-prefs-v1', 'prefers-reduced-motion'):
            self.assertIn(needle, js)
        self.assertEqual(js.count('{'), js.count('}'))


class SummaryTests(BaseTestCase):
    def setUp(self):
        self.login(self.alice)
        self.session = start(self.client, self.alice_routine)
        self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))

    def end(self, notes='foi bem'):
        return self.client.post(reverse('core:workout_end', kwargs={'pk': self.session.pk}), {'notes': notes})

    def summary_url(self, session=None):
        return reverse('core:workout_summary', kwargs={'pk': (session or self.session).pk})

    def test_ending_redirects_to_the_summary(self):
        response = self.end()
        self.assertRedirects(response, self.summary_url())
        page = self.client.get(self.summary_url())
        self.assertContains(page, 'Treino concluído!')
        self.assertContains(page, 'foi bem')
        self.assertContains(page, reverse('core:home'))
        self.assertContains(page, reverse('core:my_history'))

    def test_volume_sets_and_exercises(self):
        a, b, c = sets_of(self.session, self.supino)
        finish_set(a, 20, 10)
        finish_set(b, 20, 10)
        finish_set(c, 22.5, 8)
        SessionSet.objects.filter(pk=sets_of(self.session, self.crucifixo)[0].pk).update(is_done=True, weight=None, reps=12)  # no weight: 0
        self.end()
        data = session_summary(WorkoutSession.objects.get(pk=self.session.pk))
        self.assertEqual(data['volume'], Decimal('580'))
        self.assertEqual((data['sets_done'], data['sets_total']), (4, 6))
        self.assertEqual((data['exercises_done'], data['exercises_total']), (1, 2))
        page = self.client.get(self.summary_url())
        self.assertContains(page, '580')
        self.assertContains(page, '4<small class="fs-6">/6</small>')

    def test_volume_ignores_sets_not_done_and_incomplete_values(self):
        a, b, c = sets_of(self.session, self.supino)
        finish_set(a, 20, 10)
        SessionSet.objects.filter(pk=b.pk).update(weight=Decimal('100'), reps=10, is_done=False)   # not done
        SessionSet.objects.filter(pk=c.pk).update(weight=Decimal('50'), reps=None, is_done=True)   # no reps
        self.end()
        self.assertEqual(session_volume(WorkoutSession.objects.get(pk=self.session.pk)), Decimal('200'))

    def test_record_needs_earlier_history(self):
        a, _, _ = sets_of(self.session, self.supino)
        finish_set(a, 40, 8)
        self.end()
        self.assertEqual(session_summary(WorkoutSession.objects.get(pk=self.session.pk))['records'], [])  # first time

    def test_new_record_against_earlier_sessions(self):
        old = WorkoutSession.objects.create(person_routine=self.alice_routine)
        prx = PersonRoutineExercise.objects.get(person_routine=self.alice_routine, exercise=self.supino)
        log = SessionExercise.objects.create(session=old, person_routine_exercise=prx)
        SessionSet.objects.create(session_exercise=log, number=1, is_done=True, weight=Decimal('30'), reps=10)
        close(old, start_ago=timedelta(days=5))
        a, b, _ = sets_of(self.session, self.supino)
        finish_set(a, 32.5, 8)
        finish_set(b, 25, 10)
        self.end()
        records = session_summary(WorkoutSession.objects.get(pk=self.session.pk))['records']
        self.assertEqual([(r['name'], r['weight']) for r in records], [('Supino', Decimal('32.5'))])
        self.assertContains(self.client.get(self.summary_url()), 'Novos recordes de carga')

    def test_equal_or_lower_weight_is_not_a_record(self):
        old = WorkoutSession.objects.create(person_routine=self.alice_routine)
        prx = PersonRoutineExercise.objects.get(person_routine=self.alice_routine, exercise=self.supino)
        log = SessionExercise.objects.create(session=old, person_routine_exercise=prx)
        SessionSet.objects.create(session_exercise=log, number=1, is_done=True, weight=Decimal('30'), reps=10)
        close(old, start_ago=timedelta(days=5))
        a, _, _ = sets_of(self.session, self.supino)
        finish_set(a, 30, 10)
        self.end()
        self.assertEqual(session_summary(WorkoutSession.objects.get(pk=self.session.pk))['records'], [])
        self.assertNotContains(self.client.get(self.summary_url()), 'Novos recordes')

    def test_comparison_with_the_last_workout_of_the_same_plan(self):
        old = WorkoutSession.objects.create(person_routine=self.alice_routine)
        prx = PersonRoutineExercise.objects.get(person_routine=self.alice_routine, exercise=self.supino)
        log = SessionExercise.objects.create(session=old, person_routine_exercise=prx)
        SessionSet.objects.create(session_exercise=log, number=1, is_done=True, weight=Decimal('20'), reps=10)  # 200
        close(old, minutes=50, start_ago=timedelta(days=4))
        a, _, _ = sets_of(self.session, self.supino)
        finish_set(a, 25, 10)                                                                                    # 250
        self.end()
        comparison = session_summary(WorkoutSession.objects.get(pk=self.session.pk))['comparison']
        self.assertEqual((comparison['volume_pct'], comparison['last_duration']), (25, 50))
        page = self.client.get(self.summary_url())
        self.assertContains(page, 'Comparado com o último treino')
        self.assertContains(page, '25%')

    def test_no_comparison_for_the_first_workout_or_other_plans(self):
        other = PersonRoutine.objects.create(person=self.alice.person, routine_name='Outro')
        old = WorkoutSession.objects.create(person_routine=other)
        close(old, start_ago=timedelta(days=2))
        self.end()
        self.assertIsNone(session_summary(WorkoutSession.objects.get(pk=self.session.pk))['comparison'])
        self.assertNotContains(self.client.get(self.summary_url()), 'Comparado com o último')

    def test_lower_volume_is_shown_without_judgement(self):
        old = WorkoutSession.objects.create(person_routine=self.alice_routine)
        prx = PersonRoutineExercise.objects.get(person_routine=self.alice_routine, exercise=self.supino)
        log = SessionExercise.objects.create(session=old, person_routine_exercise=prx)
        SessionSet.objects.create(session_exercise=log, number=1, is_done=True, weight=Decimal('40'), reps=10)
        close(old, start_ago=timedelta(days=3))
        a, _, _ = sets_of(self.session, self.supino)
        finish_set(a, 20, 10)
        self.end()
        page = self.client.get(self.summary_url())
        self.assertContains(page, '-50%')
        self.assertNotContains(page, 'text-danger')

    def test_only_the_owner_sees_a_finished_session(self):
        self.end()
        url = self.summary_url()
        self.login(self.bob)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.login(self.trainer)
        self.assertEqual(self.client.get(url).status_code, 302)  # not a student: sent home
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_running_session_has_no_summary(self):
        self.assertEqual(self.client.get(self.summary_url()).status_code, 404)

    def test_old_completion_message_is_gone(self):
        response = self.client.post(reverse('core:workout_end', kwargs={'pk': self.session.pk}), {'notes': ''}, follow=True)
        self.assertNotContains(response, 'Duração: ')
        self.assertEqual(response.status_code, 200)

    def test_confidential_values_never_appear(self):
        from core.models import Person
        Person.objects.filter(pk=self.alice.person.pk).update(hourly_rate=Decimal('187.35'), plan_price=Decimal('91.77'))
        self.end()
        text = self.client.get(self.summary_url()).content.decode()
        for value in ('187.35', '187,35', '91.77', '91,77'):
            self.assertNotIn(value, text)

    def test_bottom_bar_keeps_workouts_lit_on_the_summary(self):
        self.end()
        text = self.client.get(self.summary_url()).content.decode()
        bar = text[text.index('<nav class="tabbar'):]
        self.assertEqual(bar.count('aria-current="page"'), 1)
        self.assertIn('>Treinos<', bar[bar.index('aria-current="page"'):][:200])

    def test_queries_do_not_grow_with_the_number_of_exercises(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(self.summary_url())
            return len(ctx)
        self.end()
        few = count()
        for i in range(8):
            ex = Exercise.objects.create(exercise_name=f'Extra {i}', muscle_group=self.group)
            prx = PersonRoutineExercise.objects.create(person_routine=self.alice_routine, exercise=ex, order=10 + i)
            log = SessionExercise.objects.create(session=self.session, person_routine_exercise=prx)
            SessionSet.objects.create(session_exercise=log, number=1, is_done=True, weight=Decimal('10'), reps=10)
        self.assertEqual(count(), few)
