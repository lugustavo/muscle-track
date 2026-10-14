import importlib
import os
from unittest import mock

from django.test import SimpleTestCase


def load_settings(**env):
    """Re-evaluate config.settings under a given environment."""
    import config.settings as settings_module
    with mock.patch.dict(os.environ, env, clear=False):
        return importlib.reload(settings_module)


class DomainSettingsTests(SimpleTestCase):
    def tearDown(self):
        load_settings()  # restore the module for later tests

    def test_domain_drives_hosts_origins_and_site_url(self):
        s = load_settings(DOMAIN='treino.example.com', SECRET_KEY='x', DEBUG='False')
        self.assertIn('treino.example.com', s.ALLOWED_HOSTS)
        self.assertIn('127.0.0.1', s.ALLOWED_HOSTS)  # container healthcheck
        self.assertEqual(s.CSRF_TRUSTED_ORIGINS, ['https://treino.example.com'])
        self.assertEqual(s.SITE_URL, 'https://treino.example.com')
        self.assertTrue(s.USE_HTTPS)

    def test_explicit_values_win(self):
        s = load_settings(DOMAIN='a.example.com', ALLOWED_HOSTS='b.example.com', SITE_URL='https://c.example.com')
        self.assertEqual(s.ALLOWED_HOSTS, ['b.example.com'])
        self.assertEqual(s.SITE_URL, 'https://c.example.com')

    def test_without_domain_nothing_https_related_is_forced(self):
        env = {k: v for k, v in os.environ.items() if k not in ('DOMAIN', 'USE_HTTPS', 'SITE_URL', 'ALLOWED_HOSTS')}
        with mock.patch.dict(os.environ, env, clear=True):
            import config.settings as settings_module
            s = importlib.reload(settings_module)
        self.assertFalse(s.USE_HTTPS)
        self.assertEqual(s.SITE_URL, '')
