from unittest import mock

from django.db import OperationalError
from django.urls import reverse

from .base import BaseTestCase


class HealthzTests(BaseTestCase):
    def test_ok_without_login(self):
        response = self.client.get(reverse('core:healthz'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_reports_503_when_database_is_down(self):
        with mock.patch('django.db.backends.utils.CursorWrapper.execute', side_effect=OperationalError('down')):
            response = self.client.get(reverse('core:healthz'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'error'})
