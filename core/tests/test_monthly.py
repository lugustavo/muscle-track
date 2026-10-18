from datetime import date, time, timedelta

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import recurrence
from core.agenda import ensure_appointments, monthly_match, slot_matches, slot_on
from core.forms import RepeatForm, ScheduleSlotForm
from core.models import Appointment, ScheduleSlot
from core.package_schedule import previous_pattern

from .base import BaseTestCase

# 2026: Nov 1 is a Sunday. Tuesdays: 3, 10, 17, 24 (the 2nd is the 10th, the last is the 24th); Dec 1, 8, 15, 22, 29.
SECOND_TUESDAY = date(2026, 11, 10)
LAST_TUESDAY = date(2026, 11, 24)
FIFTH_SUNDAY = date(2026, 11, 29)
TODAY = date(2026, 11, 1)


def dates(slot, start, end):
    day, found = start, []
    while day <= end:
        if slot_on(slot, day):
            found.append(day)
        day += timedelta(days=1)
    return found


class RuleTests(BaseTestCase):
    def test_same_day_of_the_month(self):
        anchor = date(2026, 11, 15)
        self.assertTrue(monthly_match(date(2026, 12, 15), anchor, 'day'))
        self.assertTrue(monthly_match(date(2026, 11, 15), anchor, 'day'))
        self.assertFalse(monthly_match(date(2026, 12, 16), anchor, 'day'))
        self.assertFalse(monthly_match(date(2026, 10, 15), anchor, 'day'))       # before the first class

    def test_a_day_that_the_month_does_not_have_is_skipped(self):
        anchor = date(2026, 10, 31)
        self.assertTrue(monthly_match(date(2026, 12, 31), anchor, 'day'))
        self.assertFalse(any(monthly_match(date(2026, 11, d), anchor, 'day') for d in range(1, 31)))

    def test_same_weekday_of_the_same_week(self):
        self.assertTrue(monthly_match(date(2026, 12, 8), SECOND_TUESDAY, 'nth'))
        self.assertTrue(monthly_match(date(2027, 1, 12), SECOND_TUESDAY, 'nth'))
        self.assertFalse(monthly_match(date(2026, 12, 1), SECOND_TUESDAY, 'nth'))
        self.assertFalse(monthly_match(date(2026, 12, 15), SECOND_TUESDAY, 'nth'))
        self.assertFalse(monthly_match(date(2026, 12, 9), SECOND_TUESDAY, 'nth'))     # not a Tuesday

    def test_the_last_weekday_of_the_month(self):
        self.assertTrue(monthly_match(date(2026, 12, 29), LAST_TUESDAY, 'last'))
        self.assertTrue(monthly_match(date(2027, 1, 26), LAST_TUESDAY, 'last'))
        self.assertFalse(monthly_match(date(2026, 12, 22), LAST_TUESDAY, 'last'))
        self.assertTrue(monthly_match(date(2027, 2, 23), LAST_TUESDAY, 'last'))       # Feb 2027 ends on a Sunday

    def test_every_other_month(self):
        self.assertTrue(monthly_match(date(2027, 1, 12), SECOND_TUESDAY, 'nth', 2))
        self.assertFalse(monthly_match(date(2026, 12, 8), SECOND_TUESDAY, 'nth', 2))
        self.assertTrue(monthly_match(date(2027, 3, 9), SECOND_TUESDAY, 'nth', 2))

    def test_which_rules_fit_a_day(self):
        self.assertEqual(set(recurrence.month_rule_options(SECOND_TUESDAY)), {'day', 'nth'})
        self.assertEqual(set(recurrence.month_rule_options(LAST_TUESDAY)), {'day', 'nth', 'last'})   # 4th and last
        self.assertEqual(set(recurrence.month_rule_options(FIFTH_SUNDAY)), {'day', 'last'})            # no "5th"
        self.assertIn('15', recurrence.month_rule_options(date(2026, 11, 15))['day'])
        self.assertIn('2ª', recurrence.month_rule_options(SECOND_TUESDAY)['nth'])

    def test_occurrence_dates_and_the_end_after_n_classes(self):
        found = list(recurrence.occurrence_dates(SECOND_TUESDAY, [1], limit=4, month_rule='nth'))
        self.assertEqual(found, [date(2026, 11, 10), date(2026, 12, 8), date(2027, 1, 12), date(2027, 2, 9)])
        last = recurrence.end_date(SECOND_TUESDAY, [1], 1, 'after', ends_after=3, month_rule='nth')
        self.assertEqual(last, date(2027, 1, 12))
        every_two = list(recurrence.occurrence_dates(date(2026, 11, 15), [6], limit=3, month_rule='day', interval_months=2))
        self.assertEqual(every_two, [date(2026, 11, 15), date(2027, 1, 15), date(2027, 3, 15)])

    def test_weekly_series_are_unchanged(self):
        found = list(recurrence.occurrence_dates(date(2026, 11, 2), [0, 3], limit=4))
        self.assertEqual(found, [date(2026, 11, 2), date(2026, 11, 5), date(2026, 11, 9), date(2026, 11, 12)])


class SeriesTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person

    def monthly(self, start=SECOND_TUESDAY, rule='nth', **extra):
        kwargs = dict(starts_on=start, month_rule=rule, interval_months=1, valid_until=None)
        kwargs.update(extra)
        slots, created = recurrence.create_series(self.person, [start.weekday()], time(7, 0), 60, **kwargs)
        return slots[0]

    def test_a_monthly_series_is_one_slot_on_the_weekday_of_its_first_class(self):
        slot = self.monthly()
        self.assertEqual((slot.month_rule, slot.weekday, slot.valid_from, slot.interval_months), ('nth', 1, SECOND_TUESDAY, 1))
        self.assertEqual(ScheduleSlot.objects.count(), 1)

    def test_the_calendar_creates_the_classes_of_the_right_days(self):
        self.monthly()
        ensure_appointments(date(2026, 11, 1), date(2027, 2, 28), TODAY)
        days = sorted(timezone.localtime(a.starts_at).date() for a in Appointment.objects.filter(person=self.person))
        self.assertEqual(days, [date(2026, 11, 10), date(2026, 12, 8), date(2027, 1, 12), date(2027, 2, 9)])
        self.assertEqual(ensure_appointments(date(2026, 11, 1), date(2027, 2, 28), TODAY), 0)    # idempotent

    def test_end_date_and_skipped_days(self):
        slot = self.monthly(valid_until=date(2027, 1, 31))
        slot.skipped_dates = ['2026-12-08']
        slot.save()
        ensure_appointments(date(2026, 11, 1), date(2027, 3, 31), TODAY)
        days = sorted(timezone.localtime(a.starts_at).date() for a in Appointment.objects.filter(person=self.person))
        self.assertEqual(days, [date(2026, 11, 10), date(2027, 1, 12)])

    def test_a_weekly_slot_at_the_same_time_lives_beside_it(self):
        weekly, _ = recurrence.create_series(self.person, [1], time(7, 0), 60, date(2026, 11, 3))
        self.monthly()
        self.assertEqual(ScheduleSlot.objects.count(), 2)
        self.assertEqual(ScheduleSlot.objects.get(month_rule='').pk, weekly[0].pk)

    def test_the_same_monthly_time_cannot_be_created_twice_while_it_runs(self):
        self.monthly()
        with self.assertRaises(recurrence.SeriesError):
            self.monthly()

    def test_a_finished_monthly_series_can_start_again(self):
        slot = self.monthly()
        ScheduleSlot.objects.filter(pk=slot.pk).update(valid_until=timezone.localdate() - timedelta(days=1))
        again = self.monthly(start=date(2027, 3, 9))
        self.assertEqual(again.pk, slot.pk)
        self.assertEqual(again.valid_from, date(2027, 3, 9))

    def test_describe_in_words(self):
        text = recurrence.describe([1], time(7, 0), 1, date(2027, 6, 30), 'nth', 1, SECOND_TUESDAY)
        self.assertEqual(text, 'Terça-feira da 2ª semana do mês às 07:00, até 30/06/2027')
        text = recurrence.describe([1], time(7, 0), 1, None, 'day', 2, date(2026, 11, 15))
        self.assertEqual(text, 'Todo dia 15 do mês, a cada 2 meses às 07:00')
        self.assertEqual(recurrence.describe([0, 3], time(19, 0), 2), 'seg, qui às 19:00, a cada 2 semanas')

    def test_a_monthly_time_is_not_a_weekly_pattern_to_continue(self):
        self.monthly()
        self.assertIsNone(previous_pattern(self.person))
        weekly, _ = recurrence.create_series(self.person, [0], time(9, 0), 60, date(2026, 11, 2))
        self.assertEqual(previous_pattern(self.person)['weekdays'], [0])


class EditTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.today = timezone.localdate()
        self.start = self.today + timedelta(days=2)
        slots, _ = recurrence.create_series(self.person, [self.start.weekday()], time(7, 0), 60, self.start,
                                            month_rule='day', interval_months=1)
        self.slot = slots[0]

    def test_changing_only_the_end_keeps_the_rule(self):
        until = self.start + timedelta(days=200)
        slot, removed = recurrence.edit_series(self.slot, 0, time(7, 0), 45, until, self.today)
        self.assertEqual((slot.pk, slot.month_rule, slot.valid_until, slot.duration_minutes), (self.slot.pk, 'day', until, 45))
        self.assertEqual(slot.weekday, self.start.weekday())     # the weekday asked for is ignored

    def test_a_new_time_starts_a_new_slot_on_the_next_matching_day(self):
        ensure_appointments(self.today, self.today + timedelta(weeks=14), self.today)
        before = Appointment.objects.filter(person=self.person).count()
        later = self.start + timedelta(days=40)
        slot, removed = recurrence.edit_series(self.slot, 0, time(9, 30), 60, None, later)
        self.assertNotEqual(slot.pk, self.slot.pk)
        self.assertEqual((slot.month_rule, slot.start_time, slot.interval_months), ('day', time(9, 30), 1))
        self.assertEqual(slot.valid_from.day, self.start.day)
        self.assertGreaterEqual(slot.valid_from, later)
        self.assertEqual(slot.weekday, slot.valid_from.weekday())
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.valid_until, later - timedelta(days=1))
        self.assertGreater(before, 0)


class FormTests(BaseTestCase):
    def slot_form(self, **data):
        base = {'start_time': '07:00', 'duration_minutes': '60', 'starts_on': '2026-11-10', 'ends': 'never',
                'interval_weeks': '1', 'interval_months': '1', 'frequency': 'monthly', 'month_rule': 'nth'}
        base.update(data)
        return ScheduleSlotForm(base)

    def test_monthly_needs_no_weekdays(self):
        form = self.slot_form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['all_weekdays'], [1])
        self.assertEqual((form.cleaned_data['month_rule'], form.cleaned_data['interval_months']), ('nth', 1))

    def test_weekly_still_needs_a_weekday(self):
        form = self.slot_form(frequency='weekly')
        self.assertFalse(form.is_valid())
        self.assertIn('weekdays', form.errors)
        form = self.slot_form(frequency='weekly', weekdays=['1'])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['month_rule'], '')

    def test_a_rule_that_does_not_fit_the_start_date_is_refused(self):
        form = self.slot_form(starts_on='2026-11-29', month_rule='nth')        # the 5th Sunday
        self.assertFalse(form.is_valid())
        self.assertIn('month_rule', form.errors)
        self.assertTrue(self.slot_form(starts_on='2026-11-29', month_rule='last').is_valid())
        self.assertFalse(self.slot_form(starts_on='2026-11-10', month_rule='last').is_valid())

    def test_the_rule_is_required_for_monthly(self):
        form = self.slot_form(month_rule='')
        self.assertFalse(form.is_valid())
        self.assertIn('month_rule', form.errors)

    def test_end_after_n_classes_counts_months(self):
        form = self.slot_form(ends='after', ends_after='3')
        self.assertTrue(form.is_valid(), form.errors)
        until = form.series_end([1], date(2026, 11, 10))
        self.assertEqual(until, date(2027, 1, 12))

    def test_interval_months_limits(self):
        self.assertFalse(self.slot_form(interval_months='13').is_valid())
        self.assertTrue(self.slot_form(interval_months='').is_valid())     # blank means every month

    def test_repeat_form_monthly_uses_the_day_of_the_class(self):
        form = RepeatForm({'enabled': 'on', 'frequency': 'monthly', 'month_rule': 'day', 'ends': 'never',
                           'interval_weeks': '1', 'interval_months': '1'})
        form.start_hint = date(2026, 11, 10)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['weekdays'], [1])
        weekly = RepeatForm({'enabled': 'on', 'ends': 'never', 'interval_weeks': '1'})
        weekly.start_hint = date(2026, 11, 10)
        self.assertFalse(weekly.is_valid())


class ViewTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person
        self.today = timezone.localdate()
        self.start = self.today + timedelta(days=3)

    def test_the_options_endpoint(self):
        response = self.client.get(reverse('core:month_rules'), {'date': '2026-11-10'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()['options']), {'day', 'nth'})
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get(reverse('core:month_rules'), {'date': 'x'}).status_code, 400)
        self.assertEqual(self.client.get(reverse('core:month_rules')).status_code, 400)

    def test_the_options_endpoint_is_for_the_trainer(self):
        self.assertEqual(Client().get(reverse('core:month_rules'), {'date': '2026-11-10'}).status_code, 302)
        self.login(self.alice)
        self.assertEqual(self.client.get(reverse('core:month_rules'), {'date': '2026-11-10'}).status_code, 403)

    def test_add_a_monthly_fixed_time(self):
        response = self.client.post(reverse('core:slot_add', kwargs={'pk': self.person.pk}), {
            'start_time': '07:00', 'duration_minutes': '60', 'starts_on': self.start.isoformat(), 'ends': 'never',
            'interval_weeks': '1', 'frequency': 'monthly', 'month_rule': 'day', 'interval_months': '1'}, follow=True)
        slot = ScheduleSlot.objects.get(person=self.person)
        self.assertEqual((slot.month_rule, slot.valid_from, slot.weekday), ('day', self.start, self.start.weekday()))
        self.assertContains(response, f'Todo dia {self.start.day} do mês')
        self.assertTrue(Appointment.objects.filter(person=self.person, slot=slot).exists())
        first = timezone.localtime(Appointment.objects.filter(slot=slot).order_by('starts_at').first().starts_at).date()
        self.assertEqual(first, self.start)

    def test_a_repeated_monthly_time_is_refused_with_a_message(self):
        data = {'start_time': '07:00', 'duration_minutes': '60', 'starts_on': self.start.isoformat(), 'ends': 'never',
                'interval_weeks': '1', 'frequency': 'monthly', 'month_rule': 'day', 'interval_months': '1'}
        url = reverse('core:slot_add', kwargs={'pk': self.person.pk})
        self.client.post(url, data)
        response = self.client.post(url, data, follow=True)
        self.assertContains(response, 'já tem um horário mensal')
        self.assertEqual(ScheduleSlot.objects.count(), 1)

    def test_a_bad_rule_shows_the_error(self):
        response = self.client.post(reverse('core:slot_add', kwargs={'pk': self.person.pk}), {
            'start_time': '07:00', 'duration_minutes': '60', 'starts_on': '2026-11-29', 'ends': 'never',
            'interval_weeks': '1', 'frequency': 'monthly', 'month_rule': 'nth', 'interval_months': '1'}, follow=True)
        self.assertContains(response, 'Essa opção não vale')
        self.assertFalse(ScheduleSlot.objects.exists())

    def test_new_class_repeated_every_month(self):
        response = self.client.post(reverse('core:appointment_create'), {
            'person': self.person.pk, 'date': self.start.isoformat(), 'time': '08:00', 'duration_minutes': '60',
            'kind': 'regular', 'notes': '', 'rep-enabled': 'on', 'rep-frequency': 'monthly', 'rep-month_rule': 'day',
            'rep-interval_months': '1', 'rep-interval_weeks': '1', 'rep-ends': 'after', 'rep-ends_after': '3'})
        self.assertEqual(response.status_code, 302)
        slot = ScheduleSlot.objects.get(person=self.person)
        self.assertEqual((slot.month_rule, slot.valid_from), ('day', self.start))
        self.assertIsNotNone(slot.valid_until)
        days = sorted(timezone.localtime(a.starts_at).date() for a in Appointment.objects.filter(slot=slot))
        self.assertEqual(days[0], self.start)
        self.assertTrue(all(d.day == self.start.day for d in days))

    def test_a_monthly_repeat_that_already_exists_keeps_the_form_open(self):
        data = {'person': self.person.pk, 'date': self.start.isoformat(), 'time': '08:00', 'duration_minutes': '60',
                'kind': 'regular', 'notes': '', 'rep-enabled': 'on', 'rep-frequency': 'monthly', 'rep-month_rule': 'day',
                'rep-interval_months': '1', 'rep-interval_weeks': '1', 'rep-ends': 'never'}
        self.client.post(reverse('core:appointment_create'), data)
        response = self.client.post(reverse('core:appointment_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'já tem um horário mensal')

    def test_the_student_agenda_tab_and_the_edit_page_say_how_it_repeats(self):
        slots, _ = recurrence.create_series(self.person, [self.start.weekday()], time(7, 0), 60, self.start,
                                            month_rule='day', interval_months=2)
        tab = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}), {'tab': 'agenda'})
        self.assertContains(tab, f'Todo dia {self.start.day} do mês, a cada 2 meses')
        page = self.client.get(reverse('core:slot_edit', kwargs={'pk': slots[0].pk}))
        self.assertContains(page, f'Todo dia {self.start.day} do mês')
        self.assertIn('disabled', str(page.context['form']['weekday']))

    def test_editing_a_monthly_time_from_the_page(self):
        slots, _ = recurrence.create_series(self.person, [self.start.weekday()], time(7, 0), 60, self.start,
                                            month_rule='day')
        response = self.client.post(reverse('core:slot_edit', kwargs={'pk': slots[0].pk}), {
            'weekday': '6', 'start_time': '07:00', 'duration_minutes': '30', 'from_day': self.today.isoformat(),
            'valid_until': ''})
        self.assertEqual(response.status_code, 302)
        slot = ScheduleSlot.objects.get()
        self.assertEqual((slot.duration_minutes, slot.weekday), (30, self.start.weekday()))

    def test_ending_the_series_from_a_class_still_works(self):
        slots, _ = recurrence.create_series(self.person, [self.start.weekday()], time(7, 0), 60, self.start,
                                            month_rule='day')
        ensure_appointments(self.today, self.today + timedelta(weeks=14), self.today)
        appointment = Appointment.objects.filter(slot=slots[0]).order_by('starts_at').first()
        response = self.client.post(reverse('core:appointment_series_end', kwargs={'pk': appointment.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Appointment.objects.filter(slot=slots[0], status='scheduled').exists())

    def test_slot_matches_ignores_the_end_date_but_slot_on_does_not(self):
        slots, _ = recurrence.create_series(self.person, [1], time(7, 0), 60, date(2026, 11, 3), valid_until=date(2026, 11, 30))
        slot = slots[0]
        self.assertTrue(slot_matches(slot, date(2026, 12, 8)))
        self.assertFalse(slot_on(slot, date(2026, 12, 8)))
        self.assertTrue(slot_on(slot, date(2026, 11, 17)))

    def test_in_english(self):
        slots, _ = recurrence.create_series(self.person, [self.start.weekday()], time(7, 0), 60, self.start,
                                            month_rule='day', interval_months=2)
        tab = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}), {'tab': 'agenda'},
                              HTTP_ACCEPT_LANGUAGE='en')
        self.assertContains(tab, f'Day {self.start.day} of every month, every 2 months')
        form = self.client.get(reverse('core:appointment_create'), HTTP_ACCEPT_LANGUAGE='en')
        for text in ('Monthly', 'Every month, on which day?', 'Repeats every (months)'):
            self.assertContains(form, text)
        options = self.client.get(reverse('core:month_rules'), {'date': '2026-11-10'}, HTTP_ACCEPT_LANGUAGE='en').json()['options']
        self.assertEqual(options['nth'], 'Tuesday of week 2 of the month')
