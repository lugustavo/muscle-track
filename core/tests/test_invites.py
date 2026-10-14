from django.contrib.auth.models import User
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from allauth.account.models import EmailAddress

from core.consent import ALL_KINDS, record_consent
from core.models import Person

from .base import BaseTestCase


def record_all_consents(person):
    for kind in ALL_KINDS:
        record_consent(person, kind, True)


class StudentCreationTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)

    def create(self, **extra):
        data = {'person_name': 'Carla Souza', 'email': 'Carla@Example.com', 'phone': '(11) 98765-4321',
                'is_active': 'on'}
        data.update(extra)
        return self.client.post(reverse('core:student_create'), data)

    def test_creates_passwordless_account_and_redirects_to_invite(self):
        response = self.create()
        person = Person.objects.get(person_name='Carla Souza')
        self.assertRedirects(response, reverse('core:student_invite', kwargs={'pk': person.pk}))
        self.assertEqual(person.user.email, 'carla@example.com')
        self.assertFalse(person.user.has_usable_password())
        self.assertTrue(person.user.is_active)
        self.assertEqual(len(mail.outbox), 0)  # no SMTP needed

    def test_email_must_be_unique_ignoring_case(self):
        response = self.create(email='ALICE@example.com')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Person.objects.filter(person_name='Carla Souza').exists())

    def test_invite_page_shows_link_and_whatsapp(self):
        self.create()
        person = Person.objects.get(person_name='Carla Souza')
        response = self.client.get(reverse('core:student_invite', kwargs={'pk': person.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertIn('/invite/', response.context['invite_url'])
        self.assertTrue(response.context['whatsapp_url'].startswith('https://wa.me/5511987654321?text='))
        self.assertFalse(response.context['has_password'])

    @override_settings(SITE_URL='https://treino.example.com/')
    def test_site_url_setting_is_used_when_configured(self):
        response = self.client.get(reverse('core:student_invite', kwargs={'pk': self.alice.person.pk}))
        self.assertTrue(response.context['invite_url'].startswith('https://treino.example.com/invite/'))

    def test_students_cannot_see_invites(self):
        self.login(self.alice)
        response = self.client.get(reverse('core:student_invite', kwargs={'pk': self.bob.person.pk}))
        self.assertEqual(response.status_code, 403)


class AcceptInviteTests(BaseTestCase):
    def setUp(self):
        self.user = User(username='nova@example.com', email='nova@example.com')
        self.user.set_unusable_password()
        self.user.save()
        self.person = Person.objects.create(user=self.user, person_name='Nova Aluna')
        self.client.logout()
        self.login(self.trainer)
        self.invite_url = self.client.get(
            reverse('core:student_invite', kwargs={'pk': self.person.pk})).context['invite_url']
        self.path = self.invite_url[self.invite_url.index('/invite/'):]
        self.client.logout()

    def test_get_shows_form(self):
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nova Aluna')

    def test_student_sets_password_and_is_logged_in(self):
        response = self.client.post(self.path, {'new_password1': 'Sup3r-segura-123', 'new_password2': 'Sup3r-segura-123'})
        # first access: the consent screen comes before the workouts (see test_lifecycle_lgpd)
        self.assertRedirects(response, reverse('core:my_workouts'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Sup3r-segura-123'))
        self.assertTrue(EmailAddress.objects.get(user=self.user).verified)
        record_all_consents(self.user.person)
        self.assertEqual(self.client.get(reverse('core:my_workouts')).status_code, 200)

        # and can log in normally afterwards
        self.client.logout()
        self.assertTrue(self.client.login(username='nova@example.com', password='Sup3r-segura-123'))

    def test_link_works_only_once(self):
        self.client.post(self.path, {'new_password1': 'Sup3r-segura-123', 'new_password2': 'Sup3r-segura-123'})
        self.client.logout()
        self.assertEqual(self.client.get(self.path).status_code, 400)
        response = self.client.post(self.path, {'new_password1': 'Outra-senha-456', 'new_password2': 'Outra-senha-456'})
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Sup3r-segura-123'))

    def test_weak_or_mismatched_password_is_rejected(self):
        for p1, p2 in (('123', '123'), ('Sup3r-segura-123', 'diferente-456-xx')):
            with self.subTest(p1=p1):
                response = self.client.post(self.path, {'new_password1': p1, 'new_password2': p2})
                self.assertEqual(response.status_code, 200)
                self.user.refresh_from_db()
                self.assertFalse(self.user.has_usable_password())

    def test_garbage_and_expired_links_are_rejected(self):
        for path in ('/invite/xx/yy/', '/invite/!!!/abc-def/', self.path[:-2] + 'zz/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 400)

    def test_inactive_student_cannot_accept(self):
        Person.objects.filter(pk=self.person.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.path).status_code, 403)

    def test_trainer_accounts_cannot_be_taken_over(self):
        from core.invites import build_invite_path
        self.assertEqual(self.client.get(build_invite_path(self.trainer)).status_code, 400)

    def test_resetting_access_for_student_with_password(self):
        self.login(self.alice)
        self.client.logout()
        self.login(self.trainer)
        response = self.client.get(reverse('core:student_invite', kwargs={'pk': self.alice.person.pk}))
        self.assertTrue(response.context['has_password'])
        path = response.context['invite_url'][response.context['invite_url'].index('/invite/'):]
        self.client.logout()
        self.client.post(path, {'new_password1': 'Nova-senha-789x', 'new_password2': 'Nova-senha-789x'})
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.check_password('Nova-senha-789x'))


class StudentEditTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)

    def edit(self, student, **extra):
        data = {'person_name': student.person.person_name, 'email': student.email, 'is_active': 'on'}
        data.update(extra)
        return self.client.post(reverse('core:student_edit', kwargs={'pk': student.person.pk}), data)

    def test_deactivating_blocks_login_and_reactivating_restores(self):
        status_url = reverse('core:student_status', kwargs={'pk': self.alice.person.pk})
        self.client.post(status_url, {'status': 'paused', 'reason': 'Viagem'})
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.is_active)
        self.client.logout()
        self.assertFalse(self.client.login(username='alice', password='pw-alice'))

        self.login(self.trainer)
        self.client.post(status_url, {'status': 'active', 'reason': ''})
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.is_active)
        self.client.logout()
        self.assertTrue(self.client.login(username='alice', password='pw-alice'))

    def test_changing_email_updates_login_without_sending_mail(self):
        self.edit(self.alice, email='alice.nova@example.com')
        self.alice.refresh_from_db()
        self.assertEqual((self.alice.email, self.alice.username), ('alice.nova@example.com',) * 2)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(EmailAddress.objects.filter(user=self.alice, email='alice.nova@example.com').exists())


class SignupClosedTests(BaseTestCase):
    def test_public_signup_is_closed_by_default(self):
        response = self.client.post(reverse('account_signup'), {
            'email': 'intruso@example.com', 'password1': 'Sup3r-segura-123', 'password2': 'Sup3r-segura-123'})
        self.assertNotEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(email='intruso@example.com').exists())

    @override_settings(ALLOW_PUBLIC_SIGNUP=True)
    def test_signup_can_be_reopened(self):
        self.client.post(reverse('account_signup'), {
            'email': 'aberto@example.com', 'password1': 'Sup3r-segura-123', 'password2': 'Sup3r-segura-123'})
        self.assertTrue(User.objects.filter(email='aberto@example.com').exists())

    def test_login_page_has_no_signup_link(self):
        self.assertNotContains(self.client.get(reverse('account_login')), reverse('account_signup'))
