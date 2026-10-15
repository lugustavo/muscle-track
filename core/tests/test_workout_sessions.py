from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from core import reminders, workout_sessions
from core.models import Person, SessionSet, StudentEvent, WorkoutSession
from core.today import student_today
from core.workout_sessions import IDLE_MINUTES, MAX_SESSION_MINUTES

from .base import BaseTestCase
from .test_workout_flow import WorkoutTestMixin, post_json


class SessionBase(WorkoutTestMixin, BaseTestCase):
    def setUp(self):
        self.login(self.alice)
        self.person = self.alice.person

    def running(self, started_ago=timedelta(minutes=5)):
        session, _ = self.begin()
        started = timezone.now() - started_ago
        WorkoutSession.objects.filter(pk=session.pk).update(start_time=started)
        # the sets were created when the page opened: they date from the start too
        SessionSet.objects.filter(session_exercise__session=session).update(updated_at=started)
        return WorkoutSession.objects.get(pk=session.pk)

    def mark_done(self, session, ago=timedelta(minutes=1)):
        done = SessionSet.objects.filter(session_exercise__session=session).first()
        post_json(self.client, self.set_url(done), {'done': True, 'weight': '20', 'reps': 10})
        when = timezone.now() - ago
        SessionSet.objects.filter(pk=done.pk).update(completed_at=when, updated_at=when)
        return when


class IdleRuleTests(SessionBase):
    def test_recent_session_is_left_alone(self):
        session = self.running(timedelta(minutes=30))
        self.assertFalse(workout_sessions.is_stale(session))
        self.assertEqual(workout_sessions.close_stale(self.person), 0)
        self.assertTrue(WorkoutSession.objects.filter(pk=session.pk, is_completed=False).exists())

    def test_idle_session_with_nothing_logged_is_dropped(self):
        session = self.running(timedelta(minutes=IDLE_MINUTES + 5))
        self.assertEqual(workout_sessions.close_stale(self.person), 1)
        self.assertFalse(WorkoutSession.objects.filter(pk=session.pk).exists())

    def test_activity_keeps_a_long_session_alive(self):
        session = self.running(timedelta(minutes=100))      # started long ago, but a set was marked a moment ago
        self.mark_done(session, timedelta(minutes=2))
        self.assertFalse(workout_sessions.is_stale(session))

    def test_editing_a_weight_counts_as_activity(self):
        session = self.running(timedelta(minutes=100))
        SessionSet.objects.filter(session_exercise__session=session).update(
            updated_at=timezone.now() - timedelta(minutes=IDLE_MINUTES + 10))
        self.assertTrue(workout_sessions.is_stale(session))
        edited = SessionSet.objects.filter(session_exercise__session=session).first()
        post_json(self.client, self.set_url(edited), {'weight': '25'})
        self.assertFalse(workout_sessions.is_stale(session))

    def test_idle_session_with_sets_is_finished_at_the_last_set(self):
        session = self.running(timedelta(minutes=100))
        when = self.mark_done(session, timedelta(minutes=IDLE_MINUTES + 10))
        self.assertEqual(workout_sessions.close_stale(self.person), 1)
        session.refresh_from_db()
        self.assertTrue(session.is_completed)
        self.assertEqual(session.end_time, when)
        self.assertEqual(session.duration_minutes, (when - session.start_time).seconds // 60)

    def test_comment_alone_keeps_the_session(self):
        session = self.running(timedelta(minutes=IDLE_MINUTES + 5))
        WorkoutSession.objects.filter(pk=session.pk).update(notes='Dor no ombro')
        workout_sessions.close_stale(self.person)
        session.refresh_from_db()
        self.assertTrue(session.is_completed)

    def test_hard_cap(self):
        session = self.running(timedelta(minutes=MAX_SESSION_MINUTES + 5))
        self.mark_done(session, timedelta(minutes=1))     # even with activity: nobody trains for 2 h non-stop
        self.assertTrue(workout_sessions.is_stale(session))

    def test_paused_workout_counts_from_the_pause(self):
        session = self.running(timedelta(minutes=100))
        WorkoutSession.objects.filter(pk=session.pk).update(paused_at=timezone.now() - timedelta(minutes=5))
        self.assertFalse(workout_sessions.is_stale(WorkoutSession.objects.get(pk=session.pk)))
        WorkoutSession.objects.filter(pk=session.pk).update(paused_at=timezone.now() - timedelta(minutes=IDLE_MINUTES + 5))
        self.assertTrue(workout_sessions.is_stale(WorkoutSession.objects.get(pk=session.pk)))

    def test_hoje_screen_no_longer_shows_an_idle_workout_as_running(self):
        self.running(timedelta(minutes=IDLE_MINUTES + 5))
        request = type('R', (), {})()
        request._today_rows = None
        today = student_today(request, self.person)
        self.assertNotEqual(today['state'], 'in_progress')
        self.assertFalse(WorkoutSession.objects.filter(is_completed=False).exists())

    def test_scheduler_run_sweeps_every_student(self):
        mine = self.running(timedelta(minutes=IDLE_MINUTES + 5))
        result = reminders.run()
        self.assertEqual(result['sessions'], 1)
        self.assertFalse(WorkoutSession.objects.filter(pk=mine.pk).exists())


class DeactivationTests(SessionBase):
    def change(self, status):
        self.person.set_status(status, actor=self.trainer)

    def test_former_student_loses_the_running_workout_even_if_recent(self):
        session = self.running()
        self.change('former')
        self.assertFalse(WorkoutSession.objects.filter(pk=session.pk).exists())     # nothing logged: dropped
        self.change('active')                                                         # reactivated
        self.assertFalse(WorkoutSession.objects.filter(is_completed=False).exists())  # nothing to resume

    def test_work_already_done_is_kept_as_a_finished_session(self):
        session = self.running(timedelta(minutes=20))
        when = self.mark_done(session, timedelta(minutes=3))
        self.change('paused')
        session.refresh_from_db()
        self.assertTrue(session.is_completed)
        self.assertEqual(session.end_time, when)
        self.assertEqual(SessionSet.objects.filter(session_exercise__session=session, is_done=True).count(), 1)

    def test_history_says_how_many_were_ended(self):
        self.running()
        self.change('former')
        event = StudentEvent.objects.filter(person=self.person, kind='status').latest('pk')
        self.assertEqual(event.changes['sessions_closed'], 1)

    def test_changing_between_active_statuses_touches_nothing(self):
        session = self.running()
        self.change('trial')
        self.assertTrue(WorkoutSession.objects.filter(pk=session.pk, is_completed=False).exists())

    def test_other_students_are_not_touched(self):
        other = WorkoutSession.objects.create(person_routine=self.bob_routine)
        self.change('former')
        self.assertTrue(WorkoutSession.objects.filter(pk=other.pk, is_completed=False).exists())

    def test_through_the_trainer_screen(self):
        session = self.running()
        self.login(self.trainer)
        self.client.post(reverse('core:student_status', kwargs={'pk': self.person.pk}), {'status': 'former', 'reason': 'x'})
        self.assertFalse(WorkoutSession.objects.filter(pk=session.pk).exists())
        self.assertEqual(Person.objects.get(pk=self.person.pk).status, 'former')
