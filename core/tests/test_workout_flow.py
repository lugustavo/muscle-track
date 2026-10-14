import json

from django.urls import reverse

from core.models import PersonRoutine, SessionExercise, WorkoutSession

from .base import BaseTestCase


class WorkoutFlowTests(BaseTestCase):
    def setUp(self):
        self.login(self.alice)

    def start(self, routine=None):
        routine = routine or self.alice_routine
        return self.client.post(reverse('core:workout_start', kwargs={'pk': routine.pk}))

    def active_log(self):
        session = WorkoutSession.objects.get(person_routine=self.alice_routine, is_completed=False)
        self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))
        return session, SessionExercise.objects.filter(session=session).first()

    def post_weight(self, log, payload, raw=False):
        return self.client.post(
            reverse('core:exercise_update_weight', kwargs={'pk': log.pk}),
            data=payload if raw else json.dumps(payload),
            content_type='application/json',
        )

    def test_start_requires_post(self):
        response = self.client.get(reverse('core:workout_start', kwargs={'pk': self.alice_routine.pk}))
        self.assertEqual(response.status_code, 405)
        self.assertFalse(WorkoutSession.objects.exists())

    def test_full_flow(self):
        response = self.start()
        self.assertRedirects(response, reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))

        response = self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))
        self.assertEqual(response.status_code, 200)
        session = WorkoutSession.objects.get()
        self.assertEqual(SessionExercise.objects.filter(session=session).count(), 2)
        log = SessionExercise.objects.filter(session=session).first()

        toggle = reverse('core:exercise_toggle_completion', kwargs={'pk': log.pk})
        self.assertTrue(self.client.post(toggle).json()['is_completed'])
        log.refresh_from_db()
        self.assertIsNotNone(log.completed_at)
        self.assertFalse(self.client.post(toggle).json()['is_completed'])
        log.refresh_from_db()
        self.assertIsNone(log.completed_at)

        self.assertEqual(self.post_weight(log, {'weight': '42,5'}).status_code, 200)
        log.refresh_from_db()
        self.assertEqual(str(log.weight_used), '42.50')

        response = self.client.post(reverse('core:workout_end', kwargs={'pk': session.pk}))
        self.assertRedirects(response, reverse('core:my_workouts'))
        session.refresh_from_db()
        self.assertTrue(session.is_completed)

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

    def test_weight_validation(self):
        self.start()
        _, log = self.active_log()
        self.assertEqual(self.post_weight(log, {'weight': '0'}).status_code, 200)
        self.assertEqual(self.post_weight(log, {'weight': 0}).status_code, 200)
        for bad in ({'weight': 'abc'}, {'weight': '-5'}, {'weight': '1e9'}, {'weight': ''},
                    {'weight': 'NaN'}, {}, [1, 2]):
            with self.subTest(payload=bad):
                self.assertEqual(self.post_weight(log, bad).status_code, 400)
        self.assertEqual(self.post_weight(log, 'not json', raw=True).status_code, 400)

    def test_stale_session_is_closed_and_replaced(self):
        from datetime import timedelta
        from django.utils import timezone
        self.start()
        WorkoutSession.objects.update(start_time=timezone.now() - timedelta(minutes=121))
        self.start()
        self.assertEqual(WorkoutSession.objects.filter(is_completed=False).count(), 1)
        self.assertEqual(WorkoutSession.objects.filter(is_completed=True).count(), 1)

    def test_cannot_toggle_or_weigh_after_session_ended(self):
        self.start()
        session, log = self.active_log()
        session.end_session()
        self.assertEqual(self.client.post(
            reverse('core:exercise_toggle_completion', kwargs={'pk': log.pk})).status_code, 404)
        self.assertEqual(self.post_weight(log, {'weight': '10'}).status_code, 404)


class IdorTests(BaseTestCase):
    """Alice must not be able to touch Bob's workout."""

    def setUp(self):
        self.client.force_login(self.bob)
        self.client.post(reverse('core:workout_start', kwargs={'pk': self.bob_routine.pk}))
        self.client.get(reverse('core:workout_execute', kwargs={'pk': self.bob_routine.pk}))
        self.bob_session = WorkoutSession.objects.get()
        self.bob_log = SessionExercise.objects.filter(session=self.bob_session).first()
        self.login(self.alice)

    def test_cannot_start_execute_or_end_other_students_workout(self):
        self.assertEqual(self.client.post(
            reverse('core:workout_start', kwargs={'pk': self.bob_routine.pk})).status_code, 404)
        self.assertEqual(self.client.get(
            reverse('core:workout_execute', kwargs={'pk': self.bob_routine.pk})).status_code, 404)
        self.assertEqual(self.client.post(
            reverse('core:workout_end', kwargs={'pk': self.bob_session.pk})).status_code, 404)
        self.bob_session.refresh_from_db()
        self.assertFalse(self.bob_session.is_completed)

    def test_cannot_toggle_or_change_weight_of_other_students_exercise(self):
        self.assertEqual(self.client.post(
            reverse('core:exercise_toggle_completion', kwargs={'pk': self.bob_log.pk})).status_code, 404)
        response = self.client.post(
            reverse('core:exercise_update_weight', kwargs={'pk': self.bob_log.pk}),
            data=json.dumps({'weight': '99'}), content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.bob_log.refresh_from_db()
        self.assertFalse(self.bob_log.is_completed)
        self.assertIsNone(self.bob_log.weight_used)

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
