"""The other automatic reminders: the short "starts soon" one before a class, the students' nudges (a new assessment,
a week without training) and the trainer's morning summary."""
from datetime import datetime, timedelta
from decimal import Decimal

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import notifications, reminders
from core.models import (
    Appointment, Assessment, Notice, NotificationPreference, Person, PersonRoutine, WorkoutSession,
)

from .base import BaseTestCase

CONFIG = dict(NOTIFICATIONS_ASYNC=False, REMINDER_CLASS_HOURS=24, REMINDER_CLASS_SOON_HOURS=2, REMINDER_REASSESSMENT_DAYS=45,
              REMINDER_INACTIVE_DAYS=7, REMINDER_DIGEST_HOUR=7, REMINDER_NUDGE_HOUR=10, SITE_URL='https://treino.example.com')

MORNING = timezone.make_aware(datetime(2026, 10, 20, 7, 30))      # a Tuesday, 07:30 local
LATE_MORNING = timezone.make_aware(datetime(2026, 10, 20, 10, 30))


def turn_on(user, **flags):
    NotificationPreference.objects.update_or_create(user=user, defaults={'email_enabled': True, **flags})


@override_settings(**CONFIG)
class ClassSoonTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        turn_on(self.alice)
        self.now = timezone.make_aware(datetime(2026, 10, 20, 6, 0))

    def appointment(self, hours, **extra):
        return Appointment.objects.create(person=self.person, starts_at=self.now + timedelta(hours=hours), **extra)

    def test_it_goes_once_to_a_class_in_the_next_hours(self):
        appt = self.appointment(1.5)
        self.assertEqual(reminders.send_class_soon_reminders(self.now), 1)
        self.assertEqual(mail.outbox[0].subject, 'Sua aula é daqui a pouco')
        self.assertIn('Sua aula começa hoje às 07:30.', mail.outbox[0].body)
        appt.refresh_from_db()
        self.assertEqual(appt.soon_reminder_sent_at, self.now)
        self.assertEqual(reminders.send_class_soon_reminders(self.now), 0)

    def test_too_far_too_close_and_past_classes_are_left_alone(self):
        for hours in (3, 0.2, -1):
            self.appointment(hours)
        self.assertEqual(reminders.send_class_soon_reminders(self.now), 0)

    def test_only_marked_classes(self):
        self.appointment(1.5, status='cancelled')
        self.assertEqual(reminders.send_class_soon_reminders(self.now), 0)

    def test_it_is_skipped_when_the_day_before_reminder_just_went_out(self):
        appt = self.appointment(1.5)
        Appointment.objects.filter(pk=appt.pk).update(reminder_sent_at=self.now - timedelta(minutes=5))
        self.assertEqual(reminders.send_class_soon_reminders(self.now), 0)
        appt.refresh_from_db()
        self.assertEqual(appt.soon_reminder_sent_at, self.now)       # marked, so it never comes back
        self.assertEqual(mail.outbox, [])

    def test_the_class_reminder_switch_silences_it(self):
        turn_on(self.alice, notify_class_reminder=False)
        self.appointment(1.5)
        reminders.send_class_soon_reminders(self.now)
        self.assertEqual(mail.outbox, [])

    @override_settings(REMINDER_CLASS_SOON_HOURS=0)
    def test_zero_turns_it_off(self):
        self.appointment(1.5)
        self.assertEqual(reminders.send_class_soon_reminders(self.now), 0)


