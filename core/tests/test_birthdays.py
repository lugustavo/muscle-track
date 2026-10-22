"""Birthdays: the dashboard card with a ready WhatsApp greeting, the student's own greeting from the studio and the count in
the trainer's morning summary."""
from datetime import date, datetime, timedelta
from urllib.parse import unquote

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import dashboard, notifications, reminders
from core.models import Notice, NotificationPreference, Person, WhatsAppTemplate

from .base import BaseTestCase

TODAY = date(2026, 10, 20)
LATE_MORNING = timezone.make_aware(datetime(2026, 10, 20, 10, 30))
CONFIG = dict(NOTIFICATIONS_ASYNC=False, REMINDER_NUDGE_HOUR=10, REMINDER_DIGEST_HOUR=7, REMINDER_INACTIVE_DAYS=0,
              REMINDER_REASSESSMENT_DAYS=0)


class NextBirthdayTests(BaseTestCase):
    def test_today_counts_and_a_passed_date_waits_for_next_year(self):
        self.assertEqual(dashboard.next_birthday(date(1990, 10, 20), TODAY), date(2026, 10, 20))
        self.assertEqual(dashboard.next_birthday(date(1990, 10, 21), TODAY), date(2026, 10, 21))
        self.assertEqual(dashboard.next_birthday(date(1990, 10, 19), TODAY), date(2027, 10, 19))

    def test_the_new_year_is_crossed(self):
        self.assertEqual(dashboard.next_birthday(date(1990, 1, 2), date(2026, 12, 28)), date(2027, 1, 2))
        self.assertEqual(dashboard.next_birthday(date(1990, 12, 30), date(2026, 12, 28)), date(2026, 12, 30))

    def test_29_february_is_celebrated_on_the_28th_in_common_years(self):
        born = date(2000, 2, 29)
        self.assertEqual(dashboard.next_birthday(born, date(2027, 1, 10)), date(2027, 2, 28))
        self.assertEqual(dashboard.next_birthday(born, date(2028, 1, 10)), date(2028, 2, 29))


class BirthdayRowsTests(BaseTestCase):
    def born(self, person, days_ahead, year=1990):
        day = TODAY + timedelta(days=days_ahead)
        Person.objects.filter(pk=person.pk).update(birth_date=day.replace(year=year))

    def test_the_next_seven_days_closest_first_with_the_age_they_turn(self):
        self.born(self.alice.person, 3, 1990)
        self.born(self.bob.person, 0, 2000)
        rows = dashboard.birthdays(TODAY)
        self.assertEqual([(r['student'], r['days'], r['turning']) for r in rows],
                         [(self.bob.person, 0, 26), (self.alice.person, 3, 36)])

    def test_beyond_a_week_inactive_and_without_a_date_are_left_out(self):
        self.born(self.alice.person, 9)
        self.born(self.bob.person, 2)
        Person.objects.filter(pk=self.bob.person.pk).update(is_active=False)
        self.assertEqual(dashboard.birthdays(TODAY), [])

    def test_the_greeting_has_the_first_name_and_uses_the_trainers_text(self):
        self.born(self.alice.person, 0)
        Person.objects.filter(pk=self.alice.person.pk).update(phone='11999998888')
        [row] = dashboard.birthdays(TODAY)
        self.assertIn('Parab', unquote(row['whatsapp']))
        self.assertIn(self.alice.person.person_name.split()[0], unquote(row['whatsapp']))
        WhatsAppTemplate.objects.create(kind='birthday', text='Feliz dia, {nome}!')
        [row] = dashboard.birthdays(TODAY)
        self.assertIn('Feliz dia, ' + self.alice.person.person_name.split()[0] + '!', unquote(row['whatsapp']))

    def test_ahead_zero_is_only_today(self):
        self.born(self.alice.person, 0)
        self.born(self.bob.person, 1)
        self.assertEqual([r['student'] for r in dashboard.birthdays(TODAY, ahead=0)], [self.alice.person])


class BirthdayCardTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        today = timezone.localdate()
        Person.objects.filter(pk=self.alice.person.pk).update(birth_date=today.replace(year=1991))

    def test_the_card_lists_the_birthday_with_the_whatsapp_button(self):
        page = self.client.get(reverse('core:home')).content.decode()
        self.assertIn('id="card-birthdays"', page)
        self.assertIn(f'data-birthday="{self.alice.person.pk}"', page)
        self.assertIn('Aniversariantes (7 dias)', page)

    def test_without_birthdays_there_is_no_card(self):
        Person.objects.update(birth_date=None)
        self.assertNotIn('id="card-birthdays"', self.client.get(reverse('core:home')).content.decode())

    def test_it_is_an_info_card_that_never_enters_the_attention_strip(self):
        context = self.client.get(reverse('core:home')).context['dashboard']
        self.assertNotIn('birthdays', [c['key'] for c in context['attention']])
        self.assertEqual(context['birthdays_today'], 1)
        self.assertLess(context['order']['birthdays'], 90)


@override_settings(**CONFIG)
class BirthdayGreetingTests(BaseTestCase):
    def setUp(self):
        self.today = timezone.localdate(LATE_MORNING)
        Person.objects.filter(pk=self.alice.person.pk).update(birth_date=self.today.replace(year=1992))

    def greetings(self):
        return list(Notice.objects.filter(user=self.alice, kind='birthday'))

    def test_the_student_gets_the_greeting_once_in_the_morning_window(self):
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['birthday'], 1)
        [notice] = self.greetings()
        self.assertEqual(notice.title, 'Feliz aniversário!')
        self.assertIn(self.alice.person.person_name.split()[0], notice.body)
        self.assertNotIn('34', notice.body)                    # the age is never written
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['birthday'], 0)

    def test_it_is_told_again_the_next_year(self):
        reminders.send_student_nudges(LATE_MORNING, self.today)
        Notice.objects.update(created_at=LATE_MORNING - timedelta(days=365))
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['birthday'], 1)

    def test_only_the_day_itself_and_only_in_the_window(self):
        Person.objects.filter(pk=self.alice.person.pk).update(birth_date=(self.today + timedelta(days=1)).replace(year=1992))
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['birthday'], 0)
        Person.objects.filter(pk=self.alice.person.pk).update(birth_date=self.today.replace(year=1992))
        early = timezone.make_aware(datetime(2026, 10, 20, 6, 0))
        self.assertEqual(reminders.send_student_nudges(early, self.today)['birthday'], 0)

    def test_the_switch_and_a_student_without_the_app_are_respected(self):
        NotificationPreference.objects.create(user=self.alice, notify_birthday=False)
        reminders.send_student_nudges(LATE_MORNING, self.today)
        self.assertEqual(self.greetings(), [])
        NotificationPreference.objects.all().delete()
        Person.objects.filter(pk=self.alice.person.pk).update(has_app=False)
        self.assertEqual(reminders.send_student_nudges(LATE_MORNING, self.today)['birthday'], 0)

    def test_the_trainer_does_not_get_the_students_greeting(self):
        reminders.send_student_nudges(LATE_MORNING, self.today)
        self.assertEqual(Notice.objects.filter(user=self.trainer, kind='birthday').count(), 0)


@override_settings(**CONFIG)
class DigestBirthdayTests(BaseTestCase):
    def test_the_morning_summary_counts_todays_birthdays(self):
        morning = timezone.make_aware(datetime(2026, 10, 20, 7, 30))
        Person.objects.filter(pk=self.alice.person.pk).update(birth_date=date(1990, 10, 20))
        Person.objects.filter(pk=self.bob.person.pk).update(birth_date=date(1985, 10, 20))
        self.assertEqual(reminders.send_daily_digest(morning, date(2026, 10, 20)), 1)
        self.assertIn('2 aniversariantes hoje', Notice.objects.get(kind='daily_digest', user=self.trainer).body)

    def test_the_text_without_birthdays_does_not_mention_them(self):
        self.assertNotIn('aniversari', str(notifications.daily_digest({'payments': 1}).body))


class SettingsTests(BaseTestCase):
    def test_the_student_can_turn_the_greeting_off(self):
        self.login(self.alice)
        self.assertIn('id="notify_birthday"', self.client.get(reverse('core:notification_settings')).content.decode())
        self.client.post(reverse('core:notification_settings'), {'types': '1', 'notify_changes': 'on'})
        self.assertFalse(NotificationPreference.objects.get(user=self.alice).notify_birthday)

    def test_the_trainer_has_no_such_switch(self):
        self.login(self.trainer)
        self.assertNotIn('id="notify_birthday"', self.client.get(reverse('core:notification_settings')).content.decode())

    def test_the_birthday_text_is_editable_with_the_other_whatsapp_messages(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:notification_settings')).content.decode()
        self.assertIn('Parabéns de aniversário', page)
