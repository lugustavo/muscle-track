from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from core.models import Appointment, ScheduleSlot

from .base import BaseTestCase


class ClassRowTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.client.force_login(self.trainer)
        self.slot = ScheduleSlot.objects.create(person=self.person, weekday=1, start_time=timezone.now().time(),
                                                valid_from=timezone.localdate())
        self.fixed = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=3), slot=self.slot)
        self.single = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=4))

    def page(self):
        return self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).content.decode()

    def url(self, name, a):
        return reverse(f'core:{name}', kwargs={'pk': a.pk})

    def test_each_upcoming_class_has_edit_and_remove(self):
        html = self.page()
        self.assertEqual(html.count('data-swipe-row'), 2)
        for a in (self.fixed, self.single):
            self.assertIn(f'data-class="{a.pk}"', html)
            self.assertIn(self.url('appointment_reschedule', a), html)
            self.assertIn(self.url('appointment_remove', a), html)
        self.assertIn('swipe-rows.css', html)
        self.assertIn('swipe-rows.js', html)
        self.assertIn('Deslize a aula para a esquerda', html)          # the phone hint

    def test_confirmation_text_depends_on_the_kind_of_class(self):
        html = self.page()
        self.assertIn('Remover esta aula avulsa?', html)
        self.assertIn('não desconta do pacote', html)

    def test_remove_deletes_a_one_off_class(self):
        response = self.client.post(self.url('appointment_remove', self.single))
        self.assertIn('#tab-agenda', response['Location'])
        self.assertFalse(Appointment.objects.filter(pk=self.single.pk).exists())

    def test_remove_cancels_a_fixed_time_class_without_discount(self):
        self.client.post(self.url('appointment_remove', self.fixed))
        self.fixed.refresh_from_db()
        self.assertEqual((self.fixed.status, self.fixed.cancelled_late, self.fixed.counts_override), ('cancelled', False, False))
        self.assertFalse(self.fixed.counts_against_package)
        self.assertNotIn(f'data-class="{self.fixed.pk}"', self.page())    # gone from the list
        from core.agenda import ensure_appointments
        ensure_appointments(timezone.localdate(), timezone.localdate() + timedelta(days=30))
        self.assertEqual(Appointment.objects.filter(person=self.person, starts_at=self.fixed.starts_at, status='scheduled').count(), 0)

    def test_removing_a_class_close_to_the_start_is_still_free(self):
        near = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=2), slot=self.slot)
        self.client.post(self.url('appointment_remove', near))
        near.refresh_from_db()
        self.assertFalse(near.counts_against_package)

    def test_only_marked_classes_are_removed_here(self):
        self.single.status = 'attended'
        self.single.save()
        self.client.post(self.url('appointment_remove', self.single))
        self.assertTrue(Appointment.objects.filter(pk=self.single.pk).exists())

    def test_the_student_is_told_when_a_class_is_taken_off(self):
        from django.core import mail
        from django.test import override_settings
        from core.models import NotificationPreference
        NotificationPreference.objects.create(user=self.alice, email_enabled=True)
        with override_settings(NOTIFICATIONS_ASYNC=False):
            self.client.post(self.url('appointment_remove', self.fixed))
        self.assertEqual(len(mail.outbox), 1)

    def test_post_only_and_trainer_only(self):
        self.assertEqual(self.client.get(self.url('appointment_remove', self.single)).status_code, 405)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(self.url('appointment_remove', self.single)).status_code, 403)
        self.assertTrue(Appointment.objects.filter(pk=self.single.pk).exists())

    def test_edit_from_the_students_file_returns_to_it(self):
        student = reverse('core:student_detail', kwargs={'pk': self.person.pk})
        later = timezone.localtime(self.single.starts_at + timedelta(days=1))
        response = self.client.post(f"{self.url('appointment_reschedule', self.single)}?next={student}", {
            'person': self.person.pk, 'date': later.date().isoformat(), 'time': '09:00', 'duration_minutes': 60, 'kind': 'regular',
            'notes': ''})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], student + '#tab-agenda')
