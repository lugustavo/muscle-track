import unittest
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qs, urlparse

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import calendar_feed as cf
from core.models import Appointment, CalendarFeed, Person, ScheduleSlot

from .base import BaseTestCase

try:
    import icalendar
except ImportError:   # only used to double-check the format when it happens to be installed
    icalendar = None


def when(day, hour=9):
    return timezone.make_aware(datetime.combine(day, time(hour)))


class TextFormatTests(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(cf.escape('a, b; c\\d\ne'), 'a\\, b\\; c\\\\d\\ne')

    def test_short_lines_are_untouched(self):
        self.assertEqual(cf.fold('SUMMARY:Aula'), 'SUMMARY:Aula')

    def test_long_lines_are_folded_under_75_octets_without_splitting_characters(self):
        text = 'DESCRIPTION:' + 'ação ' * 60
        folded = cf.fold(text)
        parts = folded.split('\r\n')
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p.encode('utf-8')) <= 75 for p in parts), [len(p.encode()) for p in parts])
        self.assertTrue(all(p.startswith(' ') for p in parts[1:]))
        self.assertEqual(''.join([parts[0]] + [p[1:] for p in parts[1:]]), text)


class FeedContentTests(BaseTestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.alice_p, self.bob_p = self.alice.person, self.bob.person
        Person.objects.filter(pk=self.alice_p.pk).update(person_name='Alice Souza Lima', phone='(11) 98765-4321',
                                                          health_notes='JOELHO-SEGREDO', notes='NOTA-INTERNA')
        self.alice_p.refresh_from_db()
        self.feed = cf.get_or_create_feed(self.trainer)
        self.student_feed = cf.get_or_create_feed(self.alice)

    def appt(self, person, day=None, hour=9, **extra):
        return Appointment.objects.create(person=person, starts_at=when(day or self.today + timedelta(days=3), hour), **extra)

    def ics(self, feed, now=None):
        return cf.build_ics(feed, 'example.org', now=now, site_url='https://example.org')

    def test_calendar_envelope_and_line_endings(self):
        body = self.ics(self.feed)
        self.assertTrue(body.startswith('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n'))
        self.assertTrue(body.endswith('END:VCALENDAR\r\n'))
        self.assertNotIn('\n', body.replace('\r\n', ''))
        for line in ('METHOD:PUBLISH', 'X-WR-TIMEZONE:America/Sao_Paulo', 'X-PUBLISHED-TTL:PT1H'):
            self.assertIn(line, body)

    def test_event_times_are_utc_and_the_uid_is_stable(self):
        a = self.appt(self.alice_p, hour=9, duration_minutes=45)
        body = self.ics(self.feed)
        utc_start = a.starts_at.astimezone(cf.dt_timezone.utc)
        self.assertIn(f'DTSTART:{utc_start:%Y%m%dT%H%M%SZ}', body)
        self.assertIn(f'DTEND:{(utc_start + timedelta(minutes=45)):%Y%m%dT%H%M%SZ}', body)
        self.assertIn(f'UID:appointment-{a.pk}@example.org', body)
        self.assertEqual(self.ics(self.feed).count(f'UID:appointment-{a.pk}@'), 1)
        self.assertEqual(utc_start.hour, 12)    # 09:00 in São Paulo (UTC-3)

    def test_cancelled_and_rescheduled_classes_are_sent_as_cancelled(self):
        ok = self.appt(self.alice_p, hour=7)
        cancelled = self.appt(self.alice_p, hour=8, status='cancelled')
        moved = self.appt(self.alice_p, hour=10, status='rescheduled')
        body = self.ics(self.feed)
        events = {int(block.split('UID:appointment-')[1].split('@')[0]): block for block in body.split('BEGIN:VEVENT')[1:]}
        self.assertIn('STATUS:CONFIRMED', events[ok.pk])
        self.assertIn('STATUS:CANCELLED', events[cancelled.pk])
        self.assertIn('STATUS:CANCELLED', events[moved.pk])

    def test_sequence_follows_the_last_change(self):
        a = self.appt(self.alice_p, status='attended', status_changed_at=timezone.now())
        self.assertIn(f'SEQUENCE:{int(a.status_changed_at.timestamp())}', self.ics(self.feed))
        b = self.appt(self.alice_p, hour=11)
        self.assertIn('SEQUENCE:0', self.ics(self.feed))
        self.assertTrue(b.pk)

    @override_settings(CALENDAR_LOCATION='Rua A, 10; sala 2')
    def test_location_is_added_when_configured(self):
        self.appt(self.alice_p)
        self.assertIn('LOCATION:Rua A\\, 10\\; sala 2', self.ics(self.feed))
        self.assertIn('LOCATION:Rua A\\, 10\\; sala 2', self.ics(self.student_feed))   # the students' classes happen there too

    def test_no_location_line_without_the_setting(self):
        self.appt(self.alice_p)
        self.assertNotIn('LOCATION', self.ics(self.feed))

    def test_trainer_titles_follow_the_name_style(self):
        self.appt(self.alice_p, hour=9, kind='trial')
        self.assertIn('SUMMARY:Experimental · Alice\r\n', self.ics(self.feed))
        CalendarFeed.objects.filter(pk=self.feed.pk).update(name_style='full')
        self.assertIn('SUMMARY:Experimental · Alice Souza Lima\r\n', self.ics(CalendarFeed.objects.get(pk=self.feed.pk)))
        CalendarFeed.objects.filter(pk=self.feed.pk).update(name_style='none')
        body = self.ics(CalendarFeed.objects.get(pk=self.feed.pk))
        self.assertIn('SUMMARY:Experimental 09:00\r\n', body)
        self.assertNotIn('Alice', body.split('BEGIN:VEVENT')[1].split('DESCRIPTION')[0])

    def test_trainer_gets_notes_and_a_link_to_the_file(self):
        a = self.appt(self.alice_p, notes='trazer a faixa')
        body = self.ics(self.feed)
        self.assertIn('trazer a faixa', body.replace('\r\n ', ''))
        self.assertIn(f'https://example.org/students/{self.alice_p.pk}/', body.replace('\r\n ', ''))
        self.assertTrue(a.pk)

    def test_student_gets_only_his_own_classes_with_nothing_else(self):
        self.appt(self.alice_p, hour=9, notes='nota da aula')
        self.appt(self.bob_p, hour=10)
        body = self.ics(self.student_feed)
        self.assertEqual(body.count('BEGIN:VEVENT'), 1)
        self.assertIn('SUMMARY:Aula de treino\r\n', body)
        self.assertIn('X-WR-CALNAME:Minhas aulas', body)
        flat = body.replace('\r\n ', '')
        for forbidden in ('Bob', 'bob@', 'nota da aula', '/students/', 'Alice'):
            self.assertNotIn(forbidden, flat, forbidden)

    def test_nothing_confidential_in_any_feed(self):
        self.appt(self.alice_p)
        for feed in (self.feed, self.student_feed):
            flat = self.ics(feed).replace('\r\n ', '')
            for secret in ('JOELHO-SEGREDO', 'NOTA-INTERNA', '98765', 'alice@example.com', 'Souza Lima'):
                self.assertNotIn(secret, flat, secret)

    def test_window_of_dates(self):
        old = self.appt(self.alice_p, self.today - timedelta(days=cf.PAST_DAYS + 3))
        edge = self.appt(self.alice_p, self.today - timedelta(days=cf.PAST_DAYS - 1), hour=8)
        far = self.appt(self.alice_p, self.today + timedelta(days=cf.FUTURE_DAYS + 5))
        near = self.appt(self.alice_p, self.today + timedelta(days=cf.FUTURE_DAYS - 1), hour=8)
        body = self.ics(self.feed)
        for included in (edge, near):
            self.assertIn(f'UID:appointment-{included.pk}@', body)
        for excluded in (old, far):
            self.assertNotIn(f'UID:appointment-{excluded.pk}@', body)

    def test_fixed_times_become_events(self):
        day = self.today + timedelta(days=5)
        ScheduleSlot.objects.create(person=self.alice_p, weekday=day.weekday(), start_time=time(7), valid_from=self.today)
        body = self.ics(self.feed)
        self.assertGreaterEqual(body.count('BEGIN:VEVENT'), 15)     # about 17 weeks of one weekly class
        self.assertEqual(self.ics(self.student_feed).count('BEGIN:VEVENT'), body.count('BEGIN:VEVENT'))

    def test_query_count_is_constant(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as few:
            self.ics(self.feed)
        for i in range(20):
            self.appt(self.alice_p if i % 2 else self.bob_p, self.today + timedelta(days=i), hour=7 + i % 10)
        with CaptureQueriesContext(connection) as many:
            self.ics(self.feed)
        self.assertEqual(len(few), len(many))

    @unittest.skipUnless(icalendar, 'icalendar is not installed')
    def test_a_real_parser_reads_it(self):
        self.appt(self.alice_p, kind='makeup', notes='com acentuação; e vírgulas, ok')
        self.appt(self.alice_p, hour=14, status='cancelled')
        cal = icalendar.Calendar.from_ical(self.ics(self.feed))
        events = [c for c in cal.walk() if c.name == 'VEVENT']
        self.assertEqual(len(events), 2)
        first = events[0]
        self.assertEqual(str(first['SUMMARY']), 'Reposição · Alice')
        self.assertIn('com acentuação; e vírgulas, ok', str(first['DESCRIPTION']))
        self.assertEqual(first['DTSTART'].dt.utcoffset(), timedelta(0))
        self.assertEqual(str(events[1]['STATUS']), 'CANCELLED')


class TokenTests(BaseTestCase):
    def test_feed_is_created_once_per_account_with_a_long_token(self):
        first = cf.get_or_create_feed(self.trainer)
        again = cf.get_or_create_feed(self.trainer)
        self.assertEqual(first.pk, again.pk)
        self.assertGreaterEqual(len(first.token), 40)
        self.assertNotEqual(first.token, cf.get_or_create_feed(self.alice).token)
        self.assertEqual(CalendarFeed.objects.count(), 2)

    def test_feed_for_token(self):
        feed = cf.get_or_create_feed(self.alice)
        self.assertEqual(cf.feed_for_token(feed.token).pk, feed.pk)
        self.assertIsNone(cf.feed_for_token('nope'))
        Person.objects.filter(pk=self.alice.person.pk).update(is_active=False)
        self.assertIsNone(cf.feed_for_token(feed.token))
        Person.objects.filter(pk=self.alice.person.pk).update(is_active=True)
        self.alice.is_active = False
        self.alice.save()
        self.assertIsNone(cf.feed_for_token(feed.token))

    def test_trainer_who_is_no_longer_staff_loses_the_feed(self):
        feed = cf.get_or_create_feed(self.trainer)
        self.trainer.is_staff = False
        self.trainer.save()
        self.assertIsNone(cf.feed_for_token(feed.token))     # and has no student file either

    def test_rotating_invalidates_the_old_token(self):
        feed = cf.get_or_create_feed(self.alice)
        old = feed.token
        cf.rotate(feed)
        self.assertNotEqual(feed.token, old)
        self.assertIsNone(cf.feed_for_token(old))
        self.assertIsNotNone(cf.feed_for_token(feed.token))
        self.assertIsNotNone(feed.rotated_at)

    def test_google_template_link(self):
        a = Appointment.objects.create(person=self.alice.person, starts_at=when(date(2026, 10, 20), 9), duration_minutes=60)
        url = cf.google_template_url(a, 'Aula · Alice', '60 min\nlinha 2')
        parsed = urlparse(url)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ('https', 'calendar.google.com', '/calendar/render'))
        query = parse_qs(parsed.query)
        self.assertEqual(query['action'], ['TEMPLATE'])
        self.assertEqual(query['text'], ['Aula · Alice'])
        self.assertEqual(query['dates'], ['20261020T120000Z/20261020T130000Z'])
        self.assertEqual(query['details'], ['60 min\nlinha 2'])
        self.assertEqual(query['ctz'], ['America/Sao_Paulo'])
        self.assertNotIn(' ', url)


class FeedViewTests(BaseTestCase):
    def setUp(self):
        self.feed = cf.get_or_create_feed(self.alice)
        self.url = reverse('core:calendar_feed', kwargs={'token': self.feed.token})

    def test_public_no_login_with_private_headers(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/calendar'))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn(b'BEGIN:VCALENDAR', response.content)
        self.assertEqual(self.client.head(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_unknown_token_and_old_token_are_404(self):
        self.assertEqual(self.client.get(reverse('core:calendar_feed', kwargs={'token': 'x' * 43})).status_code, 404)
        cf.rotate(self.feed)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_the_last_fetch_is_recorded_but_not_on_every_request(self):
        self.assertIsNone(CalendarFeed.objects.get(pk=self.feed.pk).last_fetched_at)
        self.client.get(self.url)
        first = CalendarFeed.objects.get(pk=self.feed.pk).last_fetched_at
        self.assertIsNotNone(first)
        self.client.get(self.url)
        self.assertEqual(CalendarFeed.objects.get(pk=self.feed.pk).last_fetched_at, first)

    def test_the_token_never_reaches_other_pages_or_the_student_list(self):
        self.login(self.trainer)
        for name in ('core:home', 'core:student_list', 'core:agenda'):
            self.assertNotContains(self.client.get(reverse(name)), self.feed.token)


class SettingsPageTests(BaseTestCase):
    def setUp(self):
        self.url = reverse('core:calendar_settings')

    def test_trainer_page(self):
        self.login(self.trainer)
        response = self.client.get(self.url)
        feed = CalendarFeed.objects.get(user=self.trainer)
        self.assertContains(response, f'http://testserver/calendar/feed/{feed.token}.ics')
        self.assertContains(response, 'webcal://testserver/calendar/feed/')
        self.assertContains(response, 'addbyurl')
        self.assertContains(response, 'Como os alunos aparecem')
        self.assertContains(response, 'Ainda ninguém buscou este link')
        self.assertEqual(self.client.get(self.url).context['feed'].pk, feed.pk)   # reused, not recreated
        self.assertEqual(CalendarFeed.objects.filter(user=self.trainer).count(), 1)

    @override_settings(SITE_URL='https://treino.example.org/')
    def test_the_public_address_comes_from_site_url(self):
        self.login(self.trainer)
        feed_url = self.client.get(self.url).context['feed_url']
        self.assertTrue(feed_url.startswith('https://treino.example.org/calendar/feed/'))
        self.assertTrue(self.client.get(self.url).context['webcal_url'].startswith('webcal://treino.example.org/'))

    def test_student_page_says_what_google_receives(self):
        self.login(self.alice)
        response = self.client.get(self.url)
        self.assertContains(response, 'O que o Google recebe')
        self.assertNotContains(response, 'Como os alunos aparecem')
        self.assertContains(response, 'Aula de treino')

    def test_visitors_students_without_consent_and_accounts_without_a_file(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        from django.contrib.auth.models import User
        stranger = User.objects.create_user('stranger', 'stranger@example.com', 'pw')
        self.login(stranger)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        newbie = self._make_student('newbie', 'Newbie')[0]
        from core.models import ConsentRecord
        ConsentRecord.objects.filter(person=newbie.person).delete()
        self.login(newbie)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertIn('/consent/', self.client.get(self.url)['Location'])

    def test_rotate(self):
        self.login(self.alice)
        feed = cf.get_or_create_feed(self.alice)
        old = feed.token
        self.assertEqual(self.client.get(reverse('core:calendar_rotate')).status_code, 405)
        response = self.client.post(reverse('core:calendar_rotate'), follow=True)
        self.assertContains(response, 'Novo link gerado')
        feed.refresh_from_db()
        self.assertNotEqual(feed.token, old)
        self.assertEqual(self.client.get(reverse('core:calendar_feed', kwargs={'token': old})).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('core:calendar_rotate')).status_code, 302)   # login required

    def test_one_account_cannot_rotate_anothers_link(self):
        trainer_feed = cf.get_or_create_feed(self.trainer)
        self.login(self.alice)
        self.client.post(reverse('core:calendar_rotate'))
        trainer_feed.refresh_from_db()
        self.assertEqual(cf.feed_for_token(trainer_feed.token).pk, trainer_feed.pk)

    def test_name_style_only_for_the_trainer_and_only_valid_values(self):
        self.login(self.trainer)
        url = reverse('core:calendar_style')
        self.client.post(url, {'name_style': 'full'})
        self.assertEqual(CalendarFeed.objects.get(user=self.trainer).name_style, 'full')
        self.client.post(url, {'name_style': 'hacker'})
        self.assertEqual(CalendarFeed.objects.get(user=self.trainer).name_style, 'full')
        self.assertEqual(self.client.get(url).status_code, 405)
        self.login(self.alice)
        self.assertEqual(self.client.post(url, {'name_style': 'none'}).status_code, 403)

    def test_entry_points(self):
        self.login(self.trainer)
        self.assertContains(self.client.get(reverse('core:agenda')), reverse('core:calendar_settings'))
        self.login(self.alice)
        self.assertContains(self.client.get(reverse('core:my_profile')), reverse('core:calendar_settings'))

    def test_breadcrumb_by_role(self):
        self.login(self.trainer)
        html = self.client.get(self.url).content.decode()
        self.assertIn('Agenda', html[html.index('breadcrumb'):html.index('breadcrumb') + 600])
        self.login(self.alice)
        html = self.client.get(self.url).content.decode()
        self.assertIn('Meu perfil', html[html.index('breadcrumb'):html.index('breadcrumb') + 600])


class AgendaButtonTests(BaseTestCase):
    def test_each_class_has_an_add_to_google_link_titled_by_the_style(self):
        self.login(self.trainer)
        day = timezone.localdate() + timedelta(days=1)
        Appointment.objects.create(person=self.alice.person, starts_at=when(day, 9))
        html = self.client.get(reverse('core:agenda'), {'vista': 'dia', 'dia': day.isoformat()}).content.decode()
        self.assertIn('calendar.google.com/calendar/render?action=TEMPLATE', html)
        self.assertIn('text=Aula%20%C2%B7%20Alice', html)
        CalendarFeed.objects.create(user=self.trainer, token='t' * 43, name_style='none')
        html = self.client.get(reverse('core:agenda'), {'vista': 'dia', 'dia': day.isoformat()}).content.decode()
        self.assertIn('text=Aula%2009%3A00', html)
