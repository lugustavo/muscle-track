from datetime import date, datetime, time, timedelta

from django.urls import reverse
from django.utils import timezone

from core.agenda import ensure_appointments
from core.models import Appointment, AppointmentRequest, ScheduleSlot
from core.recurrence import SeriesError, edit_series

from .base import BaseTestCase

MON = date(2026, 10, 12)               # a Monday
NOW = timezone.make_aware(datetime(2026, 10, 14, 10, 0))   # Wednesday of that week
TODAY = date(2026, 10, 14)


def when(day, hour=7, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class EditSeriesTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.slot = ScheduleSlot.objects.create(person=self.person, weekday=0, start_time=time(7, 0), duration_minutes=60,
                                                valid_from=MON - timedelta(weeks=3))
        ensure_appointments(MON - timedelta(weeks=3), MON + timedelta(weeks=4), TODAY)

    def classes(self, **filters):
        return list(Appointment.objects.filter(person=self.person, **filters).order_by('starts_at'))

    def edit(self, weekday=0, start=time(7, 0), duration=60, until=None, from_day=TODAY):
        return edit_series(self.slot, weekday, start, duration, until, from_day, now=NOW)

    def test_new_time_applies_from_the_date_and_keeps_the_past(self):
        past = self.classes(starts_at__lt=NOW)
        current, removed = self.edit(start=time(9, 0))
        self.assertNotEqual(current.pk, self.slot.pk)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.valid_until, TODAY - timedelta(days=1))
        self.assertEqual(self.classes(starts_at__lt=NOW), past)                  # history untouched
        self.assertTrue(all(timezone.localtime(a.starts_at).hour == 7 for a in past))
        self.assertEqual(removed, 4)                                             # Mondays 19/10, 26/10, 02/11, 09/11
        ensure_appointments(TODAY, TODAY + timedelta(weeks=4), TODAY)
        future = self.classes(starts_at__gte=NOW)
        self.assertEqual([timezone.localtime(a.starts_at).strftime('%d/%m %H:%M') for a in future],
                         ['19/10 09:00', '26/10 09:00', '02/11 09:00', '09/11 09:00'])
        self.assertTrue(all(a.slot_id == current.pk for a in future))

    def test_new_weekday_and_length(self):
        current, _ = self.edit(weekday=2, duration=45)       # Mondays -> Wednesdays
        self.assertEqual((current.weekday, current.duration_minutes, current.valid_from), (2, 45, date(2026, 10, 14)))
        ensure_appointments(TODAY, TODAY + timedelta(weeks=2), TODAY)
        wednesdays = [a for a in self.classes(starts_at__gte=NOW) if a.slot_id == current.pk]
        self.assertEqual([timezone.localtime(a.starts_at).date() for a in wednesdays],
                         [date(2026, 10, 21), date(2026, 10, 28)])      # today's 07:00 class is already past
        self.assertEqual({a.duration_minutes for a in wednesdays}, {45})
        self.assertFalse([a for a in self.classes(starts_at__gte=NOW) if timezone.localtime(a.starts_at).weekday() == 0])

    def test_only_the_length_changes_in_place(self):
        current, removed = self.edit(duration=90)
        self.assertEqual((current.pk, removed), (self.slot.pk, 0))
        self.assertEqual({a.duration_minutes for a in self.classes(starts_at__gte=NOW)}, {90})
        self.assertEqual({a.duration_minutes for a in self.classes(starts_at__lt=NOW)}, {60})   # past stays
        self.assertEqual(ScheduleSlot.objects.filter(person=self.person).count(), 1)

    def test_end_date_drops_later_classes(self):
        _, removed = self.edit(until=date(2026, 10, 26))
        self.assertEqual(removed, 2)                       # 02/11 and 09/11 are after the end
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.valid_until, date(2026, 10, 26))
        ensure_appointments(TODAY, TODAY + timedelta(weeks=4), TODAY)
        self.assertEqual(len(self.classes(starts_at__gte=NOW)), 2)

    def test_classes_touched_by_hand_are_left_alone(self):
        moved = Appointment.objects.get(person=self.person, starts_at=when(date(2026, 10, 19)))
        moved.duration_minutes = 30                                   # edited by hand
        moved.save()
        asked = Appointment.objects.get(person=self.person, starts_at=when(date(2026, 10, 26)))
        AppointmentRequest.objects.create(appointment=asked, kind='cancel', message='x')
        done = Appointment.objects.get(person=self.person, starts_at=when(date(2026, 11, 2)))
        done.status = 'attended'
        done.save()
        _, removed = self.edit(start=time(9, 0))
        self.assertEqual(removed, 1)                                  # only 09/11 was untouched
        for a in (moved, asked, done):
            self.assertTrue(Appointment.objects.filter(pk=a.pk).exists())

    def test_series_starting_later_is_changed_in_place(self):
        later = ScheduleSlot.objects.create(person=self.person, weekday=3, start_time=time(8, 0), valid_from=TODAY + timedelta(days=30))
        current, _ = edit_series(later, 4, time(8, 30), 60, None, TODAY, now=NOW)
        self.assertEqual((current.pk, current.weekday, current.start_time), (later.pk, 4, time(8, 30)))

    def test_every_other_week_keeps_its_rhythm(self):
        slot = ScheduleSlot.objects.create(person=self.person, weekday=1, start_time=time(18, 0), valid_from=MON - timedelta(weeks=2),
                                           interval_weeks=2)   # Tuesdays of the weeks of 28/09, 12/10, 26/10...
        current, _ = edit_series(slot, 3, time(18, 0), 60, None, TODAY, now=NOW)   # Thursday, same weeks
        self.assertEqual(current.valid_from, date(2026, 10, 15))                   # this week is on the cycle
        slot2 = ScheduleSlot.objects.create(person=self.person, weekday=1, start_time=time(19, 0), valid_from=MON - timedelta(weeks=1),
                                            interval_weeks=2)      # cycle weeks: 05/10, 19/10...
        current, _ = edit_series(slot2, 3, time(19, 0), 60, None, TODAY, now=NOW)
        self.assertEqual(current.valid_from, date(2026, 10, 22))                   # skips this off-cycle week

    def test_errors(self):
        ScheduleSlot.objects.create(person=self.person, weekday=2, start_time=time(9, 0), valid_from=MON)
        with self.assertRaises(SeriesError):
            self.edit(weekday=2, start=time(9, 0))                          # clashes with another slot
        with self.assertRaises(SeriesError):
            self.edit(until=TODAY - timedelta(days=1))                      # ends before it starts
        self.slot.valid_until = TODAY - timedelta(days=5)
        self.slot.save()
        with self.assertRaises(SeriesError):
            self.edit(start=time(10, 0))                                    # already ended


class SlotEditViewTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        today = timezone.localdate()
        self.slot = ScheduleSlot.objects.create(person=self.person, weekday=today.weekday(), start_time=time(7, 0),
                                                valid_from=today - timedelta(weeks=2))
        self.url = reverse('core:slot_edit', kwargs={'pk': self.slot.pk})
        self.client.force_login(self.trainer)

    def data(self, **extra):
        today = timezone.localdate()
        return {'weekday': self.slot.weekday, 'start_time': '09:00', 'duration_minutes': 60,
                'from_day': today.isoformat(), 'valid_until': '', **extra}

    def test_form_is_prefilled(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'value="07:00"')
        self.assertContains(page, 'Editar horário fixo')

    def test_post_saves_and_returns_to_the_agenda_tab(self):
        response = self.client.post(self.url, self.data())
        self.assertIn('#tab-agenda', response['Location'])
        self.assertTrue(ScheduleSlot.objects.filter(person=self.person, start_time=time(9, 0)).exists())

    def test_error_is_shown_on_the_form(self):
        ScheduleSlot.objects.create(person=self.person, weekday=self.slot.weekday, start_time=time(9, 0))
        response = self.client.post(self.url, self.data())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'já tem um horário fixo')

    def test_student_file_lists_current_slots_with_an_edit_link(self):
        ended = ScheduleSlot.objects.create(person=self.person, weekday=3, start_time=time(8, 0),
                                            valid_from=timezone.localdate() - timedelta(weeks=9),
                                            valid_until=timezone.localdate() - timedelta(days=2))
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, self.url)
        self.assertNotContains(page, reverse('core:slot_edit', kwargs={'pk': ended.pk}))   # ended: only history

    def test_only_the_trainer(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(self.url).status_code, 403)
