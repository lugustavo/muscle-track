import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from core import workout_sessions
from core.models import SessionExercise, SessionSet, WorkoutSession
from core.workout_sessions import IDLE_MINUTES

from .base import BaseTestCase
from .test_workout_flow import WorkoutTestMixin, post_json


def ms(moment):
    return int(moment.timestamp() * 1000)


class WorkoutSyncTests(WorkoutTestMixin, BaseTestCase):
    """The device sends the whole workout it kept (core/workout_offline.py)."""

    def setUp(self):
        self.login(self.alice)
        self.url = reverse('core:workout_sync')
        self.uuid = str(uuid.uuid4())
        self.prx = list(self.alice_routine.exercises.order_by('order'))

    def make_idle(self, session):
        old = timezone.now() - timedelta(minutes=IDLE_MINUTES + 30)
        WorkoutSession.objects.filter(pk=session.pk).update(start_time=old)
        SessionSet.objects.filter(session_exercise__session=session).update(updated_at=old)

    def payload(self, **extra):
        now = timezone.now()
        data = {'uuid': self.uuid, 'routine': self.alice_routine.pk, 'started_at': ms(now - timedelta(minutes=50)),
                'exercises': [{'prx': self.prx[0].pk, 'notes': 'ombro estalou', 'sets': [
                    {'n': 1, 'weight': '40', 'reps': 10, 'done': True, 'at': ms(now - timedelta(minutes=40))},
                    {'n': 2, 'weight': '42,5', 'reps': 9, 'done': True}]}]}
        data.update(extra)
        return data

    def test_a_workout_started_offline_is_created_with_the_device_start_time(self):
        data = self.payload()
        response = post_json(self.client, self.url, data)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        session = WorkoutSession.objects.get(pk=body['session'])
        self.assertTrue(body['created'])
        self.assertEqual(str(session.client_uuid), self.uuid)
        self.assertAlmostEqual(session.start_time.timestamp() * 1000, data['started_at'], delta=2000)
        sets = {s.number: s for s in SessionSet.objects.filter(session_exercise__session=session, session_exercise__person_routine_exercise=self.prx[0])}
        self.assertEqual((sets[1].weight, sets[1].reps, sets[1].is_done), (40, 10, True))
        self.assertEqual(str(sets[2].weight), '42.50')
        self.assertIsNotNone(sets[1].completed_at)
        self.assertEqual(SessionExercise.objects.get(session=session, person_routine_exercise=self.prx[0]).notes, 'ombro estalou')

    def test_sending_it_again_changes_nothing(self):
        post_json(self.client, self.url, self.payload())
        again = post_json(self.client, self.url, self.payload())
        self.assertFalse(again.json()['created'])
        self.assertEqual(WorkoutSession.objects.count(), 1)
        self.assertEqual(SessionSet.objects.filter(session_exercise__session__client_uuid=self.uuid, is_done=True).count(), 2)

    def test_it_joins_the_session_started_online_and_overwrites_with_the_device_values(self):
        session, _ = self.begin()
        first = SessionSet.objects.filter(session_exercise__session=session).order_by('pk').first()
        post_json(self.client, self.set_url(first), {'weight': '10'})
        response = post_json(self.client, self.url, self.payload(session=session.pk))
        self.assertEqual(response.json()['session'], session.pk)
        self.assertEqual(WorkoutSession.objects.count(), 1)
        session.refresh_from_db()
        self.assertEqual(str(session.client_uuid), self.uuid)
        self.assertEqual(str(SessionSet.objects.get(session_exercise__session=session, session_exercise__person_routine_exercise=self.prx[0], number=1).weight), '40.00')

    def test_finishing_with_the_device_end_time_and_pauses(self):
        started = timezone.now() - timedelta(minutes=70)
        ended = started + timedelta(minutes=60)
        response = post_json(self.client, self.url, self.payload(
            started_at=ms(started), finish=True, ended_at=ms(ended), paused_seconds=600, notes='cansado'))
        body = response.json()
        self.assertTrue(body['finished'])
        self.assertEqual(body['summary_url'], reverse('core:workout_summary', kwargs={'pk': body['session']}))
        session = WorkoutSession.objects.get(pk=body['session'])
        self.assertTrue(session.is_completed)
        self.assertEqual(session.duration_minutes, 50)            # 60 min minus 10 paused
        self.assertEqual(session.notes, 'cansado')
        self.assertEqual(self.client.get(body['summary_url']).status_code, 200)

    def test_a_session_closed_for_being_idle_comes_back_when_the_offline_device_reports(self):
        session, _ = self.begin()
        self.make_idle(session)
        # something was logged, so closing keeps it (an empty one would be deleted)
        done = SessionSet.objects.filter(session_exercise__session=session).first()
        SessionSet.objects.filter(pk=done.pk).update(is_done=True, completed_at=timezone.now() - timedelta(minutes=IDLE_MINUTES + 20))
        self.assertEqual(workout_sessions.close_stale(self.alice.person), 1)
        session.refresh_from_db()
        self.assertTrue(session.is_completed and session.auto_closed)
        response = post_json(self.client, self.url, self.payload(session=session.pk, started_at=ms(timezone.now() - timedelta(minutes=95))))
        self.assertEqual(response.json()['session'], session.pk)
        session.refresh_from_db()
        self.assertFalse(session.is_completed)
        self.assertFalse(session.auto_closed)
        self.assertIsNone(session.end_time)
        self.assertEqual(WorkoutSession.objects.count(), 1)

    def test_an_idle_session_with_nothing_logged_is_deleted_and_the_device_recreates_it(self):
        session, _ = self.begin()
        self.make_idle(session)
        self.assertEqual(workout_sessions.close_stale(self.alice.person), 1)
        self.assertFalse(WorkoutSession.objects.filter(pk=session.pk).exists())
        body = post_json(self.client, self.url, self.payload(session=session.pk)).json()
        self.assertTrue(body['created'])
        self.assertEqual(SessionSet.objects.filter(session_exercise__session_id=body['session'], is_done=True).count(), 2)

    def test_a_workout_the_student_already_finished_is_not_reopened(self):
        session, _ = self.begin()
        self.client.post(reverse('core:workout_end', kwargs={'pk': session.pk}), {'notes': ''})
        post_json(self.client, self.url, self.payload(session=session.pk))   # unrelated finished session: a new workout
        self.assertEqual(WorkoutSession.objects.count(), 2)
        done = WorkoutSession.objects.get(pk=session.pk)
        self.assertTrue(done.is_completed)
        self.assertFalse(done.auto_closed)

    def test_the_device_clock_is_not_believed_when_absurd(self):
        future = timezone.now() + timedelta(days=3)
        old = timezone.now() - timedelta(days=9)
        body = post_json(self.client, self.url, self.payload(started_at=ms(future), finish=True, ended_at=ms(future))).json()
        session = WorkoutSession.objects.get(pk=body['session'])
        self.assertLess(abs((session.start_time - timezone.now()).total_seconds()), 10)
        self.assertLess(abs((session.end_time - timezone.now()).total_seconds()), 10)
        self.uuid = str(uuid.uuid4())
        body = post_json(self.client, self.url, self.payload(started_at=ms(old))).json()
        self.assertLess(abs((WorkoutSession.objects.get(pk=body['session']).start_time - timezone.now()).total_seconds()), 10)

    def test_invalid_data_is_refused(self):
        for bad in ({'uuid': 'not-a-uuid'}, {'exercises': [{'prx': 999999, 'sets': []}]},
                    {'exercises': [{'prx': self.prx[0].pk, 'sets': [{'n': 1, 'weight': '-5'}]}]},
                    {'exercises': [{'prx': self.prx[0].pk, 'sets': [{'n': 'x'}]}]},
                    {'exercises': 'oops'}, {'routine': 'abc'}, {'session': 'abc'}, {'exercises': ['x']}):
            response = post_json(self.client, self.url, self.payload(**bad))
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(WorkoutSession.objects.count(), 0)   # a refused sync leaves nothing behind
        self.assertEqual(post_json(self.client, self.url, 'not json', raw=True).status_code, 400)

    def test_another_students_routine_is_not_found(self):
        response = post_json(self.client, self.url, self.payload(routine=self.bob_routine.pk))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(WorkoutSession.objects.exists())

    def test_requires_a_student_and_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(post_json(self.client, self.url, self.payload()).status_code, 302)   # to the login
        self.login(self.trainer)
        self.assertIn(post_json(self.client, self.url, self.payload()).status_code, (302, 403))   # the trainer is not a student
        self.assertFalse(WorkoutSession.objects.exists())

    def test_the_session_is_kept_when_the_set_endpoint_is_used_online_as_before(self):
        session, _ = self.begin()
        one = SessionSet.objects.filter(session_exercise__session=session).first()
        response = post_json(self.client, self.set_url(one), {'done': True, 'weight': '33', 'reps': 8})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['done'], True)


