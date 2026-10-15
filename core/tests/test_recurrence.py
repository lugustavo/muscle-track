from datetime import date, datetime, time, timedelta

from django.urls import reverse
from django.utils import timezone

from core.agenda import ensure_appointments, on_week_cycle
from core.forms import RepeatForm, ScheduleSlotForm
from core.models import Appointment, ScheduleSlot
from core.recurrence import MAX_SPAN_DAYS, create_series, describe, end_date, end_series_from, occurrence_dates

from .base import BaseTestCase

MON = date(2026, 10, 12)   # a Monday


def when(day, hour=7):
    return timezone.make_aware(datetime.combine(day, time(hour)))


class EngineTests(BaseTestCase):
    def test_week_cycle(self):
        self.assertTrue(on_week_cycle(MON, MON, 1))
        self.assertTrue(on_week_cycle(MON + timedelta(days=9), MON, 1))
        self.assertTrue(on_week_cycle(MON, MON, 2))
        self.assertFalse(on_week_cycle(MON + timedelta(weeks=1), MON, 2))
        self.assertTrue(on_week_cycle(MON + timedelta(weeks=2, days=3), MON, 2))
        self.assertFalse(on_week_cycle(MON - timedelta(weeks=2), MON, 2))   # never before the start
        self.assertTrue(on_week_cycle(MON, MON, 0))                          # guard: 0 behaves as 1

    def test_occurrence_dates_with_several_weekdays_and_interval(self):
        dates = list(occurrence_dates(MON, {0, 2}, interval_weeks=2, limit=5))
        self.assertEqual(dates, [MON, MON + timedelta(days=2), MON + timedelta(weeks=2),
                                 MON + timedelta(weeks=2, days=2), MON + timedelta(weeks=4)])

    def test_occurrence_dates_start_mid_week_and_until(self):
        start = MON + timedelta(days=2)                        # Wednesday
        self.assertEqual(list(occurrence_dates(start, {0, 2}, 1, limit=3)),
                         [start, start + timedelta(days=5), start + timedelta(days=7)])
        self.assertEqual(list(occurrence_dates(MON, {1}, 1, until=MON + timedelta(days=15))),
                         [MON + timedelta(days=1), MON + timedelta(days=8), MON + timedelta(days=15)])
        with self.assertRaises(ValueError):
            list(occurrence_dates(MON, {0}))

    def test_occurrences_never_run_past_the_span_limit(self):
        got = list(occurrence_dates(MON, {0}, 8, limit=10_000))
        self.assertTrue(all((d - MON).days <= MAX_SPAN_DAYS for d in got))

    def test_end_date_variants(self):
        self.assertIsNone(end_date(MON, {0}, 1, 'never'))
        self.assertEqual(end_date(MON, {0}, 1, 'on', ends_on=date(2026, 12, 1)), date(2026, 12, 1))
        self.assertEqual(end_date(MON, {0}, 1, 'after', ends_after=4), MON + timedelta(weeks=3))
        self.assertEqual(end_date(MON, {0, 3}, 1, 'after', ends_after=3), MON + timedelta(weeks=1))
        self.assertEqual(end_date(MON, {0}, 2, 'after', ends_after=3), MON + timedelta(weeks=4))

    def test_describe(self):
        self.assertEqual(describe({0, 2}, time(7, 0), 1), 'seg, qua às 07:00, toda semana')
        self.assertEqual(describe({4}, time(18, 30), 2, date(2026, 12, 1)), 'sex às 18:30, a cada 2 semanas, até 01/12/2026')

    def test_slots_every_two_weeks_generate_alternate_weeks_only(self):
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        ScheduleSlot.objects.create(person=self.alice.person, weekday=0, start_time=time(7), valid_from=monday, interval_weeks=2)
        ensure_appointments(monday, monday + timedelta(weeks=5, days=6), today)
        days = sorted(timezone.localtime(a.starts_at).date() for a in Appointment.objects.filter(person=self.alice.person))
        expected = [monday + timedelta(weeks=w) for w in (0, 2, 4) if monday + timedelta(weeks=w) >= today - timedelta(days=14)]
        self.assertEqual(days, expected)

    def test_a_cancelled_class_is_not_generated_again(self):
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        slot = ScheduleSlot.objects.create(person=self.alice.person, weekday=0, start_time=time(7), valid_from=monday)
        ensure_appointments(monday, monday, today)
        appointment = Appointment.objects.get(slot=slot)
        Appointment.objects.filter(pk=appointment.pk).update(status='cancelled')
        ensure_appointments(monday, monday, today)
        self.assertEqual(Appointment.objects.filter(slot=slot).count(), 1)
        self.assertEqual(Appointment.objects.get(slot=slot).status, 'cancelled')

    def test_create_series_updates_an_existing_slot(self):
        person = self.alice.person
        slots, created = create_series(person, [0, 2], time(7), 60, MON)
        self.assertEqual((len(slots), created), (2, 2))
        ScheduleSlot.objects.filter(pk=slots[0].pk).update(is_active=False)
        slots, created = create_series(person, [0, 4], time(7), 45, MON + timedelta(weeks=1), 3, date(2027, 1, 1))
        self.assertEqual(created, 1)   # only Friday is new
        monday = ScheduleSlot.objects.get(person=person, weekday=0, start_time=time(7))
        self.assertEqual((monday.duration_minutes, monday.interval_weeks, monday.valid_until, monday.is_active),
                         (45, 3, date(2027, 1, 1), True))
        self.assertEqual(ScheduleSlot.objects.filter(person=person).count(), 3)


