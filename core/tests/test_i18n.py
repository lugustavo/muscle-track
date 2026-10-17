from decimal import Decimal

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import translation

from core.billing import money
from core.i18n import clean, language_for, lazy_text, save_language
from core.models import NotificationPreference

from .base import BaseTestCase


class DetectionTests(BaseTestCase):
    """The language follows the browser (Accept-Language), unless a choice was made with the switcher (cookie)."""

    def lang(self, **headers):
        html = self.client.get(reverse('core:home'), **headers).content.decode()
        return html.split('<html lang="', 1)[1].split('"', 1)[0]

    def test_no_header_uses_the_site_default(self):
        self.assertEqual(self.lang(), 'pt-br')

    def test_english_browser(self):
        self.assertEqual(self.lang(HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9'), 'en')
        self.assertEqual(self.lang(HTTP_ACCEPT_LANGUAGE='en-GB'), 'en')

    def test_portuguese_browsers_get_brazilian_portuguese(self):
        for header in ('pt-BR', 'pt-PT', 'pt', 'pt-BR,pt;q=0.9,en;q=0.8'):
            with self.subTest(header=header):
                self.assertEqual(self.lang(HTTP_ACCEPT_LANGUAGE=header), 'pt-br')

    def test_an_unsupported_language_falls_back_to_the_default(self):
        self.assertEqual(self.lang(HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9'), 'pt-br')

    def test_the_second_choice_counts_when_the_first_is_not_available(self):
        self.assertEqual(self.lang(HTTP_ACCEPT_LANGUAGE='fr,en;q=0.5'), 'en')

    def test_weights_decide(self):
        self.assertEqual(self.lang(HTTP_ACCEPT_LANGUAGE='en;q=0.4,pt-BR;q=0.9'), 'pt-br')

    def test_the_cookie_wins_over_the_browser(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'
        self.assertEqual(self.lang(HTTP_ACCEPT_LANGUAGE='pt-BR'), 'en')
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'pt-br'
        self.assertEqual(self.lang(HTTP_ACCEPT_LANGUAGE='en-US'), 'pt-br')

    def test_content_language_header(self):
        self.assertEqual(self.client.get(reverse('account_login'), HTTP_ACCEPT_LANGUAGE='en').headers['Content-Language'], 'en')

    def test_signed_in_pages_follow_the_browser_too(self):
        self.login(self.alice)
        html = self.client.get(reverse('core:my_workouts'), HTTP_ACCEPT_LANGUAGE='en').content.decode()
        self.assertIn('<html lang="en">', html)


class SwitcherTests(BaseTestCase):
    def setUp(self):
        self.url = reverse('set_language')

    def test_the_menu_lists_both_languages_and_marks_the_current_one(self):
        html = self.client.get(reverse('account_login'), HTTP_ACCEPT_LANGUAGE='en').content.decode()
        self.assertEqual(html.count('data-language-option='), 2)
        self.assertIn('Português (Brasil)', html)
        self.assertIn('English', html)
        self.assertIn('data-language-option="en"', html)
        self.assertEqual(html.count('aria-current="true"'), 1)

    def test_choosing_sets_the_cookie_and_goes_back(self):
        response = self.client.post(self.url, {'language': 'en', 'next': '/accounts/login/'})
        self.assertRedirects(response, '/accounts/login/', fetch_redirect_response=False)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, 'en')

    def test_the_choice_sticks_on_the_next_requests(self):
        self.client.post(self.url, {'language': 'en', 'next': '/'})
        html = self.client.get(reverse('account_login'), HTTP_ACCEPT_LANGUAGE='pt-BR').content.decode()
        self.assertIn('<html lang="en">', html)

    def test_an_unsupported_language_is_ignored(self):
        response = self.client.post(self.url, {'language': 'xx', 'next': '/'})
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)

    def test_an_outside_next_is_not_followed(self):
        response = self.client.post(self.url, {'language': 'en', 'next': 'https://evil.example/'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_a_signed_in_choice_is_saved_for_what_is_sent_to_the_person(self):
        self.login(self.alice)
        self.client.post(self.url, {'language': 'en', 'next': '/'})
        self.assertEqual(NotificationPreference.objects.get(user=self.alice).language, 'en')
        self.assertEqual(language_for(self.alice), 'en')

    def test_a_visitors_choice_is_not_saved_anywhere(self):
        self.client.post(self.url, {'language': 'en', 'next': '/'})
        self.assertFalse(NotificationPreference.objects.exclude(language='').exists())


class SavedLanguageTests(BaseTestCase):
    def test_default_when_nothing_is_saved(self):
        self.assertEqual(language_for(self.alice), 'pt-br')

    def test_signing_in_saves_the_language_of_that_browser(self):
        client = Client(HTTP_ACCEPT_LANGUAGE='en-US')
        client.post(reverse('account_login'), {'login': 'alice@example.com', 'password': 'pw-alice'})
        self.assertEqual(language_for(self.alice), 'en')

    def test_the_next_sign_in_refreshes_it(self):
        save_language(self.alice, 'en')
        client = Client(HTTP_ACCEPT_LANGUAGE='pt-BR')
        client.post(reverse('account_login'), {'login': 'alice@example.com', 'password': 'pw-alice'})
        self.assertEqual(language_for(self.alice), 'pt-br')

    def test_clean_maps_variants_and_rejects_the_rest(self):
        self.assertEqual((clean('en-GB'), clean('pt'), clean('pt-PT'), clean('fr'), clean(''), clean(None)),
                         ('en', 'pt-br', 'pt-br', '', '', ''))

    def test_saving_an_unsupported_language_changes_nothing(self):
        save_language(self.alice, 'xx')
        self.assertFalse(NotificationPreference.objects.filter(user=self.alice).exists())

    def test_anonymous_has_no_saved_language(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(language_for(AnonymousUser()), 'pt-br')


class LazyTextTests(SimpleTestCase):
    def test_it_is_built_in_the_language_active_when_it_is_shown(self):
        text = lazy_text(lambda: translation.get_language())
        with translation.override('en'):
            self.assertEqual(str(text), 'en')
        with translation.override('pt-br'):
            self.assertEqual(str(text), 'pt-br')


class FormatTests(SimpleTestCase):
    def test_money_follows_the_language(self):
        with translation.override('pt-br'):
            self.assertEqual(money(Decimal('1234.5')), '1.234,50 €')
        with translation.override('en'):
            self.assertEqual(money(Decimal('1234.5')), '€1,234.50')

    @override_settings(CURRENCY_SYMBOL='R$', CURRENCY_POSITION='before')
    def test_a_long_symbol_keeps_a_space_in_english(self):
        with translation.override('pt-br'):
            self.assertEqual(money(Decimal('1234.5')), 'R$ 1.234,50')
        with translation.override('en'):
            self.assertEqual(money(Decimal('1234.5')), 'R$ 1,234.50')

    def test_month_names_follow_the_language(self):
        from datetime import date
        from django.utils.formats import date_format
        with translation.override('pt-br'):
            self.assertEqual(date_format(date(2026, 10, 15), 'F'), 'Outubro')
        with translation.override('en'):
            self.assertEqual(date_format(date(2026, 10, 15), 'F'), 'October')
