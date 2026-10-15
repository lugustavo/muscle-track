from datetime import date, datetime, time, timedelta

from django.urls import reverse
from django.utils import timezone

from core.agenda import MoveError, move_appointment, undo_move
from core.models import Appointment

from .base import BaseTestCase


def when(day, hour=9, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


DAY = date(2026, 10, 20)   # Tuesday


class MoveBase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.appt = Appointment.objects.create(person=self.alice.person, starts_at=when(DAY), duration_minutes=60)
        self.client.force_login(self.trainer)

    def move(self, appt=None, **data):
        appt = appt or self.appt
        return self.client.post(reverse('core:appointment_move', kwargs={'pk': appt.pk}), data)


class MoveServiceTests(MoveBase):
    def test_move_creates_new_and_links_original(self):
        new = move_appointment(self.appt, when(DAY + timedelta(days=1)))
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, 'rescheduled')
        self.assertEqual(self.appt.rescheduled_to, new)
        self.assertEqual((new.person, new.starts_at, new.duration_minutes, new.status),
                         (self.alice.person, when(DAY + timedelta(days=1)), 60, 'scheduled'))

    def test_same_time_or_taken_time_is_refused(self):
        with self.assertRaises(MoveError):
            move_appointment(self.appt, self.appt.starts_at)
        Appointment.objects.create(person=self.alice.person, starts_at=when(DAY + timedelta(days=1)))
        with self.assertRaises(MoveError):
            move_appointment(self.appt, when(DAY + timedelta(days=1)))
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, 'scheduled')

    def test_undo_restores_original(self):
        new = move_appointment(self.appt, when(DAY + timedelta(days=1)))
        original = undo_move(new)
        self.assertEqual((original.pk, original.status, original.rescheduled_to_id), (self.appt.pk, 'scheduled', None))
        self.assertFalse(Appointment.objects.filter(pk=new.pk).exists())

    def test_undo_refused_once_new_class_was_touched(self):
        new = move_appointment(self.appt, when(DAY + timedelta(days=1)))
        new.status = 'attended'
        new.save()
        with self.assertRaises(MoveError):
            undo_move(new)
        self.assertTrue(Appointment.objects.filter(pk=new.pk).exists())

    def test_undo_refused_for_a_class_that_is_not_a_move(self):
        with self.assertRaises(MoveError):
            undo_move(self.appt)


class MoveEndpointTests(MoveBase):
    def test_move_to_another_day_keeps_time(self):
        response = self.move(date='2026-10-22')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['when'], '22/10 às 09:00')
        self.assertEqual(body['overlap'], [])
        new = Appointment.objects.get(pk=body['id'])
        self.assertEqual(timezone.localtime(new.starts_at).date(), date(2026, 10, 22))
        self.assertEqual(timezone.localtime(new.starts_at).time(), time(9, 0))
        self.assertEqual(body['undo_url'], reverse('core:appointment_move_undo', kwargs={'pk': new.pk}))

    def test_move_with_time_and_duration(self):
        body = self.move(date='2026-10-22', time='14:30', duration='45').json()
        new = Appointment.objects.get(pk=body['id'])
        self.assertEqual((timezone.localtime(new.starts_at).strftime('%H:%M'), new.duration_minutes), ('14:30', 45))

    def test_overlap_is_reported_not_blocked(self):
        Appointment.objects.create(person=self.bob.person, starts_at=when(date(2026, 10, 22), 9, 30), duration_minutes=60)
        body = self.move(date='2026-10-22').json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['overlap'], [self.bob.person.person_name])

    def test_errors(self):
        for data in ({}, {'date': 'x'}, {'date': '2026-10-22', 'time': '99:00'}, {'date': '2026-10-22', 'duration': '5'},
                     {'date': '2026-10-22', 'duration': 'abc'}, {'date': '2026-10-20'}):
            response = self.move(**data)
            self.assertEqual(response.status_code, 400, data)
            self.assertFalse(response.json()['ok'])
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, 'scheduled')

    def test_only_marked_classes_move(self):
        for status in ('attended', 'missed', 'cancelled', 'rescheduled'):
            self.appt.status = status
            self.appt.save()
            self.assertEqual(self.move(date='2026-10-22').status_code, 400, status)

    def test_undo_endpoint(self):
        new_id = self.move(date='2026-10-22').json()['id']
        response = self.client.post(reverse('core:appointment_move_undo', kwargs={'pk': new_id}))
        self.assertEqual(response.json(), {'ok': True, 'id': self.appt.pk})
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, 'scheduled')
        again = self.client.post(reverse('core:appointment_move_undo', kwargs={'pk': new_id}))
        self.assertEqual(again.status_code, 404)

    def test_undo_error_is_json(self):
        response = self.client.post(reverse('core:appointment_move_undo', kwargs={'pk': self.appt.pk}))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])

    def test_staff_only_and_post_only(self):
        url = reverse('core:appointment_move', kwargs={'pk': self.appt.pk})
        undo = reverse('core:appointment_move_undo', kwargs={'pk': self.appt.pk})
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.get(undo).status_code, 405)
        for who in (self.alice, None):
            self.client.logout()
            if who:
                self.client.force_login(who)
            self.assertNotEqual(self.client.post(url, {'date': '2026-10-22'}).status_code, 200)
            self.assertNotEqual(self.client.post(undo).status_code, 200)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, 'scheduled')

    def test_reschedule_form_still_works_and_links(self):
        response = self.client.post(reverse('core:appointment_reschedule', kwargs={'pk': self.appt.pk}), {
            'person': self.alice.person.pk, 'date': '2026-10-23', 'time': '10:00', 'duration_minutes': 60, 'kind': 'regular',
            'notes': ''})
        self.assertEqual(response.status_code, 302)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, 'rescheduled')
        self.assertIsNotNone(self.appt.rescheduled_to)


class AgendaMarkupTests(MoveBase):
    def page(self):
        return self.client.get(reverse('core:agenda'), {'semana': '2026-10-19', 'dia': '2026-10-20'})

    def test_marked_class_is_draggable_with_move_data(self):
        page = self.page()
        self.assertContains(page, 'draggable="true" data-appt-move')
        self.assertContains(page, reverse('core:appointment_move', kwargs={'pk': self.appt.pk}))
        self.assertContains(page, 'data-appt-move-open')
        self.assertContains(page, 'js/agenda-move.js')
        self.assertContains(page, 'id="moveModal"')

    def test_other_statuses_are_not_draggable(self):
        self.appt.status = 'attended'
        self.appt.save()
        page = self.page()
        self.assertNotContains(page, 'draggable="true" data-appt-move')
        self.assertNotContains(page, 'data-appt-move-open')
        self.assertContains(page, reverse('core:appointment_reschedule', kwargs={'pk': self.appt.pk}))
