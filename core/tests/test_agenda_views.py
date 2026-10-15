from datetime import date, datetime, time, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.agenda import (
    LIST_DAYS, add_months, month_cells, month_range, parse_day, parse_month, view_range, week_start,
)
from core.models import Appointment, ScheduleSlot

from .base import BaseTestCase

TODAY = date(2026, 10, 14)   # Wednesday


def when(day, hour=9):
    return timezone.make_aware(datetime.combine(day, time(hour)))


class CalendarMathTests(BaseTestCase):
    def test_parse_month_and_day_fall_back(self):
        self.assertEqual(parse_month('2026-03', TODAY), date(2026, 3, 1))
        for bad in (None, '', 'x', '2026', '2026-13', '2026-00', '2026-1-1-1'):
            self.assertEqual(parse_month(bad, TODAY), date(2026, 10, 1), bad)
        self.assertEqual(parse_day('2026-01-31', TODAY), date(2026, 1, 31))
        for bad in (None, '', 'x', '2026-02-30'):
            self.assertEqual(parse_day(bad, TODAY), TODAY, bad)

    def test_add_months_crosses_years(self):
        self.assertEqual(add_months(date(2026, 12, 1), 1), date(2027, 1, 1))
        self.assertEqual(add_months(date(2026, 1, 1), -1), date(2025, 12, 1))
        self.assertEqual(add_months(date(2026, 10, 1), 15), date(2028, 1, 1))

    def test_month_grid_is_whole_weeks(self):
        start, end = month_range(date(2026, 10, 1))   # Oct 1st is a Thursday; Oct 31 a Saturday
        self.assertEqual((start, end), (date(2026, 9, 28), date(2026, 11, 1)))
        self.assertEqual((end - start).days + 1, 35)
        start, end = month_range(date(2026, 8, 1))    # Aug 1st is a Saturday; Aug 31 a Monday
        self.assertEqual((start, end), (date(2026, 7, 27), date(2026, 9, 6)))
        self.assertEqual((end - start).days + 1, 42)
        for first in (date(2026, m, 1) for m in range(1, 13)):
            start, end = month_range(first)
            self.assertEqual(start.weekday(), 0)
            self.assertEqual(end.weekday(), 6)

    def test_view_range(self):
        self.assertEqual(view_range('semana', {}, TODAY)[1:], (date(2026, 10, 12), date(2026, 10, 18), date(2026, 10, 12)))
        self.assertEqual(view_range('dia', {'dia': '2026-10-20'}, TODAY)[1:3], (date(2026, 10, 20), date(2026, 10, 20)))
        self.assertEqual(view_range('lista', {}, TODAY)[1:3], (TODAY, TODAY + timedelta(days=LIST_DAYS - 1)))
        self.assertEqual(view_range('mes', {'mes': '2026-02'}, TODAY)[3], date(2026, 2, 1))

    def test_month_cells(self):
        a = Appointment(pk=1, person=self.alice.person, starts_at=when(date(2026, 10, 14)), status='attended')
        b = Appointment(pk=2, person=self.bob.person, starts_at=when(date(2026, 10, 14), 10), status='cancelled')
        weeks = month_cells(date(2026, 10, 1), {date(2026, 10, 14): [a, b]}, TODAY, {1})
        self.assertEqual(len(weeks), 5)
        self.assertTrue(all(len(w) == 7 for w in weeks))
        cell = next(c for w in weeks for c in w if c['date'] == date(2026, 10, 14))
        self.assertEqual((cell['total'], cell['live'], cell['attended'], cell['missed'], cell['conflict'], cell['is_today']),
                         (2, 1, 1, 0, True, True))
        outside = weeks[0][0]
        self.assertEqual((outside['date'], outside['in_month']), (date(2026, 9, 28), False))


class AgendaViewsTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:agenda')
        self.today = timezone.localdate()

    def appt(self, day, person=None, hour=9, **extra):
        return Appointment.objects.create(person=person or self.alice.person, starts_at=when(day, hour), **extra)

    def test_week_is_the_default(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['view'], 'semana')
        self.assertContains(response, 'data-agenda-view="semana"')
        self.assertContains(response, 'data-day-strip')

    def test_selector_marks_the_current_view(self):
        response = self.client.get(self.url, {'vista': 'mes'})
        links = {key: active for key, _, _, active in response.context['view_links']}
        self.assertEqual(links, {'semana': False, 'dia': False, 'mes': True, 'lista': False})
        html = response.content.decode()
        self.assertEqual(html.count('data-view='), 4)
        self.assertIn('aria-current="true" data-view="mes"', html)

    def test_invalid_view_falls_back_to_week(self):
        for bad in ('xyz', '', 'MES', '../etc'):
            self.assertEqual(self.client.get(self.url, {'vista': bad}).context['view'], 'semana', bad)

    def test_the_choice_is_remembered_in_a_cookie(self):
        self.assertNotIn('agenda_vista', self.client.get(self.url).cookies)
        response = self.client.get(self.url, {'vista': 'lista'})
        self.assertEqual(response.cookies['agenda_vista'].value, 'lista')
        self.assertEqual(self.client.get(self.url).context['view'], 'lista')       # next visit, no parameter
        self.assertEqual(self.client.get(self.url, {'vista': 'dia'}).context['view'], 'dia')   # explicit wins
        self.assertEqual(self.client.get(self.url, {'vista': 'bad'}).context['view'], 'dia')   # junk keeps the memory
        self.client.cookies['agenda_vista'] = 'hacked'
        self.assertEqual(self.client.get(self.url).context['view'], 'semana')

    def test_month_view(self):
        self.appt(date(2026, 3, 10))
        self.appt(date(2026, 3, 10), self.bob.person, hour=10, status='missed')
        self.appt(date(2026, 3, 12), status='cancelled')
        response = self.client.get(self.url, {'vista': 'mes', 'mes': '2026-03'})
        cells = {c['date']: c for week in response.context['weeks'] for c in week}
        self.assertEqual(cells[date(2026, 3, 10)]['live'], 2)
        self.assertEqual(cells[date(2026, 3, 12)]['live'], 0)
        self.assertEqual(cells[date(2026, 3, 12)]['total'], 1)
        html = response.content.decode()
        self.assertEqual(html.count('class="month-cell'), len(cells))
        self.assertIn('Março de 2026', html)
        self.assertIn('vista=dia&dia=2026-03-10', html)        # tapping a day opens it
        self.assertIn('month-chip s-missed', html)

    def test_month_view_lists_the_classes_below_the_grid(self):
        first = self.appt(date(2026, 3, 10), hour=8)
        self.appt(date(2026, 3, 10), self.bob.person, hour=10, status='missed')
        self.appt(date(2026, 3, 12), status='cancelled')
        self.appt(date(2026, 4, 1))      # next month's day shown in the last grid row: not listed
        response = self.client.get(self.url, {'vista': 'mes', 'mes': '2026-03'})
        self.assertEqual([d['date'] for d in response.context['month_days']], [date(2026, 3, 10), date(2026, 3, 12)])
        html = response.content.decode()
        self.assertEqual(html.count('data-month-class='), 3)
        listed = html[html.index('data-month-list'):]
        self.assertIn('08:00', listed)
        self.assertIn('Alice', listed)
        self.assertIn('Bob', listed)
        # the name opens the student's file on the Agenda tab
        link = reverse('core:student_detail', kwargs={'pk': self.alice.person.pk})
        self.assertIn(f'{link}?next=', listed)
        self.assertIn('#tab-agenda', listed)
        self.assertIn(f'data-month-class="{first.pk}"', listed)
        self.assertIn('Faltou', listed)

    def test_month_without_classes_says_so(self):
        response = self.client.get(self.url, {'vista': 'mes', 'mes': '2026-03'})
        self.assertContains(response, 'Nenhuma aula neste mês.')

    def test_month_navigation_links(self):
        response = self.client.get(self.url, {'vista': 'mes', 'mes': '2026-01'})
        self.assertIn('mes=2025-12', response.context['prev_url'])
        self.assertIn('mes=2026-02', response.context['next_url'])
        self.assertFalse(response.context['is_current'])
        self.assertTrue(self.client.get(self.url, {'vista': 'mes'}).context['is_current'])

    def test_day_view(self):
        day = self.today + timedelta(days=3)
        self.appt(day, hour=11)
        self.appt(day, self.bob.person, hour=8)
        self.appt(day + timedelta(days=1))
        response = self.client.get(self.url, {'vista': 'dia', 'dia': day.isoformat()})
        days = response.context['days']
        self.assertEqual(len(days), 1)
        self.assertEqual([timezone.localtime(a.starts_at).hour for a in days[0]['items']], [8, 11])
        self.assertIn(f'dia={(day + timedelta(days=1)).isoformat()}', response.context['next_url'])
        self.assertIn(f'dia={(day - timedelta(days=1)).isoformat()}', response.context['prev_url'])

    def test_empty_day_offers_a_new_class(self):
        day = self.today + timedelta(days=5)
        response = self.client.get(self.url, {'vista': 'dia', 'dia': day.isoformat()})
        self.assertContains(response, 'Sem aulas neste dia')
        self.assertContains(response, f'date={day.isoformat()}')

    def test_list_skips_empty_days_and_stops_after_the_range(self):
        inside, outside = self.today + timedelta(days=2), self.today + timedelta(days=LIST_DAYS + 2)
        self.appt(inside)
        self.appt(outside)
        response = self.client.get(self.url, {'vista': 'lista'})
        self.assertEqual([d['date'] for d in response.context['days']], [inside])
        later = self.client.get(self.url, {'vista': 'lista', 'inicio': (self.today + timedelta(days=LIST_DAYS)).isoformat()})
        self.assertEqual([d['date'] for d in later.context['days']], [outside])
        self.assertContains(self.client.get(self.url, {'vista': 'lista', 'inicio': '2030-01-01'}), 'data-agenda-empty')

    def test_fixed_slots_appear_in_every_view(self):
        day = self.today + timedelta(days=2)
        ScheduleSlot.objects.create(person=self.alice.person, weekday=day.weekday(), start_time=time(7, 0))
        for params in ({'vista': 'dia', 'dia': day.isoformat()}, {'vista': 'lista'}, {'vista': 'semana'},
                       {'vista': 'mes', 'mes': day.strftime('%Y-%m')}):
            self.client.get(self.url, params)
            self.assertTrue(Appointment.objects.filter(person=self.alice.person, starts_at=when(day, 7)).exists(), params)

    def test_conflicts_are_flagged_in_the_day_view(self):
        day = self.today + timedelta(days=1)
        self.appt(day, hour=9)
        self.appt(day, self.bob.person, hour=9)
        response = self.client.get(self.url, {'vista': 'dia', 'dia': day.isoformat()})
        self.assertEqual(len(response.context['conflicts']), 2)
        self.assertContains(response, 'Sobreposta a outra aula')

    def test_attendance_buttons_return_to_the_same_view(self):
        day = self.today + timedelta(days=1)
        a = self.appt(day)
        response = self.client.get(self.url, {'vista': 'dia', 'dia': day.isoformat()})
        self.assertContains(response, f'action="/appointments/{a.pk}/status/?next=')
        self.assertContains(response, 'vista%3Ddia')   # the next= keeps the view

    def test_query_count_does_not_grow_with_classes(self):
        month = self.today.strftime('%Y-%m')
        with CaptureQueriesContext(connection) as few:
            self.client.get(self.url, {'vista': 'mes', 'mes': month})
        for i in range(25):
            self.appt(self.today.replace(day=1) + timedelta(days=i % 27), hour=7 + i % 10)
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url, {'vista': 'mes', 'mes': month})
        self.assertLessEqual(len(many), len(few) + 1)

    def test_only_the_trainer_sees_the_agenda(self):
        self.login(self.alice)
        self.assertEqual(self.client.get(self.url, {'vista': 'mes'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url, {'vista': 'mes'}).status_code, 302)

    def test_week_view_is_unchanged_for_the_mobile_day_strip(self):
        monday = week_start(self.today)
        self.appt(monday + timedelta(days=2))
        response = self.client.get(self.url, {'vista': 'semana', 'semana': monday.isoformat()})
        self.assertEqual(response.context['monday'], monday)
        self.assertEqual(len(response.context['days']), 7)
        self.assertEqual(response.content.decode().count('data-day-pane="'), 7)