@override_settings(**CONFIG)
class StudentNudgeTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.today = timezone.localdate(LATE_MORNING)

    def assess(self, days_ago, person=None):
        return Assessment.objects.create(person=person or self.person, date=self.today - timedelta(days=days_ago),
                                         weight_kg=Decimal('70'))

    def notices(self, kind):
        return list(Notice.objects.filter(user=self.alice, kind=kind))

    def test_an_overdue_assessment_reminds_the_student_once_a_month(self):
        self.assess(60)
        told = reminders.send_student_nudges(LATE_MORNING, self.today)
        self.assertEqual(told['reassessment'], 1)
        [notice] = self.notices('reassessment')
        self.assertIn('60 dias', notice.body)
        self.assertEqual(notice.path, reverse('core:my_evolution'))
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['reassessment'], 0)   # not again at once
        Notice.objects.update(created_at=LATE_MORNING - timedelta(days=31))
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['reassessment'], 1)

    def test_a_recent_or_missing_assessment_is_not_a_reason(self):
        self.assess(10)
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['reassessment'], 0)
        Assessment.objects.all().delete()
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['reassessment'], 0)    # never assessed: nothing to repeat

    def test_only_the_latest_assessment_counts(self):
        self.assess(200)
        self.assess(5)
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['reassessment'], 0)

    def test_the_switch_and_a_student_without_the_app_are_respected(self):
        self.assess(60)
        NotificationPreference.objects.create(user=self.alice, notify_reassessment=False)
        self.assertEqual(self.notices('reassessment'), [])
        reminders.send_student_nudges(LATE_MORNING, self.today)
        self.assertEqual(self.notices('reassessment'), [])
        NotificationPreference.objects.all().delete()
        Person.objects.filter(pk=self.person.pk).update(has_app=False)
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['reassessment'], 0)

    def test_it_only_goes_out_in_the_morning_window(self):
        self.assess(60)
        for hour in (6, 9, 12, 20):
            moment = timezone.make_aware(datetime(2026, 10, 20, hour, 0))
            self.assertEqual(reminders.send_student_nudges(moment, self.today), {'reassessment': 0, 'inactive': 0, 'birthday': 0}, hour)
        self.assertEqual(reminders.send_student_nudges(timezone.make_aware(datetime(2026, 10, 20, 11, 59)), self.today)['reassessment'], 1)

    def make_inactive(self, days):
        routine = PersonRoutine.objects.filter(person=self.person).first() or self.alice_routine
        routine.is_active = True
        routine.save()
        session = WorkoutSession.objects.create(person_routine=routine, is_completed=True)
        WorkoutSession.objects.filter(pk=session.pk).update(start_time=LATE_MORNING - timedelta(days=days))     # auto_now_add

    def test_a_week_without_training_nudges_the_student_weekly(self):
        self.make_inactive(9)
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['inactive'], 1)
        [notice] = self.notices('inactivity')
        self.assertIn('9 dias', notice.body)
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['inactive'], 0)
        Notice.objects.update(created_at=LATE_MORNING - timedelta(days=8))
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['inactive'], 1)

    def test_someone_who_trained_lately_is_left_alone(self):
        self.make_inactive(2)
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['inactive'], 0)

    @override_settings(REMINDER_INACTIVE_DAYS=0, REMINDER_REASSESSMENT_DAYS=0)
    def test_zero_turns_both_off(self):
        self.assess(90)
        self.make_inactive(30)
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today), {'reassessment': 0, 'inactive': 0, 'birthday': 0})


