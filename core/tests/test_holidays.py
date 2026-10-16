from datetime import date, time, timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import holidays, package_schedule, packages
from core.agenda import ensure_appointments
from core.models import Appointment, Holiday

from .base import BaseTestCase
from .test_package_fill import FillBase

TUE = [1]
START = date(2026, 11, 24)        # a Tuesday: Dec 1 and Dec 8 2026 are Tuesdays and Portuguese holidays


class CalendarTests(BaseTestCase):
    def test_easter(self):
        self.assertEqual([holidays.easter(y) for y in (2025, 2026, 2027)],
                         [date(2025, 4, 20), date(2026, 4, 5), date(2027, 3, 28)])

    def test_portugal_2026(self):
        days = holidays.national(2026, 'PT')
        self.assertEqual(days[date(2026, 4, 3)], 'Sexta-feira Santa')
        self.assertEqual(days[date(2026, 6, 4)], 'Corpo de Deus')
        for day in (date(2026, 1, 1), date(2026, 4, 25), date(2026, 10, 5), date(2026, 12, 1), date(2026, 12, 25)):
            self.assertIn(day, days)
        self.assertEqual(len(days), 13)

    def test_brazil_2026_has_carnival_and_black_awareness_day(self):
        days = holidays.national(2026, 'BR')
        self.assertEqual(days[date(2026, 2, 16)], 'Carnaval (segunda)')
        self.assertIn(date(2026, 11, 20), days)
        self.assertNotIn(date(2026, 12, 1), days)

    def test_no_region_means_no_national_holidays(self):
        self.assertEqual(holidays.national(2026), {})          # the test base sets HOLIDAYS_REGION = ''

    @override_settings(HOLIDAYS_REGION='pt')
    def test_region_is_case_insensitive_and_follows_the_setting(self):
        self.assertIn(date(2026, 12, 25), holidays.national(2026, None))

    @override_settings(HOLIDAYS_REGION='PT')
    def test_between_merges_own_days_and_the_own_name_wins(self):
        Holiday.objects.create(date=date(2026, 12, 25), name='Estúdio fechado')
        Holiday.objects.create(date=date(2026, 12, 10), name='São Nicolau')
        found = holidays.between(date(2026, 12, 1), date(2026, 12, 31))
        self.assertEqual(found[date(2026, 12, 25)], 'Estúdio fechado')
        self.assertEqual(found[date(2026, 12, 10)], 'São Nicolau')
        self.assertIn(date(2026, 12, 8), found)
        self.assertNotIn(date(2026, 11, 30), found)

    @override_settings(HOLIDAYS_REGION='PT')
    def test_between_spanning_two_years(self):
        found = holidays.between(date(2026, 12, 20), date(2027, 1, 5))
        self.assertEqual(sorted(found), [date(2026, 12, 25), date(2027, 1, 1)])


@override_settings(HOLIDAYS_REGION='PT')
class PreviewTests(FillBase):
    def preview(self, **kw):
        args = dict(weekdays=TUE, start=START, skip=(), keep=())
        args.update(kw)
        return package_schedule.build_preview(self.person, self.plan(), args['weekdays'], args['start'], time(7, 0),
                                              60, 1, args['skip'], args['keep'])

    def test_holidays_are_left_out_and_the_next_date_takes_the_place(self):
        preview = self.preview()
        self.assertEqual([d.isoformat() for d in preview['new_dates']],
                         ['2026-11-24', '2026-12-15', '2026-12-22', '2026-12-29', '2027-01-05', '2027-01-12'])
        states = {r['date'].isoformat(): r['state'] for r in preview['rows']}
        self.assertEqual((states['2026-12-01'], states['2026-12-08']), ('holiday', 'holiday'))
        holiday_rows = [r for r in preview['rows'] if r['state'] == 'holiday']
        self.assertEqual([r['holiday'] for r in holiday_rows], ['Restauração da Independência', 'Imaculada Conceição'])
        self.assertEqual(preview['count'], 6)

    def test_trainer_can_keep_a_holiday(self):
        preview = self.preview(keep={date(2026, 12, 1)})
        self.assertEqual(preview['new_dates'][1], date(2026, 12, 1))
        self.assertEqual([r['state'] for r in preview['rows']][:3], ['new', 'new', 'holiday'])
        self.assertTrue(preview['rows'][1]['kept'])

    def test_an_explicit_skip_on_a_holiday_is_still_a_skip(self):
        preview = self.preview(skip={date(2026, 12, 1)})
        self.assertEqual(next(r for r in preview['rows'] if r['date'] == date(2026, 12, 1))['state'], 'skipped')

    def test_own_holiday_is_skipped_too(self):
        Holiday.objects.create(date=date(2026, 12, 15), name='Recesso')
        preview = self.preview()
        self.assertNotIn(date(2026, 12, 15), preview['new_dates'])
        self.assertEqual(next(r for r in preview['rows'] if r['date'] == date(2026, 12, 15))['holiday'], 'Recesso')

    @override_settings(HOLIDAYS_REGION='')
    def test_without_region_nothing_is_skipped(self):
        self.assertEqual(self.preview()['new_dates'][1], date(2026, 12, 1))

    def test_fill_does_not_create_classes_on_holidays_and_remembers_them(self):
        result = package_schedule.fill_series(self.person, self.plan(), TUE, START, time(7, 0))
        days = sorted(timezone.localtime(a.starts_at).date() for a in Appointment.objects.filter(person=self.person))
        self.assertNotIn(date(2026, 12, 1), days)
        self.assertNotIn(date(2026, 12, 8), days)
        slot = result['slots'][0]
        self.assertEqual(slot.skipped_dates, ['2026-12-01', '2026-12-08'])

    def test_the_calendar_does_not_bring_the_holiday_class_back(self):
        package_schedule.fill_series(self.person, self.plan(), TUE, START, time(7, 0))
        before = Appointment.objects.filter(person=self.person).count()
        ensure_appointments(date(2026, 11, 20), date(2027, 1, 31), date(2026, 11, 20))
        self.assertEqual(Appointment.objects.filter(person=self.person).count(), before)

    def test_kept_holiday_is_created_and_not_remembered_as_skipped(self):
        result = package_schedule.fill_series(self.person, self.plan(), TUE, START, time(7, 0), keep={date(2026, 12, 1)})
        days = {timezone.localtime(a.starts_at).date() for a in Appointment.objects.filter(person=self.person)}
        self.assertIn(date(2026, 12, 1), days)
        self.assertEqual(result['slots'][0].skipped_dates, ['2026-12-08'])

    def test_top_up_skips_holidays(self):
        package_schedule.fill_series(self.person, self.plan(), TUE, START, time(7, 0))
        last = max(a.starts_at for a in Appointment.objects.filter(person=self.person))
        Appointment.objects.filter(person=self.person, starts_at=last).update(status='cancelled')
        Holiday.objects.create(date=date(2027, 1, 19), name='Recesso')
        package_schedule.top_up(self.person, self.plan(), now=timezone.make_aware(
            timezone.datetime(2026, 11, 20, 9, 0)))
        days = {timezone.localtime(a.starts_at).date() for a in Appointment.objects.filter(person=self.person)}
        self.assertNotIn(date(2027, 1, 19), days)


