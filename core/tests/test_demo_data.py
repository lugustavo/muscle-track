from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import app_access, class_requests, consent, demo_data, packages
from core.models import (
    Appointment, Person, Refund, ScheduleSlot, StudentEvent, StudentPackage, WorkoutSession,
)


@override_settings(HOLIDAYS_REGION='')
class DemoDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.summary, cls.password = demo_data.populate('demo-pass-123')
        cls.trainer = User.objects.get(username='personal')

    def person(self, first_name):
        return Person.objects.get(person_name__startswith=first_name)

    def current(self, first_name):
        return packages.overview(self.person(first_name))['current']

    def test_loads_once_and_reports_what_it_made(self):
        self.assertEqual(self.summary['alunos'], 18)
        self.assertTrue(self.summary['trainer_created'])
        self.assertEqual(demo_data.populate('other'), (None, None))   # already loaded: nothing changes
        self.assertEqual(Person.objects.count(), 18)

    def test_uses_an_existing_trainer_instead_of_creating_one(self):
        User.objects.all().delete()
        owner = User.objects.create_user('dona', 'dona@example.com', 'x', is_staff=True)
        from core.models import Package
        Package.objects.all().delete()
        summary, _ = demo_data.populate('abc')
        self.assertFalse(summary['trainer_created'])
        self.assertFalse(User.objects.filter(username='personal').exists())
        self.assertTrue(Person.objects.get(person_name='Ana Ferreira').events.filter(actor=owner).exists())

    def test_every_package_state_is_covered(self):
        self.assertEqual(self.current('Ana')['state'], 'active')
        self.assertEqual(self.current('Karina')['state'], 'frozen')
        self.assertEqual(self.current('Joana')['state'], 'expired')
        self.assertEqual(self.person('Joana').plan_status, 'expired')
        self.assertEqual(self.person('Carla').plan_status, 'expiring')
        self.assertTrue(StudentPackage.objects.filter(cancelled_at__isnull=False).exists())      # Fábio
        self.assertTrue(StudentPackage.objects.filter(valid_days__isnull=True).exists())        # Nuno: no deadline
        self.assertEqual(packages.overview(self.person('Eva'))['current'], None)               # finished package

    def test_money_cases(self):
        pending = {row['student'].person_name: row['amount'] for row in packages.pending_payments()}
        self.assertEqual(pending['Bruno Costa'], 154)
        self.assertEqual(pending['Olívia Torres'], 280)
        self.assertEqual(packages.refund_due_count(), 1)                                         # Luís paid too much
        refund = Refund.objects.get(person=self.person('Fábio'))
        self.assertEqual(refund.reason, 'Mudou de cidade')
        kinds = {row['kind'] for row in packages.renewals()}
        self.assertEqual(kinds, {'expired', 'expiring', 'low'})

    def test_shared_package_has_three_people_on_one_balance(self):
        sold = StudentPackage.objects.get(person=self.person('Gabriela'))
        self.assertEqual(len(packages.participant_ids(sold)), 3)
        self.assertEqual(packages.overview(self.person('Inês'))['current']['used'], 9)

    def test_student_lifecycle_and_access_cases(self):
        self.assertEqual(self.person('Eva').status, 'paused')
        self.assertEqual(self.person('Fábio').status, 'former')
        self.assertEqual(self.person('Tiago').status, 'trial')
        self.assertFalse(self.person('Bruno').has_app)
        self.assertEqual(app_access.access_state(self.person('Diego')), 'invited')
        self.assertFalse(consent.has_current_consent(self.person('Diego')))
        self.assertTrue(self.person('Marta').events.filter(kind=StudentEvent.Kind.ERASURE_REQUEST).exists())
        self.assertIsNone(self.person('Rui').intro_seen_at)

    def test_agenda_cases(self):
        today = timezone.localdate()
        kinds = set(Appointment.objects.values_list('kind', flat=True))
        statuses = set(Appointment.objects.values_list('status', flat=True))
        self.assertTrue({'regular', 'trial', 'makeup'} <= kinds)
        self.assertTrue({'scheduled', 'attended', 'missed', 'cancelled', 'rescheduled'} <= statuses)
        self.assertTrue(Appointment.objects.filter(cancelled_late=True).exists())
        self.assertTrue(Appointment.objects.filter(starts_at__date=today, person=self.person('Ana')).exists())
        self.assertTrue(ScheduleSlot.objects.exclude(month_rule='').exists())                    # Nuno's monthly series
        self.assertEqual(class_requests.pending_for_trainer().count(), 2)
        self.assertEqual(sorted(class_requests.pending_for_trainer().values_list('kind', flat=True)), ['cancel', 'reschedule'])

    def test_workouts_and_evolution(self):
        ana = self.person('Ana')
        self.assertEqual(ana.assessments.count(), 3)
        self.assertEqual(ana.progress_photos.count(), 12)
        self.assertTrue(WorkoutSession.objects.filter(person_routine__person=ana, is_completed=False).exists())   # running now
        self.assertGreater(ana.get_total_workouts(), 15)
        self.assertTrue(hasattr(ana, 'sheet_link'))

    def test_the_main_screens_open_for_the_trainer_and_for_a_student(self):
        self.client.force_login(self.trainer)
        for name in ('core:home', 'core:student_list', 'core:finance', 'core:finance_refunds', 'core:agenda', 'core:exercise_list',
                     'core:routine_list', 'core:plan_list'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for person in Person.objects.all():
            self.assertEqual(self.client.get(reverse('core:student_detail', kwargs={'pk': person.pk})).status_code, 200, person)
            self.assertEqual(self.client.get(reverse('core:student_history', kwargs={'pk': person.pk})).status_code, 200, person)
        self.client.force_login(self.person('Ana').user)
        for name in ('core:home', 'core:my_workouts', 'core:my_history', 'core:my_package', 'core:my_evolution', 'core:my_agenda'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_logins_work_with_the_printed_password(self):
        self.assertTrue(self.client.login(username='ana.ferreira@example.com', password='demo-pass-123'))
        self.client.logout()
        self.assertTrue(self.client.login(username='personal', password='demo-pass-123'))
        self.assertEqual(self.password, 'demo-pass-123')
