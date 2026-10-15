import json
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core.models import PersonRoutine, SessionExercise, SessionSet, WorkoutSession

from .base import BaseTestCase


def post_json(client, url, payload, raw=False):
    return client.post(url, data=payload if raw else json.dumps(payload), content_type='application/json')


class WorkoutTestMixin:
    def start(self, routine=None):
        routine = routine or self.alice_routine
        return self.client.post(reverse('core:workout_start', kwargs={'pk': routine.pk}))

    def execute(self, routine=None):
        routine = routine or self.alice_routine
        return self.client.get(reverse('core:workout_execute', kwargs={'pk': routine.pk}))

    def begin(self):
        self.start()
        response = self.execute()
        session = WorkoutSession.objects.get(person_routine=self.alice_routine, is_completed=False)
        return session, response

    def set_url(self, s):
        return reverse('core:workout_set_update', kwargs={'pk': s.pk})


class WorkoutFlowTests(WorkoutTestMixin, BaseTestCase):
    def setUp(self):
        self.login(self.alice)

    def test_start_requires_post(self):
        response = self.client.get(reverse('core:workout_start', kwargs={'pk': self.alice_routine.pk}))
        self.assertEqual(response.status_code, 405)
        self.assertFalse(WorkoutSession.objects.exists())

    def test_execute_creates_a_row_per_prescribed_set(self):
        # supino: 3 sets by default, crucifixo: 3 sets
        session, response = self.begin()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SessionExercise.objects.filter(session=session).count(), 2)
        self.assertEqual(SessionSet.objects.filter(session_exercise__session=session).count(), 6)
        # reloading the page must not duplicate anything
        self.execute()
        self.assertEqual(SessionSet.objects.filter(session_exercise__session=session).count(), 6)

    def test_setting_set_done_updates_exercise_summary(self):
        session, _ = self.begin()
        log = SessionExercise.objects.filter(session=session).first()
        sets = list(log.sets.all())

        response = post_json(self.client, self.set_url(sets[0]), {'done': True, 'weight': '42,5', 'reps': 10})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data['done_sets'], data['total_sets'], data['exercise_completed']), (1, 3, False))
        log.refresh_from_db()
        self.assertEqual(log.weight_used, Decimal('42.50'))
        self.assertFalse(log.is_completed)

        post_json(self.client, self.set_url(sets[1]), {'done': True, 'weight': '50'})
        post_json(self.client, self.set_url(sets[2]), {'done': True, 'weight': '40'})
        log.refresh_from_db()
        self.assertTrue(log.is_completed)
        self.assertEqual(log.weight_used, Decimal('50.00'))  # heaviest done set
        self.assertIsNotNone(log.completed_at)

        # undo one set
        post_json(self.client, self.set_url(sets[1]), {'done': False})
        log.refresh_from_db()
        self.assertFalse(log.is_completed)
        self.assertEqual(log.weight_used, Decimal('42.50'))

    def test_updates_are_idempotent(self):
        session, _ = self.begin()
        s = SessionSet.objects.filter(session_exercise__session=session).first()
        for _ in range(3):
            self.assertEqual(post_json(self.client, self.set_url(s), {'done': True, 'weight': '20', 'reps': 8}).status_code, 200)
        s.refresh_from_db()
        self.assertEqual((s.is_done, s.weight, s.reps), (True, Decimal('20.00'), 8))

    def test_validation(self):
        session, _ = self.begin()
        s = SessionSet.objects.filter(session_exercise__session=session).first()
        self.assertEqual(post_json(self.client, self.set_url(s), {'weight': '0'}).status_code, 200)
        self.assertEqual(post_json(self.client, self.set_url(s), {'weight': ''}).status_code, 200)
        s.refresh_from_db()
        self.assertIsNone(s.weight)
        for bad in ({'weight': 'abc'}, {'weight': '-5'}, {'weight': '1e9'}, {'weight': 'NaN'},
                    {'reps': 'x'}, {'reps': -1}, {'reps': 100000}, {'done': 'yes'}, {'done': 1}):
            with self.subTest(payload=bad):
                self.assertEqual(post_json(self.client, self.set_url(s), bad).status_code, 400)
        for raw in ('not json', '[1, 2]'):
            self.assertEqual(post_json(self.client, self.set_url(s), raw, raw=True).status_code, 400)

    def test_set_update_requires_post(self):
        session, _ = self.begin()
        s = SessionSet.objects.filter(session_exercise__session=session).first()
        self.assertEqual(self.client.get(self.set_url(s)).status_code, 405)

    def test_full_flow_and_notes(self):
        session, _ = self.begin()
        log = SessionExercise.objects.filter(session=session).first()
        response = post_json(self.client, reverse('core:workout_exercise_notes', kwargs={'pk': log.pk}),
                             {'notes': 'ombro incomodou'})
        self.assertEqual(response.status_code, 200)
        log.refresh_from_db()
        self.assertEqual(log.notes, 'ombro incomodou')

        response = self.client.post(reverse('core:workout_end', kwargs={'pk': session.pk}), {'notes': 'Treino pesado'})
        self.assertRedirects(response, reverse('core:workout_summary', kwargs={'pk': session.pk}))
        session.refresh_from_db()
        self.assertTrue(session.is_completed)
        self.assertEqual(session.notes, 'Treino pesado')

        # trainer sees the comments
        self.login(self.trainer)
        page = self.client.get(reverse('core:session_detail', kwargs={'pk': session.pk}))
        self.assertContains(page, 'Treino pesado')
        self.assertContains(page, 'ombro incomodou')

    def test_notes_validation(self):
        session, _ = self.begin()
        log = SessionExercise.objects.filter(session=session).first()
        url = reverse('core:workout_exercise_notes', kwargs={'pk': log.pk})
        self.assertEqual(post_json(self.client, url, {'notes': 123}).status_code, 400)
        self.assertEqual(post_json(self.client, url, 'nope', raw=True).status_code, 400)

    def test_start_twice_recovers_the_same_session(self):
        self.start()
        self.start()
        self.assertEqual(WorkoutSession.objects.count(), 1)

    def test_end_requires_post(self):
        self.start()
        session = WorkoutSession.objects.get()
        response = self.client.get(reverse('core:workout_end', kwargs={'pk': session.pk}))
        self.assertEqual(response.status_code, 405)
        session.refresh_from_db()
        self.assertFalse(session.is_completed)

    def test_stale_session_with_nothing_logged_is_dropped_and_replaced(self):
        self.start()
        first = WorkoutSession.objects.get()
        WorkoutSession.objects.update(start_time=timezone.now() - timedelta(hours=8))
        self.start()
        self.assertFalse(WorkoutSession.objects.filter(pk=first.pk).exists())   # not recorded as an 8 h "training"
        self.assertEqual(WorkoutSession.objects.filter(is_completed=False).count(), 1)
        self.assertEqual(WorkoutSession.objects.filter(is_completed=True).count(), 0)

    def test_stale_session_with_sets_is_closed_where_the_student_stopped(self):
        session, _ = self.begin()
        done = SessionSet.objects.filter(session_exercise__session=session).first()
        post_json(self.client, self.set_url(done), {'done': True, 'weight': '20', 'reps': 10})
        stopped = timezone.now() - timedelta(hours=7)
        WorkoutSession.objects.update(start_time=stopped - timedelta(minutes=20))
        SessionSet.objects.filter(session_exercise__session=session).update(updated_at=stopped - timedelta(minutes=20))
        SessionSet.objects.filter(pk=done.pk).update(completed_at=stopped, updated_at=stopped)
        self.start()
        old = WorkoutSession.objects.get(is_completed=True)
        self.assertEqual(old.duration_minutes, 20)  # until the last set, not 120 and not 440
        self.assertEqual(WorkoutSession.objects.filter(is_completed=False).count(), 1)

    def test_cannot_update_after_session_ended(self):
        session, _ = self.begin()
        s = SessionSet.objects.filter(session_exercise__session=session).first()
        log = s.session_exercise
        session.end_session()
        self.assertEqual(post_json(self.client, self.set_url(s), {'done': True}).status_code, 404)
        self.assertEqual(post_json(
            self.client, reverse('core:workout_exercise_notes', kwargs={'pk': log.pk}), {'notes': 'x'}).status_code, 404)