@override_settings(HOLIDAYS_REGION='PT')
class ScreenTests(BaseTestCase):
    def setUp(self):
        self.url = reverse('core:agenda_holidays')

    def test_only_the_trainer_gets_in(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.login(self.alice)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(reverse('core:agenda_holiday_add'), {'date': '2026-12-10', 'name': 'x'}).status_code, 403)

    def test_page_lists_national_and_own_days(self):
        self.login(self.trainer)
        Holiday.objects.create(date=timezone.localdate() + timedelta(days=3), name='São Nicolau')
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-national-holidays', html)
        self.assertIn('São Nicolau', html)
        self.assertIn('Natal', html)

    @override_settings(HOLIDAYS_REGION='')
    def test_page_without_region_explains_how_to_enable_it(self):
        self.login(self.trainer)
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-no-national', html)
        self.assertNotIn('data-national-holidays', html)

    def test_add_and_remove(self):
        self.login(self.trainer)
        response = self.client.post(reverse('core:agenda_holiday_add'), {'date': '2026-12-10', 'name': 'São Nicolau'})
        self.assertRedirects(response, self.url)
        holiday = Holiday.objects.get()
        self.assertEqual((holiday.date, holiday.name), (date(2026, 12, 10), 'São Nicolau'))
        self.client.post(reverse('core:agenda_holiday_delete', kwargs={'pk': holiday.pk}))
        self.assertFalse(Holiday.objects.exists())

    def test_same_day_twice_is_refused_with_a_message(self):
        self.login(self.trainer)
        Holiday.objects.create(date=date(2026, 12, 10), name='A')
        response = self.client.post(reverse('core:agenda_holiday_add'), {'date': '2026-12-10', 'name': 'B'}, follow=True)
        self.assertEqual(Holiday.objects.count(), 1)
        self.assertContains(response, 'já está na lista')

    def test_get_is_not_allowed_on_the_actions(self):
        self.login(self.trainer)
        self.assertEqual(self.client.get(reverse('core:agenda_holiday_add')).status_code, 405)

    def test_preview_fragment_marks_holidays_and_accepts_keep(self):
        self.login(self.trainer)
        from core import packages as pk
        from .test_package_fill import make_pack
        person = self.alice.person
        pk.sell(person, make_pack(6))
        url = reverse('core:slot_preview', kwargs={'pk': person.pk})
        query = {'weekdays': [1], 'start_time': '07:00', 'starts_on': '2026-11-24'}
        html = self.client.get(url, query).content.decode()
        self.assertIn('data-keep-date="2026-12-01"', html)
        self.assertIn('Restauração da Independência', html)
        self.assertIn('data-last="2027-01-12"', html)
        kept = self.client.get(url, {**query, 'keep': ['2026-12-01']}).content.decode()
        self.assertIn('data-last="2027-01-05"', kept)
        self.assertIn('data-keep-date="2026-12-01" checked', kept)      # stays tickable, to take it back

    def test_fill_view_honours_keep(self):
        self.login(self.trainer)
        from .test_package_fill import make_pack
        person = self.alice.person
        packages.sell(person, make_pack(6))
        self.client.post(reverse('core:slot_fill', kwargs={'pk': person.pk}),
                         {'weekdays': [1], 'start_time': '07:00', 'duration_minutes': 60, 'interval_weeks': 1,
                          'starts_on': '2026-11-24', 'keep': ['2026-12-01']})
        days = {timezone.localtime(a.starts_at).date() for a in Appointment.objects.filter(person=person)}
        self.assertIn(date(2026, 12, 1), days)
        self.assertNotIn(date(2026, 12, 8), days)

    def test_links_to_the_page(self):
        self.login(self.trainer)
        self.assertContains(self.client.get(reverse('core:agenda')), self.url)