@override_settings(**CONFIG)
class DailyDigestTests(BaseTestCase):
    def setUp(self):
        self.today = timezone.localdate(MORNING)
        self.person = self.alice.person

    def notices(self):
        return list(Notice.objects.filter(user=self.trainer, kind='daily_digest'))

    def lesson(self, day_offset, hour, **extra):
        day = timezone.make_aware(datetime.combine(self.today + timedelta(days=day_offset), datetime.min.time())) + timedelta(hours=hour)
        extra.setdefault('person', self.person)
        return Appointment.objects.create(starts_at=day, **extra)

    def test_a_quiet_day_sends_nothing(self):
        self.assertEqual(reminders.send_daily_digest(MORNING, self.today), 0)
        self.assertEqual(self.notices(), [])

    def test_it_counts_todays_classes_and_names_the_first(self):
        self.lesson(0, 9)
        self.lesson(0, 8, person=self.bob.person)
        self.assertEqual(reminders.send_daily_digest(MORNING, self.today), 1)
        [notice] = self.notices()
        self.assertEqual(notice.title, 'Resumo do dia')
        self.assertIn('2 aulas hoje (a primeira às 08:00)', notice.body)
        self.assertEqual(notice.path, reverse('core:home'))

    def test_yesterdays_unmarked_classes_are_counted_but_marked_ones_are_not(self):
        self.lesson(-1, 9)                                  # still "marcada": nobody marked the attendance
        self.lesson(-1, 10, status='attended')
        reminders.send_daily_digest(MORNING, self.today)
        self.assertIn('1 aula de ontem sem presença marcada', self.notices()[0].body)

    def test_it_is_sent_once_a_day_and_only_in_the_morning_window(self):
        self.lesson(0, 9)
        self.assertEqual(reminders.send_daily_digest(timezone.make_aware(datetime(2026, 10, 20, 5, 0)), self.today), 0)
        self.assertEqual(reminders.send_daily_digest(MORNING, self.today), 1)
        self.assertEqual(reminders.send_daily_digest(MORNING + timedelta(minutes=30), self.today), 0)
        self.assertEqual(len(self.notices()), 1)
        Notice.objects.update(created_at=MORNING - timedelta(hours=21))
        self.assertEqual(reminders.send_daily_digest(MORNING, self.today), 1)

    def test_the_switch_silences_it_and_students_never_get_it(self):
        self.lesson(0, 9)
        NotificationPreference.objects.create(user=self.trainer, notify_digest=False)
        self.assertEqual(reminders.send_daily_digest(MORNING, self.today), 0)
        self.assertEqual(Notice.objects.filter(kind='daily_digest').count(), 0)
        NotificationPreference.objects.all().delete()
        reminders.send_daily_digest(MORNING, self.today)
        self.assertEqual(Notice.objects.filter(kind='daily_digest', user=self.alice).count(), 0)

    @override_settings(REMINDER_DIGEST_HOUR=-1)
    def test_minus_one_turns_it_off(self):
        self.lesson(0, 9)
        self.assertEqual(reminders.send_daily_digest(MORNING, self.today), 0)

    def test_it_carries_counts_only(self):
        self.lesson(0, 9)
        reminders.send_daily_digest(MORNING, self.today)
        notice = self.notices()[0]
        self.assertNotIn(self.person.person_name, f'{notice.title} {notice.body}')

    def test_the_other_items_show_when_they_exist(self):
        from django.contrib.auth.models import User
        user = User.objects.create_user('semconvite@example.com', 'semconvite@example.com')
        user.set_unusable_password()
        user.save()
        invite = Person.objects.create(user=user, person_name='Sem convite', is_active=True, has_app=True)
        Person.objects.filter(pk=invite.pk).update(created_at=MORNING - timedelta(days=5))
        old = Assessment.objects.create(person=self.person, date=self.today - timedelta(days=80))
        summary = reminders.digest_summary(MORNING, self.today)
        self.assertEqual((summary['invites'], summary['reassessment']), (1, 1))
        self.assertTrue(old.pk)

    def test_the_run_includes_the_new_jobs(self):
        self.lesson(0, 9)
        result = reminders.run(MORNING)
        self.assertEqual(result['digest'], 1)
        self.assertIn('soon', result)


class SettingsTests(BaseTestCase):
    def test_the_trainer_has_the_digest_switch_and_it_saves(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:notification_settings')).content.decode()
        self.assertIn('id="notify_digest"', page)
        self.assertNotIn('id="notify_reassessment"', page)
        self.client.post(reverse('core:notification_settings'), {'types': '1', 'notify_changes': 'on'})
        self.assertFalse(NotificationPreference.objects.get(user=self.trainer).notify_digest)

    def test_the_student_has_the_two_nudge_switches_and_they_save(self):
        self.login(self.alice)
        page = self.client.get(reverse('core:notification_settings')).content.decode()
        self.assertIn('id="notify_reassessment"', page)
        self.assertIn('id="notify_inactivity"', page)
        self.assertNotIn('id="notify_digest"', page)
        self.client.post(reverse('core:notification_settings'), {'types': '1', 'notify_changes': 'on', 'notify_inactivity': 'on'})
        pref = NotificationPreference.objects.get(user=self.alice)
        self.assertFalse(pref.notify_reassessment)
        self.assertTrue(pref.notify_inactivity)


class MessageTests(BaseTestCase):
    def test_the_kinds_have_their_switches(self):
        self.assertEqual(notifications.KIND_FLAG['daily_digest'], 'notify_digest')
        self.assertEqual(notifications.KIND_FLAG['reassessment'], 'notify_reassessment')
        self.assertEqual(notifications.KIND_FLAG['inactivity'], 'notify_inactivity')

    def test_the_digest_only_writes_what_is_not_zero(self):
        text = str(notifications.daily_digest({'classes': 0, 'payments': 3, 'inactive': 1}).body)
        self.assertEqual(text, '3 pagamentos em aberto · 1 aluno sem treinar')
