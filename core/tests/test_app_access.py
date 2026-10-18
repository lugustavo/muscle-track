from datetime import timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core import app_access, notifications
from core.dashboard import inactive_students, pending_invites
from core.invites import build_invite_path
from core.models import Appointment, NotificationPreference, Person, PersonRoutine, StudentEvent

from .base import BaseTestCase


def new_student_data(**extra):
    data = {'person_name': 'Dora Lima', 'email': 'dora@example.com', 'has_app': 'on', 'phone': '(11) 98765-4321'}
    data.update(extra)
    return data


class CreateTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:student_create')

    def test_a_student_without_the_app_needs_no_email_and_gets_a_dormant_account(self):
        data = new_student_data(email='')
        del data['has_app']
        response = self.client.post(self.url, data)
        person = Person.objects.get(person_name='Dora Lima')
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': person.pk}))   # no invite to hand out
        user = person.user
        self.assertFalse(person.has_app)
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertEqual((user.email, user.username.startswith('local-')), ('', True))
        self.assertFalse(EmailAddress.objects.filter(user=user).exists())

    def test_two_students_without_email_do_not_clash(self):
        for name in ('Dora Lima', 'Elisa Reis'):
            data = new_student_data(person_name=name, email='', phone='')
            del data['has_app']
            self.client.post(self.url, data)
        self.assertEqual(Person.objects.filter(has_app=False).count(), 2)

    def test_the_app_still_needs_an_email(self):
        response = self.client.post(self.url, new_student_data(email=''))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informe o e-mail para o aluno usar o app')
        self.assertFalse(Person.objects.filter(person_name='Dora Lima').exists())

    def test_an_email_given_without_the_app_is_kept_and_stays_unique(self):
        data = new_student_data(email='Dora@Example.com')
        del data['has_app']
        self.client.post(self.url, data)
        person = Person.objects.get(person_name='Dora Lima')
        self.assertEqual(person.user.email, 'dora@example.com')
        self.assertFalse(person.user.is_active)
        response = self.client.post(self.url, new_student_data(person_name='Outra', email='DORA@example.com', phone=''))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Person.objects.filter(person_name='Outra').exists())

    def test_with_the_app_nothing_changes(self):
        response = self.client.post(self.url, new_student_data(modality='online'))
        person = Person.objects.get(person_name='Dora Lima')
        self.assertRedirects(response, reverse('core:student_invite', kwargs={'pk': person.pk}))
        self.assertEqual((person.has_app, person.user.is_active, person.modality), (True, True, 'online'))

    def test_the_dormant_account_cannot_sign_in_or_reset_the_password(self):
        data = new_student_data(email='dora@example.com')
        del data['has_app']
        self.client.post(self.url, data)
        anonymous = Client()
        self.assertFalse(anonymous.login(username='dora@example.com', password='anything'))
        anonymous.post(reverse('account_reset_password'), {'email': 'dora@example.com'})
        # the same notice as for any address nobody registered: no reset link
        self.assertTrue(all('/reset/key/' not in m.body and 'password/reset' not in m.body.split('http', 1)[-1]
                            for m in mail.outbox))
        self.assertFalse(any('Redefin' in m.subject for m in mail.outbox))


class SwitchTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person
        self.url = reverse('core:student_edit', kwargs={'pk': self.person.pk})

    def edit(self, **extra):
        data = {'person_name': 'Alice', 'email': 'alice@example.com'}
        data.update(extra)
        return self.client.post(self.url, data)

    def turn_off(self):
        return self.edit()   # an unchecked box is not sent

    def test_turning_the_app_off_blocks_the_login_and_the_old_invites(self):
        invite = build_invite_path(self.alice)
        response = self.turn_off()
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.person.refresh_from_db()
        self.alice.refresh_from_db()
        self.assertFalse(self.person.has_app)
        self.assertFalse(self.alice.is_active)
        self.assertTrue(self.alice.username.startswith('local-'))
        self.assertEqual(self.alice.email, 'alice@example.com')           # kept for the day the app is turned on
        self.assertFalse(EmailAddress.objects.filter(user=self.alice).exists())
        self.assertEqual(Client().get(invite).status_code, 400)           # invites sent before are dead
        self.assertTrue(self.person.events.filter(kind=StudentEvent.Kind.APP_ACCESS).exists())
        self.assertFalse(Client().login(username='alice', password='pw-alice'))

    def test_turning_it_back_on_needs_the_email_and_hands_out_a_new_invite(self):
        self.turn_off()
        response = self.edit(has_app='on', email='')
        self.assertEqual(response.status_code, 200)
        response = self.edit(has_app='on', email='alice.nova@example.com')
        self.assertRedirects(response, reverse('core:student_invite', kwargs={'pk': self.person.pk}))
        self.person.refresh_from_db()
        self.alice.refresh_from_db()
        self.assertTrue(self.person.has_app)
        self.assertTrue(self.alice.is_active)
        self.assertEqual((self.alice.email, self.alice.username), ('alice.nova@example.com',) * 2)
        self.assertTrue(EmailAddress.objects.filter(user=self.alice, email='alice.nova@example.com').exists())
        self.assertEqual(self.person.events.filter(kind=StudentEvent.Kind.APP_ACCESS).count(), 2)

    def test_the_status_change_never_wakes_a_dormant_account(self):
        self.turn_off()
        self.person.refresh_from_db()
        self.person.set_status('paused')
        self.person.set_status('active')
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.is_active)

    def test_the_status_change_still_toggles_a_normal_account(self):
        self.person.set_status('paused')
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.is_active)
        self.person.set_status('active')
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.is_active)

    def test_the_invite_page_is_closed_for_a_student_without_the_app(self):
        self.turn_off()
        response = self.client.get(reverse('core:student_invite', kwargs={'pk': self.person.pk}))
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': self.person.pk}))

    def test_access_state(self):
        self.person.refresh_from_db()
        self.assertEqual(app_access.access_state(self.person), 'active')
        self.alice.set_unusable_password()
        self.alice.save()
        self.assertEqual(app_access.access_state(Person.objects.get(pk=self.person.pk)), 'invited')
        self.turn_off()
        self.assertEqual(app_access.access_state(Person.objects.get(pk=self.person.pk)), 'none')