class OfflinePagesTests(WorkoutTestMixin, BaseTestCase):
    """The screens and files that let a workout be started, continued and finished with no connection."""

    def setUp(self):
        self.login(self.alice)
        self.draft_url = reverse('core:workout_offline_start', kwargs={'pk': self.alice_routine.pk})

    def test_the_workout_screen_carries_what_the_script_needs(self):
        session, response = self.begin()
        html = response.content.decode()
        for marker in (f'data-session="{session.pk}"', f'data-routine="{self.alice_routine.pk}"', 'data-sync-url="/api/workout-sync/"',
                       'data-rendered=', 'data-paused-seconds=', 'data-prx=', 'data-n="1"', 'data-offline-done', 'data-draft-url='):
            self.assertIn(marker, html)
        self.assertNotIn('data-draft="true"', html)
        self.assertEqual(response.context['draft'], False)

    def test_the_draft_screen_is_the_same_screen_without_a_session(self):
        before = WorkoutSession.objects.count()
        response = self.client.get(self.draft_url)
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorkoutSession.objects.count(), before)           # opening it creates nothing on the server
        self.assertIn('data-draft="true"', html)
        self.assertIn('data-draft-note', html)
        self.assertIn('data-discard-draft', html)
        self.assertNotIn('/api/sets/', html)                                # no set has an address yet
        self.assertEqual(html.count('class="set-grid set-row'), 6)         # supino and crucifixo, 3 sets each

    def test_the_draft_starts_from_the_previous_workout_like_a_real_one(self):
        session, _ = self.begin()
        first = SessionSet.objects.filter(session_exercise__session=session).order_by('pk').first()
        post_json(self.client, self.set_url(first), {'done': True, 'weight': '47,5', 'reps': 8})
        self.client.post(reverse('core:workout_end', kwargs={'pk': session.pk}), {'notes': ''})
        response = self.client.get(self.draft_url)
        sets = response.context['exercises'][0]['sets']
        self.assertEqual(str(sets[0].weight), '47.50')

    def test_the_draft_is_only_for_the_students_own_active_routines(self):
        self.assertEqual(self.client.get(reverse('core:workout_offline_start', kwargs={'pk': self.bob_routine.pk})).status_code, 404)
        self.alice_routine.is_active = False
        self.alice_routine.save()
        self.assertEqual(self.client.get(self.draft_url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.draft_url).status_code, 302)

    def test_my_workouts_lists_what_can_be_started_offline(self):
        html = self.client.get(reverse('core:my_workouts')).content.decode()
        self.assertIn('data-offline-routines', html)
        self.assertIn(f'data-draft="{self.draft_url}"', html)
        self.assertIn('js/workout.js', html.split('data-assets=')[1].split('>')[0])
        self.assertIn('js/my_workouts_offline.js', html)

    def test_students_load_the_offline_script_on_every_page_and_the_trainer_does_not(self):
        self.assertIn('js/workout_offline.js', self.client.get(reverse('core:home')).content.decode())
        self.login(self.trainer)
        self.assertNotIn('js/workout_offline.js', self.client.get(reverse('core:home')).content.decode())

    def test_the_summary_tells_the_script_the_workout_arrived(self):
        session, _ = self.begin()
        self.client.post(reverse('core:workout_end', kwargs={'pk': session.pk}), {'notes': ''})
        html = self.client.get(reverse('core:workout_summary', kwargs={'pk': session.pk})).content.decode()
        self.assertIn(f'data-workout-summary data-session="{session.pk}"', html)

    def test_the_service_worker_keeps_only_the_workout_screens_and_forgets_them(self):
        sw = self.client.get(reverse('core:service_worker')).content.decode()
        self.assertEqual(self.client.get(reverse('core:service_worker'))['Content-Type'].split(';')[0], 'application/javascript')
        for rule in ("WORKOUT_PAGE = /^\\/my-workouts\\/\\d+\\/(execute|offline)\\/$/", 'WORKOUT_TTL', "'X-Workout-Prefetch'",
                     "url.pathname.endsWith('/accounts/logout/')", 'caches.delete(WORKOUT_CACHE)', "'/jsi18n/'", 'data-offline-copy'):
            self.assertIn(rule, sw)
        self.assertNotIn('{{', sw)

    def test_the_student_marker_exists_only_on_a_students_pages(self):
        """The service worker keeps a page only if it carries the marker, so nothing of a trainer is ever kept."""
        for name in ('core:home', 'core:my_workouts', 'core:my_agenda', 'core:my_history'):
            self.assertIn('data-student-app', self.client.get(reverse(name)).content.decode(), name)
        self.client.logout()
        self.assertNotIn('data-student-app', self.client.get(reverse('core:home')).content.decode())
        self.login(self.trainer)
        for name in ('core:home', 'core:student_list'):
            self.assertNotIn('data-student-app', self.client.get(reverse(name)).content.decode(), name)

    def test_the_service_worker_keeps_the_students_main_screens_only_without_a_query(self):
        sw = self.client.get(reverse('core:service_worker')).content.decode()
        for rule in ("STUDENT_PAGE = /^\\/(my-workouts\\/|my-agenda\\/|my-package\\/|my-history\\/)?$/", 'PAGE_TTL',
                     "body.includes('data-student-app')", '!url.search', 'data-cached-at'):
            self.assertIn(rule, sw)
        for private in ('my-profile', 'my-privacy', 'my-evolution', 'photo'):
            self.assertNotIn(private, sw.split('STUDENT_PAGE =')[1].split('\n')[0])

    def test_the_offline_scripts_warm_the_main_screens_and_tell_when_a_copy_is_old(self):
        js = (settings.BASE_DIR / 'static/js/workout_offline.js').read_text()
        for page in ("'/'", "'/my-workouts/'", "'/my-agenda/'", "'/my-package/'", "'/my-history/'"):
            self.assertIn(page, js)
        self.assertIn('navigator.connection.saveData', js)
        self.assertIn('cachedAt', (settings.BASE_DIR / 'static/js/pwa.js').read_text())

    def test_the_offline_page_offers_to_continue_or_start_a_workout(self):
        html = self.client.get(reverse('core:offline')).content.decode()
        for marker in ('id="continueLink"', 'id="startLinks"', 'mt-wo-active-v1', 'mt-wo-routines-v1', 'muscle-track-workout-v1'):
            self.assertIn(marker, html)
