from datetime import timedelta

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import class_requests
from core.models import Appointment, AppointmentRequest

from .base import BaseTestCase


def future(days=3, hour=9):
    base = timezone.localtime(timezone.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return base


class RequestBase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.person = self.alice.person
        self.appt = Appointment.objects.create(person=self.person, starts_at=future(3))

    def ask(self, kind='reschedule', **extra):
        new = future(5, 10)
        data = {'kind': kind, 'date': new.date().isoformat(), 'time': '10:00', 'message': 'Consulta médica'}
        data.update(extra)
        return self.client.post(reverse('core:my_agenda_request', kwargs={'pk': self.appt.pk}), data)


class RulesTests(RequestBase):
    def test_block_reasons(self):
        self.assertEqual(class_requests.request_block(self.appt), '')
        soon = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=5))
        self.assertIn('prazo', class_requests.request_block(soon))
        past = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(hours=5))
        self.assertIn('passou', class_requests.request_block(past))
        self.appt.status = 'attended'
        self.assertIn('não está marcada', class_requests.request_block(self.appt))

    @override_settings(AGENDA_NOTICE_HOURS=0)
    def test_notice_zero_means_no_deadline(self):
        soon = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(class_requests.request_block(soon), '')

    def test_one_pending_request_per_class(self):
        class_requests.create_request(self.person, self.appt, 'cancel')
        with self.assertRaises(class_requests.RequestError):
            class_requests.create_request(self.person, self.appt, 'cancel')

    def test_cannot_ask_for_someone_elses_class(self):
        with self.assertRaises(class_requests.RequestError):
            class_requests.create_request(self.bob.person, self.appt, 'cancel')

    def test_reschedule_needs_a_valid_future_time(self):
        for proposed in (None, timezone.now() - timedelta(days=1), self.appt.starts_at):
            with self.assertRaises(class_requests.RequestError):
                class_requests.create_request(self.person, self.appt, 'reschedule', proposed)
        taken = Appointment.objects.create(person=self.person, starts_at=future(6))
        with self.assertRaises(class_requests.RequestError):
            class_requests.create_request(self.person, self.appt, 'reschedule', taken.starts_at)

    def test_approve_reschedule_moves_the_class(self):
        target = future(5, 10)
        asked = class_requests.create_request(self.person, self.appt, 'reschedule', target, 'x')
        class_requests.approve(asked)
        asked.refresh_from_db()
        self.appt.refresh_from_db()
        self.assertEqual((asked.status, self.appt.status), ('approved', 'rescheduled'))
        self.assertTrue(Appointment.objects.filter(person=self.person, starts_at=target, status='scheduled').exists())

    def test_approve_cancel_cancels_the_class(self):
        asked = class_requests.create_request(self.person, self.appt, 'cancel')
        class_requests.approve(asked)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, 'cancelled')

    def test_approve_fails_when_the_class_changed_or_time_was_taken(self):
        target = future(5, 10)
        asked = class_requests.create_request(self.person, self.appt, 'reschedule', target)
        Appointment.objects.create(person=self.person, starts_at=target)   # took the time meanwhile
        with self.assertRaises(class_requests.RequestError):
            class_requests.approve(asked)
        asked.refresh_from_db()
        self.assertEqual(asked.status, 'pending')
        self.appt.status = 'missed'
        self.appt.save()
        with self.assertRaises(class_requests.RequestError):
            class_requests.approve(asked)

    def test_decline_and_double_answer(self):
        asked = class_requests.create_request(self.person, self.appt, 'cancel')
        class_requests.decline(asked, 'Sem vaga em outro dia')
        asked.refresh_from_db()
        self.assertEqual((asked.status, asked.decision_note), ('declined', 'Sem vaga em outro dia'))
        with self.assertRaises(class_requests.RequestError):
            class_requests.approve(asked)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, 'scheduled')
        # after an answer the student can ask again
        class_requests.create_request(self.person, self.appt, 'cancel')

    def test_pending_list_ignores_stale_requests(self):
        asked = class_requests.create_request(self.person, self.appt, 'cancel')
        self.assertEqual(list(class_requests.pending_for_trainer()), [asked])
        self.appt.status = 'cancelled'
        self.appt.save()
        self.assertEqual(list(class_requests.pending_for_trainer()), [])
        self.assertEqual(class_requests.label(asked), 'Encerrado')


