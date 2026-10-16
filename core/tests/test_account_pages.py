from django.core import mail
from django.urls import reverse

from .base import BaseTestCase


class AccountPagesLookLikeTheApp(BaseTestCase):
    """allauth's own pages (password, e-mail, reset...) use the app's layout and Bootstrap forms."""

    def assertAppLayout(self, response):
        text = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('navbar', text)                  # the app's top menu
        self.assertIn('class="card shadow-sm border-0"', text)
        self.assertNotIn('<strong>Menu:</strong>', text)   # the unstyled allauth link list
        self.assertNotIn('Menu:', text)
        self.assertEqual(text.count('<!DOCTYPE html>'), 1)
        return text

    def test_change_password_is_styled_for_students_and_has_a_back_link(self):
        self.login(self.alice)
        text = self.assertAppLayout(self.client.get(reverse('account_change_password')))
        self.assertIn('form-control', text)
        self.assertIn('btn btn-primary', text)
        self.assertIn(f'href="{reverse("core:my_profile")}" class="btn btn-sm btn-outline-secondary btn-back-compact"', text)
        self.assertIn('Voltar ao perfil', text)

    def test_trainer_back_link_goes_to_the_dashboard(self):
        self.login(self.trainer)
        text = self.assertAppLayout(self.client.get(reverse('account_change_password')))
        self.assertIn('Voltar ao painel', text)

    def test_bottom_bar_keeps_profile_lit_on_account_pages(self):
        self.login(self.alice)
        text = self.client.get(reverse('account_change_password')).content.decode()
        bar = text[text.index('<nav class="tabbar'):]
        self.assertEqual(bar.count('aria-current="page"'), 1)
        self.assertIn('>Perfil<', bar[bar.index('aria-current="page"'):][:200])

    def test_changing_the_password_still_works(self):
        self.login(self.alice)
        response = self.client.post(reverse('account_change_password'), {
            'oldpassword': 'pw-alice', 'password1': 'Nova-senha-9XyZ', 'password2': 'Nova-senha-9XyZ'})
        self.assertEqual(response.status_code, 302)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.check_password('Nova-senha-9XyZ'))

    def test_validation_errors_are_shown_in_bootstrap_style(self):
        self.login(self.alice)
        response = self.client.post(reverse('account_change_password'), {
            'oldpassword': 'errada', 'password1': '123', 'password2': '456'})
        text = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('is-invalid', text)
        self.assertIn('invalid-feedback', text)

    def test_email_page(self):
        self.login(self.alice)
        from allauth.account.models import EmailAddress
        EmailAddress.objects.create(user=self.alice, email=self.alice.email, primary=True, verified=True)
        text = self.assertAppLayout(self.client.get(reverse('account_email')))
        self.assertIn('badge bg-success', text)       # "Verificado"
        self.assertIn('form-check-input', text)       # radio for the address
        self.assertIn('btn btn-primary', text)
        self.assertIn('btn btn-outline-danger', text)  # "Remover"

    def test_reset_password_pages_for_visitors(self):
        text = self.assertAppLayout(self.client.get(reverse('account_reset_password')))
        self.assertIn('form-control', text)
        self.assertIn('peça um novo link de acesso à sua personal', text)
        self.assertNotIn('Voltar ao perfil', text)
        response = self.client.post(reverse('account_reset_password'), {'email': self.alice.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        done = self.client.get(reverse('account_reset_password_done'))
        self.assertAppLayout(done)

    def test_signup_closed_and_inactive_pages(self):
        self.assertAppLayout(self.client.get(reverse('account_signup')))
        self.assertAppLayout(self.client.get(reverse('account_inactive')))

    def test_login_and_logout_keep_their_own_templates(self):
        self.assertContains(self.client.get(reverse('account_login')), 'A mesma entrada serve')
        self.login(self.alice)
        self.assertEqual(self.client.get(reverse('account_logout')).status_code, 200)
