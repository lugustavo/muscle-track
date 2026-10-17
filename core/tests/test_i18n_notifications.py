"""Notifications are written in the recipient's own language, whoever (and whatever language) triggered them."""
import unittest
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone, translation
from django.utils.translation import get_language

from core import notifications
from core.billing import charge_message
from core.models import Appointment, NotificationPreference

from .base import BaseTestCase

CONFIG = dict(NOTIFICATIONS_ASYNC=False, SITE_URL='https://treino.example.com')


def prefer(user, language):
    NotificationPreference.objects.update_or_create(user=user, defaults={'email_enabled': True, 'language': language})


@override_settings(**CONFIG)
class RecipientLanguageTests(BaseTestCase):
    def test_one_message_is_built_in_each_recipients_language(self):
        """The text is only built at delivery, inside the recipient's language (here marked by the active language)."""
        prefer(self.alice, 'en')
        prefer(self.bob, 'pt-br')

        def mark(text):
            return f'[{get_language()}] {text}'

        message = notifications.test_message()   # built once, here, in the language of whoever triggers it
        with mock.patch('core.notifications._', side_effect=mark):
            notifications.notify([self.alice, self.bob], message, sync=True)
        subjects = {m.to[0]: m.subject for m in mail.outbox}
        self.assertEqual(subjects[self.alice.email], '[en] Teste de notificação')
        self.assertEqual(subjects[self.bob.email], '[pt-br] Teste de notificação')
        bodies = {m.to[0]: m.body for m in mail.outbox}
        self.assertTrue(bodies[self.alice.email].startswith('[en] Tudo certo'))
        self.assertIn('[en] Você recebe este aviso', bodies[self.alice.email])
        self.assertIn('[pt-br] Você recebe este aviso', bodies[self.bob.email])

    def test_title_and_body_are_lazy_until_delivery(self):
        appointment = Appointment.objects.create(person=self.alice.person, starts_at=timezone.now() + timedelta(days=2))
        message = notifications.class_cancelled(appointment)
        self.assertNotIsInstance(message.title, str)   # a lazy text, not built yet
        self.assertEqual(str(message.title), 'Aula cancelada')
        self.assertIn('foi cancelada', str(message.body))

    def test_pt_br_output_is_unchanged(self):
        prefer(self.alice, 'pt-br')
        notifications.notify([self.alice], notifications.package_low(1), sync=True)
        self.assertEqual(mail.outbox[0].subject, 'Seu pacote está acabando')
        self.assertTrue(mail.outbox[0].body.startswith('Resta 1 aula no seu pacote. Fale com a sua personal para renovar.'))

    @unittest.skip('aguarda o catálogo en')
    def test_english_catalog_for_an_english_recipient(self):
        prefer(self.alice, 'en')
        notifications.notify([self.alice], notifications.package_low(2), sync=True)
        self.assertEqual(mail.outbox[0].subject, 'Your package is running out')
        self.assertIn('2 classes left', mail.outbox[0].body)

    @unittest.skip('aguarda o catálogo en')
    def test_charge_message_follows_the_students_language(self):
        prefer(self.alice, 'en')
        with translation.override('pt-br'):   # the trainer works in Portuguese; the student reads English
            text = charge_message(self.alice.person, None, None)
        self.assertNotIn('Olá', text)