class StudentPagesTests(RequestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.alice)

    def test_my_agenda_lists_only_my_classes(self):
        Appointment.objects.create(person=self.bob.person, starts_at=future(4))
        page = self.client.get(reverse('core:my_agenda'))
        self.assertContains(page, f'data-my-class="{self.appt.pk}"')
        self.assertEqual(page.content.decode().count('data-my-class='), 1)
        self.assertNotContains(page, 'Bob')
        self.assertContains(page, reverse('core:my_agenda_request', kwargs={'pk': self.appt.pk}))

    def test_trainer_and_visitor_cannot_open_it(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('core:my_agenda')).status_code, 302)
        self.client.force_login(self.trainer)
        self.assertEqual(self.client.get(reverse('core:my_agenda')).status_code, 302)

    def test_consent_gate(self):
        self.alice.person.consents.all().delete()
        response = self.client.get(reverse('core:my_agenda'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('core:consent'), response['Location'])

    def test_ask_to_reschedule(self):
        response = self.ask()
        self.assertRedirects(response, reverse('core:my_agenda'), fetch_redirect_response=False)
        asked = AppointmentRequest.objects.get()
        self.assertEqual((asked.kind, asked.status, asked.message), ('reschedule', 'pending', 'Consulta médica'))
        self.assertEqual(timezone.localtime(asked.proposed_starts_at).strftime('%H:%M'), '10:00')
        page = self.client.get(reverse('core:my_agenda'))
        self.assertContains(page, 'data-open-request')
        self.assertNotContains(page, reverse('core:my_agenda_request', kwargs={'pk': self.appt.pk}))

    def test_ask_to_cancel_ignores_date(self):
        self.ask('cancel', date='', time='')
        self.assertEqual(AppointmentRequest.objects.get().kind, 'cancel')
        self.assertIsNone(AppointmentRequest.objects.get().proposed_starts_at)

    def test_reschedule_form_needs_date_and_time(self):
        response = self.ask(date='', time='')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AppointmentRequest.objects.exists())

    def test_cannot_ask_for_a_class_inside_the_deadline_or_of_another_student(self):
        soon = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=3))
        page = self.client.get(reverse('core:my_agenda'))
        self.assertContains(page, 'data-request-block')
        self.client.post(reverse('core:my_agenda_request', kwargs={'pk': soon.pk}), {'kind': 'cancel'})
        other = Appointment.objects.create(person=self.bob.person, starts_at=future(4))
        self.assertEqual(self.client.post(
            reverse('core:my_agenda_request', kwargs={'pk': other.pk}), {'kind': 'cancel'}).status_code, 404)
        self.assertFalse(AppointmentRequest.objects.exists())

    def test_request_page_shows_form_or_the_reason_it_is_closed(self):
        page = self.client.get(reverse('core:my_agenda_request', kwargs={'pk': self.appt.pk}))
        self.assertContains(page, 'method="post" data-request-form')
        soon = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=3))
        page = self.client.get(reverse('core:my_agenda_request', kwargs={'pk': soon.pk}))
        self.assertNotContains(page, 'method="post" data-request-form')
        self.assertContains(page, 'prazo para pedir pelo app terminou')

    def test_withdraw(self):
        self.ask('cancel')
        asked = AppointmentRequest.objects.get()
        self.client.post(reverse('core:my_agenda_request_withdraw', kwargs={'pk': asked.pk}))
        asked.refresh_from_db()
        self.assertEqual(asked.status, 'withdrawn')
        self.assertEqual(self.client.get(reverse('core:my_agenda_request_withdraw', kwargs={'pk': asked.pk})).status_code, 405)

    def test_cannot_withdraw_someone_elses_request(self):
        asked = class_requests.create_request(self.bob.person, Appointment.objects.create(
            person=self.bob.person, starts_at=future(4)), 'cancel')
        self.assertEqual(self.client.post(
            reverse('core:my_agenda_request_withdraw', kwargs={'pk': asked.pk})).status_code, 404)

    def test_declined_note_is_shown(self):
        asked = class_requests.create_request(self.person, self.appt, 'cancel')
        class_requests.decline(asked, 'Sem vaga')
        page = self.client.get(reverse('core:my_agenda'))
        self.assertContains(page, 'data-declined')
        self.assertContains(page, 'Sem vaga')

    def test_message_is_escaped(self):
        self.ask('cancel', message='<script>alert(1)</script>')
        page = self.client.get(reverse('core:my_agenda'))
        self.assertNotContains(page, '<script>alert(1)</script>')

    def test_query_count_does_not_grow_with_classes(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('core:my_agenda'))
            return len(ctx)
        base = count()
        for day in range(10, 20):
            asked = Appointment.objects.create(person=self.person, starts_at=future(day))
            class_requests.create_request(self.person, asked, 'cancel')
        self.assertEqual(count(), base)

    def test_today_shows_next_class_and_links(self):
        home = self.client.get(reverse('core:home'))
        self.assertContains(home, 'data-next-class')
        self.assertContains(home, reverse('core:my_agenda'))
        self.assertContains(self.client.get(reverse('core:my_profile')), reverse('core:my_agenda'))


