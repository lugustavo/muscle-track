import os
import subprocess
import sys
from unittest import mock

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, override_settings
from django.urls import reverse

from core.adapters import SocialAccountAdapter

from .base import BaseTestCase

GOOGLE = [{'id': 'google', 'name': 'Google', 'login_url_name': 'account_login'}]    # any existing URL name will do
ALL = GOOGLE + [{'id': 'facebook', 'name': 'Facebook', 'login_url_name': 'account_login'},
                {'id': 'twitter_oauth2', 'name': 'X', 'login_url_name': 'account_login'}]


class LandingTests(BaseTestCase):
    def test_visitor_sees_the_landing_with_the_login_card(self):
        response = self.client.get(reverse('core:home'))
        self.assertTemplateUsed(response, 'core/landing.html')
        html = response.content.decode()
        self.assertIn('Sua rotina de personal', html)
        self.assertIn('Seus alunos, sempre por dentro', html)
        self.assertIn('data-login-card', html)
        self.assertIn('data-for="trainer"', html)
        self.assertIn('data-for="student"', html)
        self.assertIn('Acesso por convite', html)
        self.assertIn(f'action="{reverse("account_login")}"', html)

    def test_title_and_description_speak_to_personal_trainers(self):
        html = self.client.get(reverse('core:home')).content.decode()
        self.assertIn('<title>Muscle Track - Gestão para personal trainers', html)
        self.assertIn('<meta name="description" content="Gestão para personal trainers', html)

    def test_logged_in_users_do_not_get_the_landing(self):
        for user in (self.alice, self.trainer):
            self.login(user)
            self.assertTemplateNotUsed(self.client.get(reverse('core:home')), 'core/landing.html')

    def test_other_pages_still_have_a_description(self):
        self.login(self.trainer)
        self.assertIn('<meta name="description"', self.client.get(reverse('core:student_list')).content.decode())

    def test_login_screen_shares_the_card(self):
        response = self.client.get(reverse('account_login'))
        self.assertContains(response, 'data-login-card')
        self.assertContains(response, 'A mesma entrada serve')

    def test_login_screen_keeps_the_next_page(self):
        html = self.client.get(reverse('account_login'), {'next': '/agenda/'}).content.decode()
        self.assertIn('name="next" value="/agenda/"', html)

    def test_wrong_password_shows_the_error_on_the_card(self):
        response = self.client.post(reverse('account_login'), {'login': 'alice@example.com', 'password': 'nope'})
        self.assertContains(response, 'data-login-card')
        self.assertContains(response, 'alert-danger')

    def test_password_login_still_works_from_the_landing_form(self):
        response = self.client.post(reverse('account_login'), {'login': 'alice@example.com', 'password': 'pw-alice'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)


class StudioNameTests(BaseTestCase):
    def test_default_brand(self):
        html = self.client.get(reverse('core:home')).content.decode()
        self.assertNotIn('data-studio-name', html)
        self.assertIn('Gestão para personal trainers', html)

    @override_settings(STUDIO_NAME='Studio da Ana')
    def test_studio_name_replaces_the_brand_everywhere(self):
        html = self.client.get(reverse('core:home')).content.decode()
        self.assertIn('data-studio-name', html)
        self.assertIn('<title>Studio da Ana - Gestão', html)
        self.assertIn('content="Studio da Ana"', html)                 # apple-mobile-web-app-title
        self.assertIn('feito com Muscle Track', html)
        self.login(self.alice)
        self.assertIn('Studio da Ana', self.client.get(reverse('core:my_workouts')).content.decode())

    @override_settings(STUDIO_NAME='Studio da Ana')
    def test_installed_app_uses_the_name(self):
        manifest = self.client.get(reverse('core:pwa_manifest')).json()
        self.assertEqual((manifest['name'], manifest['short_name']), ('Studio da Ana', 'Studio da Ana'))
        self.assertIn('personal trainers', manifest['description'])

    @override_settings(STUDIO_NAME='Estúdio Movimento Total Ltda')
    def test_short_name_is_cut_for_the_home_screen(self):
        manifest = self.client.get(reverse('core:pwa_manifest')).json()
        self.assertEqual(manifest['short_name'], 'Estúdio')               # whole words only, up to 14 characters

    def test_app_name_in_the_manifest_without_a_studio(self):
        self.assertEqual(self.client.get(reverse('core:pwa_manifest')).json()['name'], 'Muscle Track')


class SsoButtonsTests(BaseTestCase):
    @override_settings(SSO_PROVIDERS=[])
    def test_nothing_is_shown_when_no_provider_is_configured(self):
        for url in (reverse('core:home'), reverse('account_login')):
            html = self.client.get(url).content.decode()
            self.assertNotIn('data-sso', html)
            self.assertNotIn('ou com e-mail', html)

    @override_settings(SSO_PROVIDERS=GOOGLE)
    def test_only_the_configured_provider_appears(self):
        html = self.client.get(reverse('account_login')).content.decode()
        self.assertEqual(html.count('data-sso="'), 1)
        self.assertIn('data-sso="google"', html)
        self.assertIn('Continuar com Google', html)
        self.assertNotIn('Facebook', html)
        self.assertIn('ou com e-mail', html)

    @override_settings(SSO_PROVIDERS=ALL)
    def test_all_three_on_the_landing_and_the_login(self):
        for url in (reverse('core:home'), reverse('account_login')):
            html = self.client.get(url).content.decode()
            for provider in ('google', 'facebook', 'twitter_oauth2'):
                self.assertIn(f'data-sso="{provider}"', html)
            self.assertIn('Continuar com X', html)

    @override_settings(SSO_PROVIDERS=ALL)
    def test_buttons_are_post_forms_with_csrf(self):
        html = self.client.get(reverse('account_login')).content.decode()
        self.assertIn('method="post"', html)
        self.assertGreaterEqual(html.count('csrfmiddlewaretoken'), 4)       # three buttons + the password form

    @override_settings(SSO_PROVIDERS=GOOGLE)
    def test_steps_mention_the_providers(self):
        self.assertIn('entrar também com Google', self.client.get(reverse('core:home')).content.decode())

    @override_settings(SSO_PROVIDERS=ALL)
    def test_steps_list_the_providers_with_commas_and_or(self):
        self.assertIn('Google, Facebook ou X', self.client.get(reverse('core:home')).content.decode())

    @override_settings(SSO_PROVIDERS=GOOGLE)
    def test_connected_accounts_link_for_students_and_the_trainer(self):
        self.login(self.alice)
        self.assertContains(self.client.get(reverse('core:my_profile')), 'data-connections-link')
        self.assertContains(self.client.get(reverse('core:my_workouts')), 'data-connections-link')
        self.login(self.trainer)
        self.assertContains(self.client.get(reverse('core:student_list')), 'Contas conectadas')

    def test_no_connected_accounts_link_without_providers(self):
        self.login(self.alice)
        self.assertNotContains(self.client.get(reverse('core:my_profile')), 'data-connections-link')

    def test_connected_accounts_page_uses_the_app_layout(self):
        self.login(self.alice)
        response = self.client.get(reverse('socialaccount_connections'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-theme-menu')


class SocialAdapterTests(BaseTestCase):
    """The app is invite-only: single sign-on never creates an account."""

    def setUp(self):
        self.adapter = SocialAccountAdapter()

    def request(self, user=None):
        request = RequestFactory().get('/')
        request.user = user or AnonymousUser()
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def sociallogin(self, email, verified=True, uid='g-1', provider='google'):
        addresses = [EmailAddress(email=email, verified=verified, primary=True)] if email else []
        return SocialLogin(user=User(), account=SocialAccount(provider=provider, uid=uid), email_addresses=addresses)

    def test_it_never_opens_signup(self):
        self.assertFalse(self.adapter.is_open_for_signup(self.request(), self.sociallogin('new@example.com')))

    def test_a_verified_email_of_an_existing_user_is_connected(self):
        login = self.sociallogin('ALICE@example.com')                                   # case does not matter
        request = self.request()
        with mock.patch.object(SocialLogin, 'connect') as connect:                      # needs an installed provider app
            self.adapter.pre_social_login(request, login)
        connect.assert_called_once_with(request, self.alice)

    def test_an_unverified_email_is_never_trusted(self):
        with self.assertRaises(ImmediateHttpResponse) as caught:
            self.adapter.pre_social_login(self.request(), self.sociallogin('alice@example.com', verified=False, provider='facebook'))
        self.assertEqual(caught.exception.response.url, reverse('account_login'))
        self.assertFalse(SocialAccount.objects.exists())

    def test_no_email_at_all_is_refused(self):
        with self.assertRaises(ImmediateHttpResponse):
            self.adapter.pre_social_login(self.request(), self.sociallogin(None, provider='twitter_oauth2'))
        self.assertFalse(SocialAccount.objects.exists())

    def test_a_stranger_is_refused_and_no_user_is_created(self):
        users = User.objects.count()
        request = self.request()
        with self.assertRaises(ImmediateHttpResponse):
            self.adapter.pre_social_login(request, self.sociallogin('stranger@example.com'))
        self.assertEqual(User.objects.count(), users)
        self.assertIn('Peça um convite', ' '.join(str(m) for m in request._messages))

    def test_a_deactivated_user_is_not_matched(self):
        User.objects.filter(pk=self.alice.pk).update(is_active=False)
        with self.assertRaises(ImmediateHttpResponse):
            self.adapter.pre_social_login(self.request(), self.sociallogin('alice@example.com'))

    def test_someone_signed_in_can_connect_any_provider(self):
        login = self.sociallogin(None, provider='twitter_oauth2')
        self.adapter.pre_social_login(self.request(self.alice), login)                  # allauth does the connecting
        self.assertFalse(SocialAccount.objects.exists())

    def test_an_already_connected_account_just_signs_in(self):
        SocialAccount.objects.create(user=self.alice, provider='google', uid='g-9')
        login = SocialLogin(user=self.alice, account=SocialAccount.objects.get(uid='g-9'))
        self.adapter.pre_social_login(self.request(), login)                            # no error, nothing new
        self.assertEqual(SocialAccount.objects.count(), 1)

    def test_the_trainer_can_use_it_too(self):
        request = self.request()
        with mock.patch.object(SocialLogin, 'connect') as connect:
            self.adapter.pre_social_login(request, self.sociallogin('trainer@example.com', uid='t-1'))
        connect.assert_called_once_with(request, self.trainer)


class ConfiguredFromTheEnvironmentTests(BaseTestCase):
    """Providers exist only when both keys are in the environment: checked in a fresh process (settings load once)."""

    SCRIPT = (
        "from django.conf import settings; from core.context_processors import branding;"
        "print('|'.join(p['id'] for p in settings.SSO_PROVIDERS), len(branding(None)['sso_providers']), "
        "'google' in settings.SOCIALACCOUNT_PROVIDERS, settings.STUDIO_NAME)"
    )

    def run_with(self, **env):
        base = {k: v for k, v in os.environ.items() if not k.endswith(('_CLIENT_ID', '_CLIENT_SECRET')) and k != 'STUDIO_NAME'}
        base.update(SECRET_KEY='test', DEBUG='1', **env)
        done = subprocess.run([sys.executable, 'manage.py', 'shell', '-c', self.SCRIPT], cwd=settings.BASE_DIR, env=base,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        return done.stdout.strip().splitlines()[-1]

    def test_nothing_configured(self):
        self.assertEqual(self.run_with(), ' 0 False')

    def test_a_provider_needs_both_keys(self):
        self.assertEqual(self.run_with(GOOGLE_CLIENT_ID='id'), ' 0 False')
        self.assertEqual(self.run_with(FACEBOOK_CLIENT_SECRET='secret'), ' 0 False')

    def test_google_alone(self):
        self.assertEqual(self.run_with(GOOGLE_CLIENT_ID='id', GOOGLE_CLIENT_SECRET='secret'), 'google 1 True')

    def test_all_three_and_the_studio_name(self):
        out = self.run_with(GOOGLE_CLIENT_ID='a', GOOGLE_CLIENT_SECRET='b', FACEBOOK_CLIENT_ID='c', FACEBOOK_CLIENT_SECRET='d',
                            X_CLIENT_ID='e', X_CLIENT_SECRET='f', STUDIO_NAME='Studio da Ana')
        self.assertEqual(out, 'google|facebook|twitter_oauth2 3 True Studio da Ana')