class PrefillAndRecordsTests(WorkoutTestMixin, BaseTestCase):
    def setUp(self):
        self.login(self.alice)

    def finish_session(self, weights):
        """Run a full session doing `weights` (one per set) on the first exercise."""
        session, _ = self.begin()
        log = SessionExercise.objects.filter(session=session).order_by('person_routine_exercise__order').first()
        for s, w in zip(log.sets.order_by('number'), weights):
            post_json(self.client, self.set_url(s), {'done': True, 'weight': w, 'reps': 10})
        self.client.post(reverse('core:workout_end', kwargs={'pk': session.pk}))
        return session

    def test_next_session_is_prefilled_from_previous_one(self):
        self.finish_session(['20', '25', '30'])
        session, response = self.begin()
        log = SessionExercise.objects.filter(session=session).order_by('person_routine_exercise__order').first()
        self.assertEqual([float(s.weight) for s in log.sets.order_by('number')], [20, 25, 30])
        self.assertTrue(all(not s.is_done for s in log.sets.all()))
        item = response.context['exercises'][0]
        self.assertEqual(item['previous_weight'], Decimal('30.00'))
        self.assertEqual(item['record'], Decimal('30.00'))

    def test_record_ignores_current_session_and_other_students(self):
        self.finish_session(['20', '25', '30'])
        # Bob has a heavier lift on the same exercise: must not leak into Alice's record
        self.client.force_login(self.bob)
        self.client.post(reverse('core:workout_start', kwargs={'pk': self.bob_routine.pk}))
        self.client.get(reverse('core:workout_execute', kwargs={'pk': self.bob_routine.pk}))
        bob_set = SessionSet.objects.filter(session_exercise__session__person_routine=self.bob_routine).first()
        post_json(self.client, self.set_url(bob_set), {'done': True, 'weight': '200'})

        self.login(self.alice)
        _, response = self.begin()
        self.assertEqual(response.context['exercises'][0]['record'], Decimal('30.00'))

    def test_exercise_progress_page_and_history_records(self):
        self.finish_session(['20', '25', '30'])
        response = self.client.get(reverse('core:my_exercise_progress', kwargs={'exercise_pk': self.supino.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['record_log'].weight_used, Decimal('30.00'))
        self.assertEqual(response.context['sessions_count'], 1)

        empty = self.client.get(reverse('core:my_exercise_progress', kwargs={'exercise_pk': self.crucifixo.pk}))
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.context['sessions_count'], 0)

        history = self.client.get(reverse('core:my_history'))
        self.assertEqual(history.status_code, 200)
        records = list(history.context['records'])
        self.assertEqual(records[0]['best'], Decimal('30.00'))
        self.assertEqual(json.loads(history.context['chart_data'])[0]['avg_weight'], 30.0)

    def test_progress_pages_are_per_student(self):
        self.finish_session(['20', '25', '30'])
        self.client.force_login(self.bob)
        response = self.client.get(reverse('core:my_exercise_progress', kwargs={'exercise_pk': self.supino.pk}))
        self.assertEqual(response.context['sessions_count'], 0)


class SuggestionTests(WorkoutTestMixin, BaseTestCase):
    def setUp(self):
        self.login(self.alice)
        self.second = PersonRoutine.objects.create(person=self.alice.person, routine_name='B - Costas')

    def suggested(self):
        items = self.client.get(reverse('core:my_workouts')).context['routines_with_sessions']
        return [i['routine'] for i in items if i['suggested']], items

    def test_single_routine_has_no_suggestion(self):
        self.second.is_active = False
        self.second.save()
        suggested, _ = self.suggested()
        self.assertEqual(suggested, [])

    def test_rotates_to_the_routine_done_longest_ago(self):
        suggested, items = self.suggested()
        self.assertEqual(len(suggested), 1)  # none done yet: alphabetical first

        done_a = WorkoutSession.objects.create(person_routine=self.alice_routine)
        done_a.end_session()
        suggested, items = self.suggested()
        self.assertEqual(suggested, [self.second])
        self.assertEqual(items[0]['routine'], self.second)  # shown first

        done_b = WorkoutSession.objects.create(person_routine=self.second)
        done_b.end_session()
        WorkoutSession.objects.filter(pk=done_a.pk).update(start_time=timezone.now() - timedelta(days=3))
        suggested, _ = self.suggested()
        self.assertEqual(suggested, [self.alice_routine])

    def test_running_session_is_suggested_to_continue(self):
        self.client.post(reverse('core:workout_start', kwargs={'pk': self.second.pk}))
        suggested, _ = self.suggested()
        self.assertEqual(suggested, [self.second])


class PauseTests(WorkoutTestMixin, BaseTestCase):
    def setUp(self):
        self.login(self.alice)

    def test_paused_time_is_not_counted(self):
        session, _ = self.begin()
        now = timezone.now()
        WorkoutSession.objects.filter(pk=session.pk).update(start_time=now - timedelta(minutes=30))
        session.refresh_from_db()
        self.assertAlmostEqual(session.elapsed_seconds(), 30 * 60, delta=3)

        response = self.client.post(reverse('core:workout_pause', kwargs={'pk': session.pk}))
        self.assertTrue(response.json()['paused'])
        # pretend the pause started 10 minutes ago
        WorkoutSession.objects.filter(pk=session.pk).update(paused_at=now - timedelta(minutes=10))
        session.refresh_from_db()
        self.assertAlmostEqual(session.elapsed_seconds(), 20 * 60, delta=3)  # frozen at pause time

        response = self.client.post(reverse('core:workout_resume', kwargs={'pk': session.pk}))
        self.assertFalse(response.json()['paused'])
        session.refresh_from_db()
        self.assertAlmostEqual(session.paused_seconds, 10 * 60, delta=3)

        self.client.post(reverse('core:workout_end', kwargs={'pk': session.pk}))
        session.refresh_from_db()
        self.assertEqual(session.duration_minutes, 20)

    def test_ending_while_paused_closes_the_pause(self):
        session, _ = self.begin()
        self.client.post(reverse('core:workout_pause', kwargs={'pk': session.pk}))
        self.client.post(reverse('core:workout_end', kwargs={'pk': session.pk}))
        session.refresh_from_db()
        self.assertIsNone(session.paused_at)
        self.assertTrue(session.is_completed)

    def test_pause_requires_post_and_ownership(self):
        session, _ = self.begin()
        url = reverse('core:workout_pause', kwargs={'pk': session.pk})
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(self.bob)
        self.assertEqual(self.client.post(url).status_code, 404)


class IdorTests(WorkoutTestMixin, BaseTestCase):
    """Alice must not be able to touch Bob's workout."""

    def setUp(self):
        self.client.force_login(self.bob)
        self.client.post(reverse('core:workout_start', kwargs={'pk': self.bob_routine.pk}))
        self.client.get(reverse('core:workout_execute', kwargs={'pk': self.bob_routine.pk}))
        self.bob_session = WorkoutSession.objects.get()
        self.bob_log = SessionExercise.objects.filter(session=self.bob_session).first()
        self.bob_set = self.bob_log.sets.first()
        self.login(self.alice)

    def test_cannot_start_execute_or_end_other_students_workout(self):
        self.assertEqual(self.client.post(
            reverse('core:workout_start', kwargs={'pk': self.bob_routine.pk})).status_code, 404)
        self.assertEqual(self.execute(self.bob_routine).status_code, 404)
        self.assertEqual(self.client.post(
            reverse('core:workout_end', kwargs={'pk': self.bob_session.pk})).status_code, 404)
        self.bob_session.refresh_from_db()
        self.assertFalse(self.bob_session.is_completed)

    def test_cannot_touch_other_students_sets_or_notes(self):
        self.assertEqual(post_json(self.client, self.set_url(self.bob_set), {'done': True, 'weight': '99'}).status_code, 404)
        self.bob_set.refresh_from_db()
        self.assertFalse(self.bob_set.is_done)
        self.assertIsNone(self.bob_set.weight)
        self.assertEqual(post_json(
            self.client, reverse('core:workout_exercise_notes', kwargs={'pk': self.bob_log.pk}),
            {'notes': 'x'}).status_code, 404)

    def test_cannot_start_inactive_routine(self):
        PersonRoutine.objects.filter(pk=self.alice_routine.pk).update(is_active=False)
        self.assertEqual(self.client.post(
            reverse('core:workout_start', kwargs={'pk': self.alice_routine.pk})).status_code, 404)

    def test_session_detail_access(self):
        url = reverse('core:session_detail', kwargs={'pk': self.bob_session.pk})
        self.assertEqual(self.client.get(url).status_code, 403)  # Alice
        self.login(self.bob)
        self.assertEqual(self.client.get(url).status_code, 200)  # owner
        self.login(self.trainer)
        self.assertEqual(self.client.get(url).status_code, 200)  # trainer
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)  # anonymous


class PwaTests(BaseTestCase):
    def test_manifest_and_service_worker_are_public(self):
        manifest = self.client.get(reverse('core:pwa_manifest'))
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest['Content-Type'], 'application/manifest+json')
        data = json.loads(manifest.content)
        self.assertEqual(data['display'], 'standalone')
        self.assertTrue(all(i['src'].startswith('/static/') for i in data['icons']))

        sw = self.client.get(reverse('core:service_worker'))
        self.assertEqual(sw.status_code, 200)
        self.assertEqual(sw['Service-Worker-Allowed'], '/')
        self.assertIn('javascript', sw['Content-Type'])
        self.assertEqual(self.client.get('/sw.js').status_code, 200)

        self.assertEqual(self.client.get(reverse('core:offline')).status_code, 200)

    def test_pages_link_the_manifest(self):
        self.assertContains(self.client.get(reverse('account_login')), 'rel="manifest"')