class TrainerPagesTests(RequestBase):
    def setUp(self):
        super().setUp()
        self.asked = class_requests.create_request(self.person, self.appt, 'reschedule', future(5, 10), 'Consulta')
        self.client.force_login(self.trainer)

    def decide(self, decision, **extra):
        return self.client.post(reverse('core:appointment_request_decide', kwargs={'pk': self.asked.pk}),
                                {'decision': decision, **extra})

    def test_agenda_shows_pending_requests_and_badge(self):
        page = self.client.get(reverse('core:agenda'))
        self.assertContains(page, 'data-pending-requests')
        self.assertContains(page, 'Consulta')
        self.assertContains(page, reverse('core:appointment_request_decide', kwargs={'pk': self.asked.pk}))
        self.assertEqual(next(i for i in page.context['tabbar']['items'] if i['key'] == 'agenda')['badge'] >= 1, True)

    def test_approve_and_decline(self):
        self.decide('approve')
        self.asked.refresh_from_db()
        self.appt.refresh_from_db()
        self.assertEqual((self.asked.status, self.appt.status), ('approved', 'rescheduled'))
        self.assertNotContains(self.client.get(reverse('core:agenda')), 'data-pending-requests')

    def test_decline_keeps_the_class(self):
        self.decide('decline', note='Sem vaga')
        self.asked.refresh_from_db()
        self.appt.refresh_from_db()
        self.assertEqual((self.asked.status, self.asked.decision_note, self.appt.status), ('declined', 'Sem vaga', 'scheduled'))

    def test_bad_decision_and_method(self):
        self.assertEqual(self.decide('maybe').status_code, 400)
        self.assertEqual(self.client.get(reverse('core:appointment_request_decide', kwargs={'pk': self.asked.pk})).status_code, 405)

    def test_students_cannot_decide(self):
        self.client.force_login(self.alice)
        self.assertNotEqual(self.decide('approve').status_code, 200)
        self.asked.refresh_from_db()
        self.assertEqual(self.asked.status, 'pending')

    def test_month_view_chips_are_draggable(self):
        day = timezone.localtime(self.appt.starts_at).date()
        page = self.client.get(reverse('core:agenda'), {'vista': 'mes', 'dia': day.isoformat()})
        self.assertContains(page, f'data-agenda-day="{day.isoformat()}"')
        self.assertContains(page, 'draggable="true" data-appt-move')
        self.assertContains(page, 'month-chip s-scheduled')
