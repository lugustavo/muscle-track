from datetime import date, datetime, time, timedelta
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import packages, reminders
from decimal import Decimal

from core.models import Appointment, NotificationLog, NotificationPreference, Package, ScheduleSlot

from .base import BaseTestCase

CONFIG = dict(NOTIFICATIONS_ASYNC=False, REMINDER_CLASS_HOURS=24, REMINDER_PLAN_DAYS=3, SITE_URL='https://treino.example.com')

NOW = timezone.make_aware(datetime(2026, 10, 20, 3, 0))   # a Tuesday, 03:00 local


def starts(hours_from_now):
    return NOW + timedelta(hours=hours_from_now)


def turn_on(user, **flags):
    defaults = {'email_enabled': True}
    defaults.update(flags)
    NotificationPreference.objects.update_or_create(user=user, defaults=defaults)


@override_settings(**CONFIG)
class ClassReminderTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.person = self.alice.person
        turn_on(self.alice)

    def appointment(self, hours, **extra):
        return Appointment.objects.create(person=self.person, starts_at=starts(hours), **extra)

    def run_classes(self):
        return reminders.send_class_reminders(NOW)

    def test_reminder_goes_once_to_a_class_inside_the_window(self):
        appt = self.appointment(5)
        self.assertEqual(self.run_classes(), 1)
        self.assertEqual([m.subject for m in mail.outbox], ['Lembrete de aula'])
        self.assertIn('hoje às 08:00', mail.outbox[0].body)
        appt.refresh_from_db()
        self.assertEqual(appt.reminder_sent_at, NOW)
        self.assertEqual(self.run_classes(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(NotificationLog.objects.get().kind, 'class_reminder')

    def test_tomorrow_wording(self):
        self.appointment(20)   # 23:00 today is still "hoje"; 20 h from 03:00 = 23:00
        self.appointment(23, notes='x')   # 02:00 tomorrow
        self.run_classes()
        bodies = sorted(m.body.split('\n')[0] for m in mail.outbox)
        self.assertEqual(bodies, ['Sua aula é amanhã às 02:00.', 'Sua aula é hoje às 23:00.'])

    def test_outside_the_window_is_left_alone(self):
        far = self.appointment(30)
        soon = self.appointment(0.5, notes='soon')
        past = self.appointment(-2, notes='past')
        self.assertEqual(self.run_classes(), 0)
        for a in (far, soon, past):
            a.refresh_from_db()
            self.assertIsNone(a.reminder_sent_at)

    def test_only_marked_classes(self):
        for status in ('cancelled', 'rescheduled', 'attended', 'missed'):
            self.appointment(5 + len(status) / 100, status=status)
        self.assertEqual(self.run_classes(), 0)

    def test_no_channel_still_reminds_in_the_app_but_the_switch_is_respected(self):
        self.appointment(5)
        turn_on(self.alice, email_enabled=False, push_enabled=False)
        self.assertEqual(self.run_classes(), 1)                      # the notice centre needs no channel
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.alice.notices.filter(kind='class_reminder').count(), 1)
        self.appointment(6, notes='second')
        turn_on(self.alice, notify_class_reminder=False)
        self.assertEqual(self.run_classes(), 0)                      # that kind switched off: nothing at all
        self.assertEqual(self.alice.notices.count(), 1)
        turn_on(self.alice, notify_class_reminder=True)
        self.assertEqual(self.run_classes(), 1)

    def test_student_without_any_preference_gets_it_in_the_app_only(self):
        Appointment.objects.create(person=self.bob.person, starts_at=starts(5))
        self.assertEqual(self.run_classes(), 1)
        self.assertEqual(self.bob.notices.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_student_is_ignored(self):
        self.appointment(5)
        self.person.is_active = False
        self.person.save()
        self.assertEqual(self.run_classes(), 0)

    @override_settings(REMINDER_CLASS_HOURS=0)
    def test_can_be_turned_off(self):
        self.appointment(5)
        self.assertEqual(self.run_classes(), 0)

    @override_settings(REMINDER_CLASS_HOURS=48)
    def test_window_follows_the_setting(self):
        self.appointment(40)
        self.assertEqual(self.run_classes(), 1)

    def test_a_moved_class_is_reminded_again_at_its_new_time(self):
        from core.agenda import move_appointment
        old = self.appointment(5)
        self.run_classes()
        new = move_appointment(old, starts(6))
        self.assertIsNone(new.reminder_sent_at)
        self.assertEqual(self.run_classes(), 1)


@override_settings(**CONFIG)
class PlanReminderTests(BaseTestCase):
    TODAY = date(2026, 10, 20)

    def setUp(self):
        super().setUp()
        self.person = self.alice.person
        turn_on(self.alice)

    def expires(self, days):
        self.person.plan_expires_on = self.TODAY + timedelta(days=days)
        self.person.save()

    def test_reminder_inside_the_window_once(self):
        self.expires(3)
        self.assertEqual(reminders.send_plan_reminders(self.TODAY), 1)
        self.assertEqual(mail.outbox[0].subject, 'Seu pacote está vencendo')
        self.assertIn('vence em 3 dias', mail.outbox[0].body)
        self.assertEqual(reminders.send_plan_reminders(self.TODAY), 0)
        self.assertEqual(reminders.send_plan_reminders(self.TODAY + timedelta(days=1)), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_wording_for_today_and_tomorrow(self):
        self.expires(0)
        reminders.send_plan_reminders(self.TODAY)
        self.assertIn('vence hoje', mail.outbox[0].body)
        mail.outbox.clear()
        self.person.plan_reminder_for = None
        self.expires(1)
        reminders.send_plan_reminders(self.TODAY)
        self.assertIn('vence amanhã', mail.outbox[0].body)

    def test_amounts_never_appear(self):
        self.expires(2)
        self.person.negotiated_amount = 350
        self.person.save()
        reminders.send_plan_reminders(self.TODAY)
        self.assertNotIn('350', mail.outbox[0].body)

    def test_renewal_rearms_the_reminder(self):
        self.expires(2)
        reminders.send_plan_reminders(self.TODAY)
        self.expires(32)   # renewed: new expiry
        self.assertEqual(reminders.send_plan_reminders(self.TODAY), 0)
        self.assertEqual(reminders.send_plan_reminders(self.TODAY + timedelta(days=30)), 1)

    def test_outside_window_expired_or_unset(self):
        self.expires(10)
        self.assertEqual(reminders.send_plan_reminders(self.TODAY), 0)
        self.expires(-1)
        self.assertEqual(reminders.send_plan_reminders(self.TODAY), 0)
        self.person.plan_expires_on = None
        self.person.save()
        self.assertEqual(reminders.send_plan_reminders(self.TODAY), 0)

    def test_needs_the_switch_and_is_configurable(self):
        self.expires(2)
        turn_on(self.alice, notify_plan_expiry=False)
        self.assertEqual(reminders.send_plan_reminders(self.TODAY), 0)
        turn_on(self.alice, notify_plan_expiry=True)
        with override_settings(REMINDER_PLAN_DAYS=0):
            self.assertEqual(reminders.send_plan_reminders(self.TODAY), 0)
        self.assertEqual(reminders.send_plan_reminders(self.TODAY), 1)


@override_settings(**CONFIG)
class RunTests(BaseTestCase):
    def test_run_generates_fixed_time_classes_and_reminds(self):
        turn_on(self.alice)
        ScheduleSlot.objects.create(person=self.alice.person, weekday=1, start_time=time(8, 0), valid_from=date(2026, 10, 1))
        result = reminders.run(NOW)
        self.assertEqual(result['classes'], 1)
        self.assertTrue(Appointment.objects.filter(person=self.alice.person, starts_at=starts(5)).exists())
        self.assertEqual(reminders.run(NOW), {'classes': 0, 'plans': 0, 'packages': 0, 'backup': 0, 'sessions': 0})

    def test_command_prints_counts(self):
        out = StringIO()
        call_command('send_reminders', stdout=out)
        self.assertIn('0 lembrete(s) de aula, 0 de plano', out.getvalue())


@override_settings(**CONFIG)
class PreferenceSwitchTests(BaseTestCase):
    def test_changes_switch_silences_class_notices_but_not_the_test(self):
        from core import notifications
        turn_on(self.alice, notify_changes=False)
        notifications.notify([self.alice], notifications.class_cancelled(Appointment(
            person=self.alice.person, starts_at=starts(30))))
        self.assertEqual(len(mail.outbox), 0)
        notifications.notify([self.alice], notifications.test_message())
        self.assertEqual(len(mail.outbox), 1)

    def test_page_saves_the_switches_and_trainer_sees_only_changes(self):
        self.client.force_login(self.alice)
        page = self.client.get(reverse('core:notification_settings'))
        for name in ('notify_changes', 'notify_class_reminder', 'notify_plan_expiry'):
            self.assertContains(page, f'name="{name}"')
        self.client.post(reverse('core:notification_settings'), {'types': '1', 'notify_changes': 'on'})
        pref = NotificationPreference.objects.get(user=self.alice)
        self.assertEqual((pref.notify_changes, pref.notify_class_reminder, pref.notify_plan_expiry), (True, False, False))
        self.client.force_login(self.trainer)
        page = self.client.get(reverse('core:notification_settings'))
        self.assertContains(page, 'name="notify_changes"')
        self.assertNotContains(page, 'name="notify_class_reminder"')

    def test_a_post_without_the_marker_keeps_the_switches(self):
        turn_on(self.alice, notify_class_reminder=False)
        self.client.force_login(self.alice)
        self.client.post(reverse('core:notification_settings'), {'email_enabled': 'on'})
        self.assertFalse(NotificationPreference.objects.get(user=self.alice).notify_class_reminder)

    @override_settings(REMINDER_CLASS_HOURS=0, REMINDER_PLAN_DAYS=0)
    def test_page_hides_reminders_that_are_off(self):
        self.client.force_login(self.alice)
        page = self.client.get(reverse('core:notification_settings'))
        self.assertNotContains(page, 'name="notify_class_reminder"')
        self.assertContains(page, 'name="notify_plan_expiry"')   # still covers "classes running out"

    def test_trainer_has_the_renewals_switch_and_the_student_does_not(self):
        self.client.force_login(self.trainer)
        self.assertContains(self.client.get(reverse('core:notification_settings')), 'name="notify_renewals"')
        self.client.post(reverse('core:notification_settings'), {'types': '1', 'notify_changes': 'on'})
        self.assertFalse(NotificationPreference.objects.get(user=self.trainer).notify_renewals)
        self.client.force_login(self.alice)
        self.assertNotContains(self.client.get(reverse('core:notification_settings')), 'name="notify_renewals"')


TODAY = date(2026, 10, 20)


def finished(person, count, days_ago_first=10):
    """`count` attended classes, the first one `days_ago_first` days ago."""
    for n in range(count):
        day = TODAY - timedelta(days=days_ago_first - n)
        Appointment.objects.create(person=person, status='attended',
                                   starts_at=timezone.make_aware(datetime.combine(day, time(9))))
    packages.sync(person, TODAY)


@override_settings(**CONFIG)
class PackageReminderTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.person = self.alice.person
        turn_on(self.alice)
        turn_on(self.trainer)
        self.pack = Package.objects.create(name='Pacote 8', sessions=8, valid_days=60, pricing_mode='per_session',
                                           price_per_session=Decimal('55'))
        self.sold = packages.sell(self.person, self.pack)

    def run_packages(self, today=TODAY):
        return reminders.send_package_reminders(timezone.make_aware(datetime.combine(today, time(9))), today)

    def subjects(self):
        return sorted((m.to[0], m.subject) for m in mail.outbox)

    def test_nothing_when_there_is_plenty_left(self):
        finished(self.person, 3)
        self.assertEqual(self.run_packages(), 0)
        self.assertEqual(mail.outbox, [])

    def test_running_low_tells_the_student_and_the_trainer_once(self):
        finished(self.person, 6)   # 2 left
        self.assertEqual(self.run_packages(), 1)
        self.assertEqual(self.subjects(), [('alice@example.com', 'Seu pacote está acabando'),
                                           ('trainer@example.com', '1 pacote para renovar')])
        student = next(m for m in mail.outbox if m.to == ['alice@example.com'])
        self.assertIn('Restam 2 aulas no seu pacote', student.body)
        trainer = next(m for m in mail.outbox if m.to == ['trainer@example.com'])
        self.assertIn('Alice: 2 aulas restantes', trainer.body)
        self.assertEqual(self.run_packages(), 0)
        self.assertEqual(len(mail.outbox), 2)
        self.sold.refresh_from_db()
        self.assertIsNotNone(self.sold.low_notified_at)

    def test_one_class_left_wording(self):
        finished(self.person, 7)
        self.run_packages()
        student = next(m for m in mail.outbox if m.to == ['alice@example.com'])
        self.assertIn('Resta 1 aula no seu pacote', student.body)

    def test_expiring_goes_to_the_trainer_only_and_once_per_deadline(self):
        finished(self.person, 3, days_ago_first=55)   # deadline in 5 days
        self.assertEqual(self.run_packages(), 1)
        self.assertEqual(self.subjects(), [('trainer@example.com', '1 pacote para renovar')])
        body = mail.outbox[0].body
        self.assertIn('Alice: vence em 5 dias, 5 aulas restantes', body)
        self.assertEqual(self.run_packages(TODAY + timedelta(days=1)), 0)
        packages.extend(self.sold, 30, actor=self.trainer)   # a new deadline re-arms it
        self.assertEqual(self.run_packages(TODAY + timedelta(days=60)), 1)

    def test_expired_with_classes_left(self):
        finished(self.person, 3, days_ago_first=70)
        self.assertEqual(self.run_packages(), 1)
        self.assertIn('Alice: pacote vencido, 5 aulas sem usar', mail.outbox[0].body)
        self.assertEqual(self.run_packages(), 0)

    def test_one_notice_lists_every_package(self):
        bob_pack = packages.sell(self.bob.person, self.pack)
        turn_on(self.bob)
        finished(self.person, 7)
        finished(self.bob.person, 6)
        self.assertEqual(self.run_packages(), 2)
        trainer = [m for m in mail.outbox if m.to == ['trainer@example.com']]
        self.assertEqual(len(trainer), 1)
        self.assertEqual(trainer[0].subject, '2 pacotes para renovar')
        self.assertIn('Alice', trainer[0].body)
        self.assertIn('Bob', trainer[0].body)
        self.assertIsNotNone(bob_pack)

    def test_not_started_done_cancelled_and_queued_are_left_alone(self):
        self.assertEqual(self.run_packages(), 0)   # not started (8 left, no first class)
        finished(self.person, 8)                   # done
        self.assertEqual(self.run_packages(), 0)
        queued = packages.sell(self.person, self.pack)
        queued.sessions = 1
        queued.save()
        self.assertEqual(self.run_packages(), 0)   # current is the queued one: 1 class left but not started
        packages.cancel_package(queued, actor=self.trainer)
        self.assertEqual(self.run_packages(), 0)

    def test_switches_decide_who_hears_about_it(self):
        turn_on(self.alice, notify_plan_expiry=False)
        turn_on(self.trainer, notify_renewals=False)
        finished(self.person, 6)
        self.assertEqual(self.run_packages(), 1)
        self.assertEqual(mail.outbox, [])
        self.sold.refresh_from_db()
        self.assertIsNotNone(self.sold.low_notified_at)   # told is told: turning it on later does not replay the past

    def test_run_includes_the_package_count(self):
        finished(self.person, 6)
        result = reminders.run(timezone.make_aware(datetime.combine(TODAY, time(9))))
        self.assertEqual(result['packages'], 1)