class LocalStudentFixtures(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person
        self.person.has_app = False
        self.person.modality = 'in_person'
        self.person.save()
        app_access.apply(self.person, 'alice@example.com', True)

    def attended(self, days_ago):
        return Appointment.objects.create(person=self.person, status='attended',
                                          starts_at=timezone.now() - timedelta(days=days_ago))


class DashboardTests(LocalStudentFixtures):
    def test_no_invite_is_waiting_for_a_student_without_the_app(self):
        self.alice.set_unusable_password()
        self.alice.save()
        self.bob.person.has_app = False
        self.bob.person.save()
        self.assertEqual([r['student'] for r in pending_invites()], [])

    def test_inactivity_follows_the_classes_attended(self):
        old = timezone.now() - timedelta(days=30)
        Person.objects.filter(pk__in=[self.person.pk, self.bob.person.pk]).update(created_at=old)
        self.attended(2)
        names = {r['student'].person_name for r in inactive_students()}
        self.assertNotIn('Alice', names)      # a class two days ago
        self.assertIn('Bob', names)          # never trained in the app
        Appointment.objects.all().delete()
        self.attended(20)
        rows = {r['student'].person_name: r for r in inactive_students()}
        self.assertEqual(rows['Alice']['days'], 20)

    def test_a_workout_in_the_app_does_not_count_for_a_student_without_it(self):
        Person.objects.filter(pk=self.person.pk).update(created_at=timezone.now() - timedelta(days=30))
        from core.models import SessionExercise, WorkoutSession  # noqa: F401
        session = WorkoutSession.objects.create(person_routine=self.alice_routine, is_completed=True)
        self.assertTrue(session.pk)
        names = {r['student'].person_name for r in inactive_students()}
        self.assertIn('Alice', names)

    def test_onboarding_does_not_ask_for_an_invite_when_nobody_uses_the_app(self):
        from core.onboarding import trainer_steps
        Person.objects.update(has_app=False)
        step = next(s for s in trainer_steps()['steps'] if s['key'] == 'access')
        self.assertTrue(step['done'])


class ListAndFileTests(LocalStudentFixtures):
    def test_badges_and_class_numbers_in_the_list(self):
        self.attended(3)
        self.attended(10)
        for view in ('list', 'grid', 'table'):
            html = self.client.get(reverse('core:student_list'), {'view': view}).content.decode()
            self.assertIn('data-no-app', html, view)
            self.assertIn('data-modality="in_person"', html, view)
        html = self.client.get(reverse('core:student_list'), {'view': 'list'}).content.decode()
        self.assertIn('2 aulas', html)

    def test_filters(self):
        names = lambda **q: {p.person_name for p in self.client.get(reverse('core:student_list'), q).context['students']}  # noqa: E731
        self.assertEqual(names(filtro='no_app'), {'Alice'})
        self.assertNotIn('Alice', names(filtro='with_app'))
        self.assertIn('Bob', names(filtro='with_app'))
        self.assertEqual(names(filtro='in_person'), {'Alice'})
        self.assertEqual(names(filtro='online'), set())
        self.assertEqual(names(filtro='invite_pending'), set())

    def test_the_file_of_a_student_without_the_app(self):
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        html = page.content.decode()
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-no-app', html)
        self.assertIn('data-app-state="none"', html)
        self.assertNotIn('Convite pendente', html)
        self.assertNotIn(reverse('core:student_invite', kwargs={'pk': self.person.pk}), html)

    def test_every_trainer_page_of_the_student_works(self):
        pk = self.person.pk
        for name in ('student_detail', 'student_edit', 'student_status', 'student_history', 'student_package_sell',
                     'assessment_create', 'payment_create'):
            response = self.client.get(reverse(f'core:{name}', kwargs={'pk': pk}))
            self.assertEqual(response.status_code, 200, name)
        self.assertEqual(self.client.get(reverse('core:student_list'), {'search': 'alice'}).status_code, 200)
        self.assertEqual(self.client.get(reverse('core:search'), {'q': 'Alice'}).status_code, 200)

    def test_edit_form_does_not_ask_for_the_email_when_the_app_is_off(self):
        html = self.client.get(reverse('core:student_edit', kwargs={'pk': self.person.pk})).content.decode()
        self.assertIn('data-app-toggle', html)
        self.assertNotIn(' checked', html.split('data-app-toggle')[1].split('>')[0])


@override_settings(NOTIFICATIONS_ASYNC=False)
class NothingIsSentTests(LocalStudentFixtures):
    def test_notifications_skip_the_dormant_account(self):
        NotificationPreference.objects.update_or_create(user=self.alice, defaults={'email_enabled': True})
        notifications.notify([self.alice], notifications.test_message())
        self.assertEqual(len(mail.outbox), 0)
        NotificationPreference.objects.update_or_create(user=self.bob, defaults={'email_enabled': True})
        notifications.notify([self.bob], notifications.test_message())
        self.assertEqual(len(mail.outbox), 1)

    def test_a_routine_can_still_be_assigned(self):
        self.assertTrue(PersonRoutine.objects.filter(person=self.person).exists())
        self.assertEqual(self.client.get(reverse('core:person_routine_create'), {'person': self.person.pk}).status_code, 200)

    def test_the_user_row_stays_unique_per_student(self):
        self.assertEqual(User.objects.filter(person=self.person).count(), 1)