class FormTests(BaseTestCase):
    def slot_form(self, **data):
        base = {'start_time': '07:00', 'duration_minutes': 60, 'weekdays': ['0']}
        base.update(data)
        return ScheduleSlotForm(base)

    def test_needs_at_least_one_weekday(self):
        form = self.slot_form(weekdays=[])
        self.assertFalse(form.is_valid())
        self.assertIn('weekdays', form.errors)

    def test_single_weekday_parameter_still_works(self):
        form = self.slot_form(weekdays=[], weekday='3')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['all_weekdays'], [3])
        form = self.slot_form(weekdays=['0', '2'], weekday='2')
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['all_weekdays'], [0, 2])

    def test_interval_bounds_and_default(self):
        default = self.slot_form()
        self.assertTrue(default.is_valid(), default.errors)
        self.assertEqual(default.cleaned_data['interval_weeks'], 1)
        for bad in ('0', '9', '-1', 'x'):
            self.assertFalse(self.slot_form(interval_weeks=bad).is_valid(), bad)
        self.assertTrue(self.slot_form(interval_weeks='8').is_valid())

    def test_ending_rules(self):
        self.assertTrue(self.slot_form(ends='never').is_valid())
        self.assertIn('ends_on', self.slot_form(ends='on').errors)
        self.assertIn('ends_after', self.slot_form(ends='after').errors)
        for bad in ('0', '201'):
            self.assertFalse(self.slot_form(ends='after', ends_after=bad).is_valid(), bad)
        self.assertTrue(self.slot_form(ends='after', ends_after='12').is_valid())
        before = self.slot_form(ends='on', ends_on='2020-01-01', starts_on='2026-10-12')
        self.assertIn('ends_on', before.errors)
        too_far = self.slot_form(ends='on', ends_on='2035-01-01', starts_on='2026-10-12')
        self.assertIn('ends_on', too_far.errors)
        ok = self.slot_form(ends='on', ends_on='2026-12-31', starts_on='2026-10-12')
        self.assertTrue(ok.is_valid(), ok.errors)

    def test_repeat_form_is_ignored_when_not_enabled(self):
        form = RepeatForm({'rep-ends': 'on'}, prefix='rep')    # would be invalid if it counted
        self.assertTrue(form.is_valid())
        self.assertFalse(form.cleaned_data['enabled'])
        form = RepeatForm({'rep-enabled': 'on'}, prefix='rep')
        self.assertFalse(form.is_valid())
        self.assertIn('weekdays', form.errors)


class ViewsTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person
        self.today = timezone.localdate()

    def add_slot(self, **data):
        base = {'start_time': '07:00', 'duration_minutes': '60', 'weekdays': ['0', '2'], 'ends': 'never', 'interval_weeks': '1'}
        base.update(data)
        return self.client.post(reverse('core:slot_add', kwargs={'pk': self.person.pk}), base, follow=True)

    def test_student_file_creates_one_slot_per_weekday(self):
        response = self.add_slot(weekdays=['0', '2', '4'], interval_weeks='2', ends='after', ends_after='6')
        slots = ScheduleSlot.objects.filter(person=self.person).order_by('weekday')
        self.assertEqual([s.weekday for s in slots], [0, 2, 4])
        self.assertTrue(all(s.interval_weeks == 2 and s.valid_until is not None for s in slots))
        self.assertTrue(all(s.valid_until == slots[0].valid_until for s in slots))
        self.assertContains(response, 'Horário fixo: seg, qua, sex às 07:00, a cada 2 semanas, até')

    def test_invalid_series_creates_nothing(self):
        self.add_slot(weekdays=[])
        self.add_slot(ends='on')
        self.assertFalse(ScheduleSlot.objects.exists())

    def test_the_form_in_the_student_file(self):
        html = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).content.decode()
        self.assertIn('Repetição personalizada', html)
        self.assertEqual(html.count('name="weekdays"'), 7)
        for name in ('interval_weeks', 'starts_on', 'ends_on', 'ends_after'):
            self.assertIn(f'name="{name}"', html)
        self.assertIn('js/recurrence.js', html)

    def test_slot_list_shows_interval_and_end(self):
        self.add_slot(weekdays=['1'], interval_weeks='3', ends='on', ends_on=(self.today + timedelta(days=60)).isoformat())
        html = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).content.decode()
        self.assertIn('a cada 3 semanas', html)
        self.assertIn('até', html)

    def post_new_class(self, **extra):
        day = self.today + timedelta(days=(7 - self.today.weekday()) % 7 or 7)   # next Monday
        data = {'person': self.person.pk, 'date': day.isoformat(), 'time': '07:00', 'duration_minutes': '60', 'kind': 'regular'}
        data.update(extra)
        return day, self.client.post(reverse('core:appointment_create'), data, follow=True)

    def test_new_class_without_repeat_is_a_single_appointment(self):
        day, response = self.post_new_class()
        self.assertEqual(Appointment.objects.filter(person=self.person).count(), 1)
        self.assertFalse(ScheduleSlot.objects.exists())

    def test_new_class_with_repeat_creates_a_series(self):
        day, response = self.post_new_class(**{'rep-enabled': 'on', 'rep-weekdays': ['0', '3'], 'rep-interval_weeks': '1',
                                               'rep-ends': 'after', 'rep-ends_after': '4'})
        slots = ScheduleSlot.objects.filter(person=self.person).order_by('weekday')
        self.assertEqual([s.weekday for s in slots], [0, 3])
        self.assertEqual(slots[0].valid_from, day)
        self.assertEqual(slots[0].valid_until, day + timedelta(weeks=1, days=3))   # 4th class: Mon, Thu, Mon, Thu
        self.assertContains(response, 'Aulas repetidas criadas')
        times = sorted(timezone.localtime(a.starts_at).date() for a in Appointment.objects.filter(person=self.person))
        self.assertEqual(times, [day, day + timedelta(days=3), day + timedelta(weeks=1), day + timedelta(weeks=1, days=3)])

    def test_repeat_needs_regular_kind_and_valid_options(self):
        self.post_new_class(kind='trial', **{'rep-enabled': 'on', 'rep-weekdays': ['0']})
        self.post_new_class(**{'rep-enabled': 'on'})                                   # no weekday
        self.post_new_class(**{'rep-enabled': 'on', 'rep-weekdays': ['0'], 'rep-ends': 'on'})   # no end date
        self.assertFalse(ScheduleSlot.objects.exists())
        self.assertFalse(Appointment.objects.exists())

    def test_form_page_has_the_repeat_box(self):
        html = self.client.get(reverse('core:appointment_create')).content.decode()
        self.assertIn('data-repeat-box', html)
        self.assertIn('name="rep-enabled"', html)
        self.assertEqual(html.count('name="rep-weekdays"'), 7)
        self.assertIn('js/recurrence.js', html)
        reschedule = Appointment.objects.create(person=self.person, starts_at=when(self.today + timedelta(days=2)))
        page = self.client.get(reverse('core:appointment_reschedule', kwargs={'pk': reschedule.pk})).content.decode()
        self.assertNotIn('data-repeat-box', page)


class SeriesEditingTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person
        self.today = timezone.localdate()
        monday = self.today - timedelta(days=self.today.weekday()) + timedelta(weeks=1)
        self.slot = ScheduleSlot.objects.create(person=self.person, weekday=0, start_time=time(7), valid_from=monday)
        ensure_appointments(monday, monday + timedelta(weeks=3), self.today)
        self.items = list(Appointment.objects.filter(slot=self.slot).order_by('starts_at'))

    def end_from(self, appointment):
        return self.client.post(reverse('core:appointment_series_end', kwargs={'pk': appointment.pk}), follow=True)

    def test_this_and_the_following_leave_the_calendar(self):
        Appointment.objects.filter(pk=self.items[0].pk).update(status='attended')     # before: stays
        Appointment.objects.filter(pk=self.items[2].pk).update(status='missed')       # marked: stays
        response = self.end_from(self.items[1])
        self.slot.refresh_from_db()
        local = timezone.localtime(self.items[1].starts_at).date()
        self.assertEqual(self.slot.valid_until, local - timedelta(days=1))
        remaining = {a.pk for a in Appointment.objects.filter(slot=self.slot)}
        self.assertEqual(remaining, {self.items[0].pk, self.items[2].pk})
        self.assertContains(response, 'Horário fixo encerrado')
        self.assertContains(response, '2 aulas marcadas saíram da agenda')   # item 1 and item 3 were scheduled

    def test_it_does_not_come_back_when_the_calendar_is_opened(self):
        self.end_from(self.items[1])
        monday = timezone.localtime(self.items[0].starts_at).date()
        ensure_appointments(monday, monday + timedelta(weeks=4), self.today)
        self.assertEqual(Appointment.objects.filter(slot=self.slot).count(), 1)

    def test_only_post_and_only_a_series_class(self):
        url = reverse('core:appointment_series_end', kwargs={'pk': self.items[0].pk})
        self.assertEqual(self.client.get(url).status_code, 405)
        one_off = Appointment.objects.create(person=self.person, starts_at=when(self.today + timedelta(days=1), 9))
        response = self.client.post(reverse('core:appointment_series_end', kwargs={'pk': one_off.pk}), follow=True)
        self.assertContains(response, 'não faz parte de um horário fixo')
        self.assertTrue(Appointment.objects.filter(pk=one_off.pk).exists())

    def test_only_the_trainer(self):
        self.login(self.alice)
        url = reverse('core:appointment_series_end', kwargs={'pk': self.items[0].pk})
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(Appointment.objects.filter(slot=self.slot).count(), len(self.items))

    def test_end_series_from_helper(self):
        self.assertIsNone(end_series_from(Appointment.objects.create(person=self.person, starts_at=when(self.today))))

    def test_series_cards_offer_the_three_levels(self):
        monday = timezone.localtime(self.items[0].starts_at).date()
        html = self.client.get(reverse('core:agenda'), {'vista': 'semana', 'semana': monday.isoformat()}).content.decode()
        self.assertIn('Só esta aula: use Cancelar ou Remarcar.', html)
        self.assertIn('Esta e as próximas deixam de repetir', html)
        self.assertIn('Todas: remover o horário fixo', html)
        self.assertIn(f'/appointments/{self.items[0].pk}/end-series/', html)

    def test_removing_all_from_the_calendar_stays_on_the_calendar(self):
        agenda = reverse('core:agenda') + '?vista=semana'
        response = self.client.post(reverse('core:slot_delete', kwargs={'pk': self.slot.pk}) + f'?next={agenda}')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('core:agenda')))
        self.assertFalse(ScheduleSlot.objects.filter(pk=self.slot.pk).exists())
        again = self.client.post(reverse('core:slot_delete', kwargs={'pk': 999999}))
        self.assertEqual(again.status_code, 404)
