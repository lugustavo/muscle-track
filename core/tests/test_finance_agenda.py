import io
import json
import zipfile
from datetime import date, time, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core.agenda import (
    BACKFILL_DAYS, at, attendance_stats, conflicting_ids, ensure_appointments, parse_week, week_start,
)
from core.models import Appointment, Payment, Person, ScheduleSlot

from .base import BaseTestCase


class AgendaRulesTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.today = timezone.localdate()
        self.monday = week_start(self.today)

    def slot(self, person=None, weekday=None, start=time(7, 0), **extra):
        return ScheduleSlot.objects.create(
            person=person or self.person, weekday=self.today.weekday() if weekday is None else weekday,
            start_time=start, **extra)

    def test_week_helpers(self):
        self.assertEqual(week_start(date(2026, 5, 13)), date(2026, 5, 11))  # Wednesday -> Monday
        self.assertEqual(week_start(date(2026, 5, 11)), date(2026, 5, 11))
        self.assertEqual(parse_week('2026-05-13'), date(2026, 5, 11))
        self.assertEqual(parse_week('lixo', self.today), self.monday)
        self.assertEqual(parse_week(None, self.today), self.monday)

    def test_generation_is_idempotent(self):
        self.slot()
        first = ensure_appointments(self.today, self.today + timedelta(days=13))
        self.assertEqual(first, 2)  # today + next week
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=13)), 0)
        self.assertEqual(Appointment.objects.count(), 2)
        self.assertTrue(all(a.slot for a in Appointment.objects.all()))

    def test_generated_class_uses_the_slot_time_in_local_time(self):
        self.slot(start=time(7, 30), duration_minutes=45)
        ensure_appointments(self.today, self.today)
        appointment = Appointment.objects.get()
        local = timezone.localtime(appointment.starts_at)
        self.assertEqual((local.date(), local.hour, local.minute), (self.today, 7, 30))
        self.assertEqual(appointment.duration_minutes, 45)

    def test_no_classes_for_students_without_access(self):
        self.slot()
        self.person.set_status('paused', 'viagem')
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=6)), 0)

    def test_not_before_valid_from_or_after_valid_until(self):
        self.slot(valid_from=self.today + timedelta(days=7))
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=6)), 0)
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=7)), 1)
        ScheduleSlot.objects.all().delete()
        Appointment.objects.all().delete()
        self.slot(valid_until=self.today + timedelta(days=6))
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=14)), 1)

    def test_backfill_is_limited(self):
        self.slot(valid_from=self.today - timedelta(days=60))
        created = ensure_appointments(self.today - timedelta(days=60), self.today)
        self.assertLessEqual(created, BACKFILL_DAYS // 7 + 1)
        oldest = min(a.starts_at for a in Appointment.objects.all())
        self.assertGreaterEqual(timezone.localtime(oldest).date(), self.today - timedelta(days=BACKFILL_DAYS))

    def test_pausing_removes_only_future_scheduled_classes(self):
        slot = self.slot()
        future = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=3), slot=slot)
        attended_future = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=4),
                                                     status='attended')
        past = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=3), slot=slot)
        self.person.set_status('former', 'mudou de cidade')
        self.assertFalse(Appointment.objects.filter(pk=future.pk).exists())
        self.assertTrue(Appointment.objects.filter(pk=attended_future.pk).exists())
        self.assertTrue(Appointment.objects.filter(pk=past.pk).exists())

    def test_conflicts(self):
        base = timezone.now().replace(hour=9, minute=0, second=0, microsecond=0)
        a = Appointment.objects.create(person=self.alice.person, starts_at=base)
        b = Appointment.objects.create(person=self.bob.person, starts_at=base + timedelta(minutes=30))
        c = Appointment.objects.create(person=self.alice.person, starts_at=base + timedelta(hours=3))
        self.assertEqual(conflicting_ids([a, b, c]), {a.pk, b.pk})
        b.status = 'cancelled'
        self.assertEqual(conflicting_ids([a, b, c]), set())
        b.status = 'scheduled'
        b.starts_at = base + timedelta(minutes=60)  # starts exactly when the first ends
        self.assertEqual(conflicting_ids([a, b, c]), set())

    def test_attendance_stats(self):
        now = timezone.now()
        for days, status in ((2, 'attended'), (5, 'attended'), (9, 'attended'), (12, 'missed'), (40, 'missed'),
                             (3, 'cancelled'), (6, 'rescheduled')):
            Appointment.objects.create(person=self.person, starts_at=now - timedelta(days=days), status=status)
        Appointment.objects.create(person=self.person, starts_at=now + timedelta(days=2), status='attended')  # future
        stats = attendance_stats(self.person, 30)
        self.assertEqual((stats['attended'], stats['missed'], stats['rate']), (3, 1, 75))
        self.assertIsNone(attendance_stats(self.bob.person, 30)['rate'])

    def test_deleting_a_slot_keeps_the_history(self):
        slot = self.slot()
        past = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=2),
                                          slot=slot, status='attended')
        slot.delete()
        past.refresh_from_db()
        self.assertIsNone(past.slot)
        self.assertEqual(past.status, 'attended')

    def test_unique_class_per_student_and_time(self):
        when = timezone.now().replace(microsecond=0)
        Appointment.objects.create(person=self.person, starts_at=when)
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Appointment.objects.create(person=self.person, starts_at=when)

    def test_management_command(self):
        from django.core.management import call_command
        self.slot()
        out = io.StringIO()
        call_command('generate_appointments', '--weeks', '3', stdout=out)
        self.assertEqual(Appointment.objects.count(), 4)
        self.assertIn('4 aula(s)', out.getvalue())


class AgendaViewTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.today = timezone.localdate()
        self.login(self.trainer)

    def make(self, person=None, hour=9, status='scheduled', **extra):
        start = at(self.today, time(hour, 0))
        return Appointment.objects.create(person=person or self.person, starts_at=start, status=status, **extra)

    def test_week_view_generates_and_lists_classes(self):
        ScheduleSlot.objects.create(person=self.person, weekday=self.today.weekday(), start_time=time(7, 0))
        response = self.client.get(reverse('core:agenda'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertContains(response, 'Alice')
        self.assertContains(response, '07:00')
        self.client.get(reverse('core:agenda'))  # opening again does not duplicate
        self.assertEqual(Appointment.objects.count(), 1)

    def test_week_navigation_and_bad_values(self):
        for value in ('', 'abc', '2026-99-99'):
            self.assertEqual(self.client.get(reverse('core:agenda'), {'semana': value}).status_code, 200, value)
        nxt = (week_start(self.today) + timedelta(days=7)).isoformat()
        response = self.client.get(reverse('core:agenda'), {'semana': nxt})
        self.assertEqual(response.context['monday'].isoformat(), nxt)
        self.assertContains(response, 'Hoje')  # link back to the current week

    def test_status_actions(self):
        a = self.make()
        url = reverse('core:appointment_status', kwargs={'pk': a.pk})
        for status in ('attended', 'missed', 'cancelled', 'scheduled'):
            response = self.client.post(url, {'status': status})
            self.assertEqual(response.status_code, 302)
            a.refresh_from_db()
            self.assertEqual(a.status, status)
        self.assertIsNotNone(a.status_changed_at)
        for bad in ('rescheduled', 'dancing', ''):
            self.assertEqual(self.client.post(url, {'status': bad}).status_code, 400, bad)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_status_redirect_keeps_the_week(self):
        a = self.make()
        url = reverse('core:appointment_status', kwargs={'pk': a.pk}) + '?next=/agenda/%3Fsemana%3D2026-05-11'
        self.assertRedirects(self.client.post(url, {'status': 'attended'}), '/agenda/?semana=2026-05-11',
                             fetch_redirect_response=False)
        evil = reverse('core:appointment_status', kwargs={'pk': a.pk}) + '?next=https://evil.example/'
        self.assertNotIn('evil', self.client.post(evil, {'status': 'missed'}).url)

    def test_create_one_off_class(self):
        url = reverse('core:appointment_create')
        response = self.client.post(url, {
            'person': self.person.pk, 'date': self.today.isoformat(), 'time': '18:30', 'duration_minutes': 60,
            'kind': 'trial', 'notes': 'primeira aula'})
        self.assertEqual(response.status_code, 302)
        a = Appointment.objects.get()
        self.assertEqual((a.kind, a.slot), ('trial', None))
        self.assertEqual(timezone.localtime(a.starts_at).strftime('%H:%M'), '18:30')
        again = self.client.post(url, {'person': self.person.pk, 'date': self.today.isoformat(), 'time': '18:30',
                                       'duration_minutes': 60, 'kind': 'trial'})
        self.assertEqual(again.status_code, 200)
        self.assertIn('time', again.context['form'].errors)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_create_validates_and_only_offers_active_students(self):
        self.bob.person.set_status('paused', 'x')
        form = self.client.get(reverse('core:appointment_create'), {'person': self.person.pk}).context['form']
        self.assertEqual(list(form.fields['person'].queryset), [self.person])
        self.assertEqual(form.initial['person'], self.person.pk)
        response = self.client.post(reverse('core:appointment_create'), {
            'person': self.person.pk, 'date': 'nope', 'time': '99:99', 'duration_minutes': 5, 'kind': 'x'})
        self.assertEqual(set(response.context['form'].errors), {'date', 'time', 'duration_minutes', 'kind'})

    def test_overlap_warns_but_saves(self):
        self.make(person=self.bob.person, hour=9)
        response = self.client.post(reverse('core:appointment_create'), {
            'person': self.person.pk, 'date': self.today.isoformat(), 'time': '09:30', 'duration_minutes': 60,
            'kind': 'regular'}, follow=True)
        self.assertContains(response, 'se sobrepõe a outra aula (Bob)')
        self.assertEqual(Appointment.objects.count(), 2)
        page = self.client.get(reverse('core:agenda'))
        self.assertContains(page, 'Sobreposta a outra aula')

    def test_reschedule(self):
        a = self.make(status='missed')
        url = reverse('core:appointment_reschedule', kwargs={'pk': a.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        tomorrow = self.today + timedelta(days=1)
        response = self.client.post(url, {'person': self.person.pk, 'date': tomorrow.isoformat(), 'time': '10:00',
                                          'duration_minutes': 60, 'kind': 'makeup'})
        self.assertEqual(response.status_code, 302)
        a.refresh_from_db()
        self.assertEqual(a.status, 'rescheduled')
        new = Appointment.objects.exclude(pk=a.pk).get()
        self.assertEqual((new.status, new.person, new.kind), ('scheduled', self.person, 'makeup'))
        self.assertEqual(timezone.localtime(new.starts_at).date(), tomorrow)
        # a rescheduled class cannot be rescheduled again, and the same time is refused
        self.assertEqual(self.client.get(url).status_code, 404)
        same = self.client.post(reverse('core:appointment_reschedule', kwargs={'pk': new.pk}), {
            'person': self.person.pk, 'date': tomorrow.isoformat(), 'time': '10:00', 'duration_minutes': 60,
            'kind': 'makeup'})
        self.assertEqual(same.status_code, 200)

    def test_delete_only_one_off_classes(self):
        slot = ScheduleSlot.objects.create(person=self.person, weekday=0, start_time=time(7, 0))
        fixed = self.make(slot=slot)
        one_off = self.make(hour=15)
        self.client.post(reverse('core:appointment_delete', kwargs={'pk': fixed.pk}))
        self.assertTrue(Appointment.objects.filter(pk=fixed.pk).exists())
        self.client.post(reverse('core:appointment_delete', kwargs={'pk': one_off.pk}))
        self.assertFalse(Appointment.objects.filter(pk=one_off.pk).exists())

    def test_cancelled_fixed_class_does_not_come_back(self):
        ScheduleSlot.objects.create(person=self.person, weekday=self.today.weekday(), start_time=time(7, 0))
        self.client.get(reverse('core:agenda'))
        a = Appointment.objects.get()
        self.client.post(reverse('core:appointment_status', kwargs={'pk': a.pk}), {'status': 'cancelled'})
        self.client.get(reverse('core:agenda'))
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(Appointment.objects.get().status, 'cancelled')

    def test_slots_from_the_students_file(self):
        add = reverse('core:slot_add', kwargs={'pk': self.person.pk})
        data = {'weekday': 2, 'start_time': '07:00', 'duration_minutes': 60}
        response = self.client.post(add, data)
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': self.person.pk}) + '#tab-agenda')
        self.assertEqual(self.person.slots.count(), 1)
        self.assertTrue(self.person.appointments.exists())  # upcoming classes are there right away
        self.client.post(add, data)  # the same slot again does not duplicate
        self.assertEqual(self.person.slots.count(), 1)
        bad = self.client.post(add, {'weekday': 9, 'start_time': 'x', 'duration_minutes': 1}, follow=True)
        self.assertContains(bad, 'Horário inválido')
        self.assertEqual(self.person.slots.count(), 1)
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'id="tab-agenda"')
        self.assertContains(page, 'Quarta')
        slot = self.person.slots.get()
        self.client.post(reverse('core:slot_delete', kwargs={'pk': slot.pk}))
        self.assertFalse(self.person.slots.exists())
        self.assertFalse(self.person.appointments.filter(status='scheduled', starts_at__gt=timezone.now()).exists())

    def test_attendance_block_on_the_file(self):
        Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=2), status='attended')
        Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=4), status='missed')
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, '50%')

    def test_dashboard_cards(self):
        self.make(hour=9)
        self.make(person=self.bob.person, hour=10, status='cancelled')
        miss = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=2), status='missed')
        Person.objects.filter(pk=self.person.pk).update(phone='(11) 98765-4321')
        page = self.client.get(reverse('core:home'))
        self.assertContains(page, 'Aulas de hoje')
        self.assertContains(page, '09:00')
        self.assertNotContains(page, '10:00')  # cancelled classes are not listed
        self.assertContains(page, 'Faltas dos últimos 7 dias')
        self.assertContains(page, 'Sentimos%20sua%20falta')
        self.assertContains(page, reverse('core:appointment_reschedule', kwargs={'pk': miss.pk}))

    def test_missed_class_older_than_a_week_is_not_in_the_card(self):
        Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=10), status='missed')
        self.assertNotContains(self.client.get(reverse('core:home')), 'Faltas dos últimos 7 dias')

    def test_students_cannot_use_the_agenda(self):
        a = self.make()
        urls = [('get', reverse('core:agenda')), ('get', reverse('core:appointment_create')),
                ('get', reverse('core:appointment_reschedule', kwargs={'pk': a.pk})),
                ('post', reverse('core:appointment_status', kwargs={'pk': a.pk})),
                ('post', reverse('core:appointment_delete', kwargs={'pk': a.pk})),
                ('post', reverse('core:slot_add', kwargs={'pk': self.person.pk}))]
        self.login(self.alice)
        for method, url in urls:
            self.assertEqual(getattr(self.client, method)(url).status_code, 403, url)
        self.client.logout()
        for method, url in urls:
            self.assertEqual(getattr(self.client, method)(url).status_code, 302, url)
        a.refresh_from_db()
        self.assertEqual(a.status, 'scheduled')

    def test_menu_has_agenda_and_finance_for_the_trainer_only(self):
        page = self.client.get(reverse('core:home'))
        self.assertContains(page, reverse('core:agenda'))
        self.assertContains(page, reverse('core:plan_list'))
        self.login(self.alice)
        page = self.client.get(reverse('core:home'))
        self.assertNotContains(page, reverse('core:agenda'))
        self.assertNotContains(page, reverse('core:finance'))


class AgendaPrivacyTests(BaseTestCase):
    def test_exports_include_payments_and_classes_and_deletion_removes_them(self):
        person = self.alice.person
        Payment.objects.create(person=person, plan_name='Mensal', amount=Decimal('300'), paid_on=date(2026, 5, 10),
                               period_start=date(2026, 5, 10), period_end=date(2026, 6, 9))
        slot = ScheduleSlot.objects.create(person=person, weekday=0, start_time=time(7, 0))
        Appointment.objects.create(person=person, starts_at=timezone.now(), slot=slot, status='attended')
        self.login(self.alice)
        response = self.client.post(reverse('core:my_export'))
        data = json.loads(zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))).read('dados.json'))
        self.assertEqual(data['payments'][0]['amount'], 300.0)
        self.assertEqual(data['schedule']['weekly_slots'][0]['weekday'], 0)
        self.assertEqual(data['schedule']['appointments'][0]['status'], 'attended')
        self.login(self.trainer)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('core:student_delete', kwargs={'pk': person.pk}))
        for model in (Payment, ScheduleSlot, Appointment):
            self.assertFalse(model.objects.exists(), model)
