"""The working hours (the window the trainer takes classes in) and the forecast view of the agenda."""
from datetime import date, datetime, time, timedelta

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone, translation

from core import agenda
from core.forms import WorkingHoursForm
from core.models import Appointment, WorkingHours
from core.templatetags.calendar_tags import forecast_hours

from .base import BaseTestCase

MONDAY = date(2026, 10, 12)


def when(day, hour=9, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def hours(**days):
    """{weekday: (start, end)} from keyword days like mon=(6, 21)."""
    names = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
    return {names.index(k): (time(a), time(b)) for k, (a, b) in days.items()}


class OutsideHoursTests(BaseTestCase):
    def appt(self, day, hour, minutes=60):
        return Appointment(person=self.alice.person, starts_at=when(day, hour), duration_minutes=minutes)

    def outside(self, a, window):
        return agenda.outside_hours(a.starts_at, a.ends_at, window)

    def test_nothing_is_outside_when_no_hours_are_configured(self):
        self.assertFalse(self.outside(self.appt(MONDAY, 3), {}))

    def test_inside_the_window_is_fine_including_the_edges(self):
        window = hours(mon=(6, 21))
        self.assertFalse(self.outside(self.appt(MONDAY, 6), window))
        self.assertFalse(self.outside(self.appt(MONDAY, 20), window))      # ends at 21:00 sharp

    def test_before_after_and_crossing_the_end_are_outside(self):
        window = hours(mon=(6, 21))
        self.assertTrue(self.outside(self.appt(MONDAY, 5), window))
        self.assertTrue(self.outside(self.appt(MONDAY, 21), window))
        self.assertTrue(self.outside(self.appt(MONDAY, 20, 90), window))   # ends 21:30

    def test_a_day_without_a_window_is_a_day_off(self):
        self.assertTrue(self.outside(self.appt(MONDAY + timedelta(days=1), 9), hours(mon=(6, 21))))

    def test_a_class_that_runs_past_midnight_is_outside(self):
        self.assertTrue(self.outside(self.appt(MONDAY, 23, 120), hours(mon=(6, 23))))


class ForecastTests(BaseTestCase):
    def appt(self, day, hour, minutes=60, person=None, **extra):
        return Appointment(pk=extra.pop('pk', None), person=person or self.alice.person, starts_at=when(day, hour),
                           duration_minutes=minutes, **extra)

    def run_forecast(self, items, days=14, window=None, **kw):
        return agenda.forecast(items, MONDAY, days, MONDAY, window or {}, **kw)

    def test_counts_booked_time_and_the_span_of_each_day(self):
        result = self.run_forecast([self.appt(MONDAY, 9), self.appt(MONDAY, 10, 90), self.appt(MONDAY + timedelta(days=2), 7)])
        monday = result['days'][0]
        self.assertEqual((monday['classes'], monday['booked'], monday['first'], monday['last']),
                         (2, 150, time(9, 0), time(11, 30)))
        self.assertEqual(result['totals']['classes'], 3)
        self.assertEqual(result['totals']['booked'], 210)
        self.assertEqual(result['totals']['busy_days'], 2)

    def test_cancelled_and_rescheduled_classes_do_not_count(self):
        result = self.run_forecast([self.appt(MONDAY, 9, status=Appointment.Status.CANCELLED),
                                    self.appt(MONDAY, 10, status=Appointment.Status.RESCHEDULED), self.appt(MONDAY, 11)])
        self.assertEqual(result['days'][0]['classes'], 1)

    def test_days_are_grouped_by_week_with_totals(self):
        result = self.run_forecast([self.appt(MONDAY, 9), self.appt(MONDAY + timedelta(days=7), 9)], days=14)
        self.assertEqual([w['monday'] for w in result['weeks']], [MONDAY, MONDAY + timedelta(days=7)])
        self.assertEqual([w['classes'] for w in result['weeks']], [1, 1])
        self.assertEqual([len(w['days']) for w in result['weeks']], [7, 7])

    def test_a_forecast_that_starts_midweek_has_a_short_first_week(self):
        result = agenda.forecast([], MONDAY + timedelta(days=3), 7, MONDAY, {})
        self.assertEqual([len(w['days']) for w in result['weeks']], [4, 3])

    def test_free_time_and_occupancy_with_working_hours(self):
        window = hours(mon=(8, 12))                      # a 4 h window on Monday
        result = self.run_forecast([self.appt(MONDAY, 9), self.appt(MONDAY, 10)], days=7, window=window)
        monday = result['days'][0]
        self.assertEqual((monday['free'], monday['busy_pct'], monday['window']), (120, 50, (time(8), time(12))))
        self.assertEqual(result['totals']['busy_pct'], 50)
        self.assertEqual(result['totals']['free'], 120)                    # only Monday is a working day

    def test_a_class_half_outside_counts_only_the_part_inside(self):
        result = self.run_forecast([self.appt(MONDAY, 11, 120)], window=hours(mon=(8, 12)))
        monday = result['days'][0]
        self.assertEqual(monday['free'], 180)
        self.assertEqual(monday['outside'], 1)

    def test_days_off_and_outside_classes_are_flagged(self):
        window = hours(mon=(8, 12))
        result = self.run_forecast([self.appt(MONDAY + timedelta(days=1), 9)], window=window)
        tuesday = result['days'][1]
        self.assertTrue(tuesday['off'])
        self.assertEqual((tuesday['outside'], tuesday['free']), (1, None))
        self.assertEqual(result['totals']['outside'], 1)

    def test_no_hours_means_no_free_time_and_no_flags(self):
        result = self.run_forecast([self.appt(MONDAY, 3)])
        day = result['days'][0]
        self.assertEqual((day['free'], day['busy_pct'], day['off'], day['outside']), (None, None, False, 0))
        self.assertIsNone(result['totals']['busy_pct'])

    def test_overlapping_classes_flag_the_day(self):
        result = self.run_forecast([self.appt(MONDAY, 9, pk=1), self.appt(MONDAY, 9, 30, person=self.bob.person, pk=2)])
        self.assertTrue(result['days'][0]['conflict'])
        self.assertEqual(result['totals']['conflicts'], 1)

    def test_free_time_can_be_left_out(self):
        result = self.run_forecast([self.appt(MONDAY, 9)], window=hours(mon=(8, 12)), with_free=False)
        self.assertIsNone(result['days'][0]['free'])
        self.assertIsNone(result['totals']['busy_pct'])


class ForecastFilterTests(SimpleTestCase):
    def test_hours_are_written_short(self):
        self.assertEqual((forecast_hours(0), forecast_hours(60), forecast_hours(90), forecast_hours(125)),
                         ('0 h', '1 h', '1 h 30', '2 h 05'))
        self.assertEqual(forecast_hours(None), '')

    def test_forecast_days_fall_back_to_30(self):
        self.assertEqual([agenda.parse_forecast_days(v) for v in ('14', '30', '60', '7', '', None, 'x', '90')],
                         [14, 30, 60, 30, 30, 30, 30, 30])


class WorkingHoursFormTests(SimpleTestCase):
    def post(self, **fields):
        return WorkingHoursForm(fields)

    def test_marked_days_become_windows(self):
        form = self.post(on_0='on', start_0='06:00', end_0='21:00', on_5='on', start_5='08:00', end_5='12:00')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.windows, {0: (time(6), time(21)), 5: (time(8), time(12))})

    def test_the_end_must_be_after_the_start(self):
        translation.activate('pt-br')
        form = self.post(on_0='on', start_0='10:00', end_0='10:00')
        self.assertFalse(form.is_valid())
        self.assertIn('end_0', form.errors)
        self.assertIn('Segunda', form.errors['end_0'][0])

    def test_a_marked_day_needs_both_times(self):
        form = self.post(on_1='on', start_1='06:00')
        self.assertFalse(form.is_valid())
        self.assertIn('start_1', form.errors)

    def test_unmarked_days_ignore_their_times_and_nothing_marked_is_valid(self):
        form = self.post(on_0='', start_0='25:99', end_0='')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.windows, {})

    def test_the_saved_windows_fill_the_form(self):
        form = WorkingHoursForm.from_saved({2: (time(7), time(19))})
        rows = {r['weekday']: r for r in form.bound_rows()}
        self.assertTrue(rows[2]['on'].value())
        self.assertFalse(rows[0]['on'].value())
        self.assertEqual(rows[2]['start'].value(), time(7))


class WorkingHoursViewTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:agenda_work_hours')

    def test_the_page_lists_the_seven_days(self):
        page = self.client.get(self.url).content.decode()
        self.assertEqual(page.count('class="work-day"'), 7)
        self.assertNotIn('Remover o horário de trabalho', page)      # nothing saved yet

    def test_saving_replaces_the_windows(self):
        WorkingHours.objects.create(weekday=6, start=time(8), end=time(10))
        response = self.client.post(self.url, {'on_0': 'on', 'start_0': '06:00', 'end_0': '21:00',
                                               'on_4': 'on', 'start_4': '06:00', 'end_4': '18:00'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(list(WorkingHours.objects.values_list('weekday', 'start', 'end')),
                         [(0, time(6), time(21)), (4, time(6), time(18))])
        self.assertContains(self.client.get(self.url), 'Remover o horário de trabalho')

    def test_a_wrong_window_saves_nothing_and_says_why(self):
        WorkingHours.objects.create(weekday=0, start=time(6), end=time(21))
        response = self.client.post(self.url, {'on_2': 'on', 'start_2': '10:00', 'end_2': '09:00'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'o fim precisa ser depois do começo')
        self.assertEqual(list(WorkingHours.objects.values_list('weekday', flat=True)), [0])

    def test_clearing_turns_the_feature_off(self):
        WorkingHours.objects.create(weekday=0, start=time(6), end=time(21))
        self.client.post(self.url, {'action': 'clear'})
        self.assertEqual(WorkingHours.objects.count(), 0)

    def test_students_cannot_open_it(self):
        self.login(self.alice)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'action': 'clear'}).status_code, 403)


class ForecastViewTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:agenda')
        self.today = timezone.localdate()

    def appt(self, day, hour=9, person=None, **extra):
        return Appointment.objects.create(person=person or self.alice.person, starts_at=when(day, hour), **extra)

    def test_the_view_looks_30_days_ahead_by_default_and_has_a_selector(self):
        response = self.client.get(self.url, {'vista': 'previsao'})
        self.assertEqual(response.context['view'], 'previsao')
        self.assertEqual(sum(len(w['days']) for w in response.context['forecast']['weeks']), 30)
        self.assertEqual([(n, active) for n, _, active in response.context['forecast_choices']], [(14, False), (30, True), (60, False)])
        self.assertContains(response, 'data-forecast-totals')

    def test_the_length_comes_from_dias_and_bad_values_fall_back(self):
        counts = {}
        for value in ('14', '60', '7', 'x'):
            weeks = self.client.get(self.url, {'vista': 'previsao', 'dias': value}).context['forecast']['weeks']
            counts[value] = sum(len(w['days']) for w in weeks)
        self.assertEqual(counts, {'14': 14, '60': 60, '7': 30, 'x': 30})

    def test_it_shows_the_classes_of_the_period_and_links_each_day(self):
        tomorrow = self.today + timedelta(days=1)
        self.appt(tomorrow, 9)
        self.appt(tomorrow, 10)
        response = self.client.get(self.url, {'vista': 'previsao'})
        self.assertEqual(response.context['forecast']['totals']['classes'], 2)
        self.assertContains(response, f'data-forecast-day="{tomorrow.isoformat()}"')
        self.assertContains(response, f'vista=dia&dia={tomorrow.isoformat()}')

    def test_without_hours_it_invites_to_set_them(self):
        page = self.client.get(self.url, {'vista': 'previsao'}).content.decode()
        self.assertIn('data-forecast-no-hours', page)
        self.assertIn(reverse('core:agenda_work_hours'), page)

    def test_with_hours_it_shows_occupancy_and_free_time(self):
        WorkingHours.objects.bulk_create(WorkingHours(weekday=d, start=time(6), end=time(21)) for d in range(7))
        self.appt(self.today + timedelta(days=1), 9)
        response = self.client.get(self.url, {'vista': 'previsao'})
        page = response.content.decode()
        self.assertNotIn('data-forecast-no-hours', page)
        self.assertIn('livres', page)
        self.assertIsNotNone(response.context['forecast']['totals']['busy_pct'])

    def test_classes_outside_the_hours_are_called_out(self):
        WorkingHours.objects.bulk_create(WorkingHours(weekday=d, start=time(6), end=time(21)) for d in range(7))
        self.appt(self.today + timedelta(days=1), 22)
        page = self.client.get(self.url, {'vista': 'previsao'}).content.decode()
        self.assertIn('data-forecast-alerts', page)
        self.assertIn('1 aula fora do seu horário de trabalho', page)

    def test_the_student_filter_looks_at_only_that_student_and_skips_free_time(self):
        WorkingHours.objects.create(weekday=self.today.weekday(), start=time(6), end=time(21))
        day = self.today + timedelta(days=1)
        self.appt(day, 9, person=self.alice.person)
        self.appt(day, 10, person=self.bob.person)
        response = self.client.get(self.url, {'vista': 'previsao', 'aluno': self.alice.person.pk})
        self.assertEqual(response.context['forecast']['totals']['classes'], 1)
        self.assertIsNone(response.context['forecast']['totals']['busy_pct'])

    def test_navigation_moves_by_the_length_and_keeps_it(self):
        response = self.client.get(self.url, {'vista': 'previsao', 'dias': '14'})
        self.assertIn('dias=14', response.context['next_url'])
        self.assertIn((self.today + timedelta(days=14)).isoformat(), response.context['next_url'])
        self.assertIn((self.today - timedelta(days=14)).isoformat(), response.context['prev_url'])

    def test_the_view_is_remembered(self):
        response = self.client.get(self.url, {'vista': 'previsao'})
        self.assertEqual(response.cookies['agenda_vista'].value, 'previsao')
        self.assertEqual(self.client.get(self.url).context['view'], 'previsao')


class HoursWarningTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.day = timezone.localdate() + timedelta(days=(0 - timezone.localdate().weekday()) % 7 + 7)   # a future Monday

    def create(self, hour):
        return self.client.post(reverse('core:appointment_create'), {
            'person': self.alice.person.pk, 'date': self.day.isoformat(), 'time': f'{hour:02d}:00',
            'duration_minutes': 60, 'kind': 'regular'}, follow=True)

    def warned(self, response):
        return [str(m) for m in response.context['messages'] if 'horário de trabalho' in str(m) or 'dia de folga' in str(m)]

    def test_no_warning_without_configured_hours(self):
        self.assertEqual(self.warned(self.create(22)), [])

    def test_a_class_inside_the_hours_is_silent(self):
        WorkingHours.objects.create(weekday=0, start=time(6), end=time(21))
        self.assertEqual(self.warned(self.create(9)), [])

    def test_a_class_outside_the_hours_warns_with_the_window(self):
        WorkingHours.objects.create(weekday=0, start=time(6), end=time(21))
        [text] = self.warned(self.create(22))
        self.assertIn('06:00–21:00', text)
        self.assertEqual(Appointment.objects.count(), 1)        # a warning, never a block

    def test_a_class_on_a_day_off_says_so(self):
        WorkingHours.objects.create(weekday=2, start=time(6), end=time(21))
        [text] = self.warned(self.create(9))
        self.assertIn('dia de folga', text)

    def test_moving_a_class_reports_the_warning_in_the_json(self):
        WorkingHours.objects.create(weekday=0, start=time(6), end=time(21))
        appointment = Appointment.objects.create(person=self.alice.person, starts_at=when(self.day, 9))
        response = self.client.post(reverse('core:appointment_move', kwargs={'pk': appointment.pk}), {
            'date': self.day.isoformat(), 'time': '22:00'})
        self.assertIn('06:00–21:00', response.json()['hours_warning'])
        inside = Appointment.objects.create(person=self.bob.person, starts_at=when(self.day, 9))
        response = self.client.post(reverse('core:appointment_move', kwargs={'pk': inside.pk}), {
            'date': self.day.isoformat(), 'time': '10:00'})
        self.assertEqual(response.json()['hours_warning'], '')

    def test_week_cards_mark_a_class_outside_the_hours(self):
        WorkingHours.objects.create(weekday=0, start=time(6), end=time(21))
        Appointment.objects.create(person=self.alice.person, starts_at=when(self.day, 22))
        Appointment.objects.create(person=self.bob.person, starts_at=when(self.day, 9))
        page = self.client.get(reverse('core:agenda'), {'vista': 'dia', 'dia': self.day.isoformat()}).content.decode()
        self.assertEqual(page.count('data-outside-hours'), 1)

    def test_the_agenda_links_to_the_settings(self):
        self.assertContains(self.client.get(reverse('core:agenda')), 'data-work-hours-link')
