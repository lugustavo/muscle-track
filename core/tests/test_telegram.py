import json
from io import StringIO
from unittest import mock

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core import notifications, telegram
from core.models import NotificationLog, NotificationPreference, StudentEvent

from .base import BaseTestCase

TOKEN = '123456:SECRET-TOKEN'
CONFIG = dict(NOTIFICATIONS_ASYNC=False, SITE_URL='https://treino.example.com', TELEGRAM_BOT_TOKEN=TOKEN,
              TELEGRAM_BOT_USERNAME='treino_bot', TELEGRAM_WEBHOOK_SECRET='hook-secret')
HEADER = {'HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN': 'hook-secret'}


def reply(status=200, body=None):
    response = mock.Mock()
    response.status_code = status
    response.json.return_value = body if body is not None else {'ok': status == 200, 'result': {}}
    return response


def update(text, chat_id=555, chat_type='private'):
    return {'update_id': 1, 'message': {'text': text, 'chat': {'id': chat_id, 'type': chat_type}}}


def pref_of(user):
    return NotificationPreference.objects.get_or_create(user=user)[0]


@override_settings(**CONFIG)
class LinkingTests(BaseTestCase):
    def post(self, payload, **extra):
        return self.client.post(reverse('core:telegram_webhook'), json.dumps(payload), content_type='application/json',
                                **{**HEADER, **extra})

    def start_linking(self, user):
        pref = pref_of(user)
        return telegram.new_code(pref)

    def test_the_link_opens_the_bot_with_a_one_time_code(self):
        self.client.force_login(self.alice)
        self.client.post(reverse('core:notification_settings'), {'telegram': 'link'})
        pref = pref_of(self.alice)
        self.assertEqual(len(pref.telegram_code), 12)
        page = self.client.get(reverse('core:notification_settings'))
        self.assertContains(page, f'https://t.me/treino_bot?start={pref.telegram_code}')
        self.assertContains(page, 'data-telegram-open')

    def test_start_with_the_code_links_the_chat_and_turns_the_channel_on(self):
        code = self.start_linking(self.alice)
        with mock.patch('core.telegram.requests.post', return_value=reply()) as sent:
            self.assertEqual(self.post(update(f'/start {code}')).status_code, 200)
        pref = pref_of(self.alice)
        self.assertEqual((pref.telegram_enabled, pref.telegram_chat_id, pref.telegram_code), (True, 555, ''))
        self.assertIsNotNone(pref.telegram_at)
        self.assertEqual(sent.call_args.kwargs['json']['chat_id'], 555)   # the bot confirms in the chat
        event = StudentEvent.objects.filter(person=self.alice.person).first()
        self.assertIn('Telegram', event.summary)

    def test_the_code_works_once_and_expires(self):
        code = self.start_linking(self.alice)
        with mock.patch('core.telegram.requests.post', return_value=reply()):
            self.post(update(f'/start {code}', chat_id=1))
            self.post(update(f'/start {code}', chat_id=2))   # used already
        self.assertEqual(pref_of(self.alice).telegram_chat_id, 1)
        other = self.start_linking(self.bob)
        NotificationPreference.objects.filter(user=self.bob).update(telegram_code_expires=timezone.now())
        with mock.patch('core.telegram.requests.post', return_value=reply()):
            self.post(update(f'/start {other}', chat_id=3))
        self.assertFalse(pref_of(self.bob).telegram_enabled)

    def test_unknown_codes_and_groups_change_nothing(self):
        with mock.patch('core.telegram.requests.post', return_value=reply()) as sent:
            self.post(update('/start nope-nope-no'))
            self.post(update('/start whatever', chat_type='group'))
            self.post(update('/start '))
            self.post(update('hello'))
        self.assertFalse(NotificationPreference.objects.filter(telegram_enabled=True).exists())
        self.assertEqual(sent.call_count, 3)   # help or "code expired" in private chats, nothing in the group

    def test_replies_follow_the_language_telegram_reports_then_the_accounts_own(self):
        english = update('/start nope-nope-no')
        english['message']['from'] = {'language_code': 'en'}
        with mock.patch('core.telegram.requests.post', return_value=reply()) as sent:
            self.post(english)
            self.assertIn('no longer valid', sent.call_args.kwargs['json']['text'])
            self.post(update('/start nope-nope-no'))
            self.assertIn('não vale mais', sent.call_args.kwargs['json']['text'])
            NotificationPreference.objects.update_or_create(user=self.alice, defaults={'language': 'en'})
            code = self.start_linking(self.alice)
            self.post(update(f'/start {code}'))   # Telegram says nothing, the account is English
            self.assertIn('Done!', sent.call_args.kwargs['json']['text'])

    def test_one_chat_belongs_to_one_account(self):
        first = self.start_linking(self.alice)
        with mock.patch('core.telegram.requests.post', return_value=reply()):
            self.post(update(f'/start {first}', chat_id=9))
            second = self.start_linking(self.bob)
            self.post(update(f'/start {second}', chat_id=9))
        self.assertFalse(pref_of(self.alice).telegram_enabled)
        self.assertIsNone(pref_of(self.alice).telegram_chat_id)
        self.assertEqual(pref_of(self.bob).telegram_chat_id, 9)

    def test_stop_turns_it_off(self):
        code = self.start_linking(self.alice)
        with mock.patch('core.telegram.requests.post', return_value=reply()):
            self.post(update(f'/start {code}'))
            self.post(update('/stop'))
        pref = pref_of(self.alice)
        self.assertEqual((pref.telegram_enabled, pref.telegram_chat_id), (False, None))

    def test_the_webhook_only_answers_to_telegram(self):
        url = reverse('core:telegram_webhook')
        body = json.dumps(update('/start x'))
        self.assertEqual(self.client.post(url, body, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post(url, body, content_type='application/json',
                                          HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='wrong').status_code, 403)
        self.assertEqual(self.client.get(url, **HEADER).status_code, 405)
        self.assertEqual(self.client.post(url, 'not json', content_type='application/json', **HEADER).status_code, 400)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, body, content_type='application/json', **HEADER).status_code, 200)

    @override_settings(TELEGRAM_WEBHOOK_SECRET='')
    def test_without_a_secret_the_webhook_is_closed(self):
        self.assertEqual(self.client.post(reverse('core:telegram_webhook'), '{}', content_type='application/json',
                                          HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='').status_code, 403)

    def test_a_failing_reply_does_not_break_the_webhook(self):
        code = self.start_linking(self.alice)
        with mock.patch('core.telegram.requests.post', side_effect=requests.ConnectionError('down')):
            self.assertEqual(self.post(update(f'/start {code}')).status_code, 200)
        self.assertTrue(pref_of(self.alice).telegram_enabled)


@override_settings(**CONFIG)
class SendingTests(BaseTestCase):
    def link(self, user, chat_id=555):
        NotificationPreference.objects.update_or_create(user=user, defaults={
            'telegram_enabled': True, 'telegram_chat_id': chat_id, 'telegram_at': timezone.now()})

    def test_a_message_goes_to_the_linked_chat_as_plain_text(self):
        self.link(self.alice)
        with mock.patch('core.telegram.requests.post', return_value=reply()) as sent:
            notifications.notify([self.alice], notifications.test_message(), sync=True)
        call = sent.call_args
        self.assertTrue(call.args[0].endswith(f'/bot{TOKEN}/sendMessage'))
        payload = call.kwargs['json']
        self.assertEqual(payload['chat_id'], 555)
        self.assertIn('Teste de notificação', payload['text'])
        self.assertIn('https://treino.example.com/notifications/', payload['text'])
        self.assertNotIn('parse_mode', payload)
        log = NotificationLog.objects.get(user=self.alice)
        self.assertEqual((log.channel, log.status), ('telegram', 'sent'))

    def test_respects_the_kind_switches_and_the_channel_switch(self):
        self.link(self.alice)
        NotificationPreference.objects.filter(user=self.alice).update(notify_changes=False)
        with mock.patch('core.telegram.requests.post', return_value=reply()) as sent:
            notifications.notify([self.alice], notifications.Message('class_cancelled', 'x', 'y', ''), sync=True)
        sent.assert_not_called()

    def test_a_blocked_bot_turns_the_channel_off(self):
        self.link(self.alice)
        body = {'ok': False, 'description': 'Forbidden: bot was blocked by the user'}
        with mock.patch('core.telegram.requests.post', return_value=reply(403, body)):
            notifications.notify([self.alice], notifications.test_message(), sync=True)
        pref = pref_of(self.alice)
        self.assertEqual((pref.telegram_enabled, pref.telegram_chat_id), (False, None))
        self.assertEqual(NotificationLog.objects.get(user=self.alice).status, 'skipped')

    def test_failures_are_logged_without_the_token(self):
        self.link(self.alice)
        boom = requests.ConnectionError(f'HTTPSConnectionPool: https://api.telegram.org/bot{TOKEN}/sendMessage failed')
        with mock.patch('core.telegram.requests.post', side_effect=boom):
            notifications.notify([self.alice], notifications.test_message(), sync=True)
        log = NotificationLog.objects.get(user=self.alice)
        self.assertEqual(log.status, 'failed')
        self.assertNotIn(TOKEN, log.detail)
        self.assertNotIn('SECRET', log.detail)
        with mock.patch('core.telegram.requests.post', return_value=reply(500, {'ok': False, 'description': f'oops {TOKEN}'})):
            notifications.notify([self.alice], notifications.test_message(), sync=True)
        self.assertNotIn(TOKEN, ''.join(NotificationLog.objects.values_list('detail', flat=True)))

    @override_settings(TELEGRAM_BOT_TOKEN='')
    def test_not_configured_is_skipped_not_broken(self):
        self.link(self.alice)
        notifications.notify([self.alice], notifications.test_message(), sync=True)
        self.assertEqual(NotificationLog.objects.get(user=self.alice).status, 'skipped')

    def test_a_pending_reminder_uses_the_telegram_channel_too(self):
        from core.reminders import _wants
        from core.models import Appointment
        self.link(self.alice)
        found = Appointment.objects.filter(_wants('appointment', 'notify_class_reminder'))
        self.assertEqual(found.count(), 0)   # (no appointment yet; the filter itself must be valid)

    def test_the_test_button_and_the_page(self):
        self.link(self.alice)
        self.client.force_login(self.alice)
        with mock.patch('core.telegram.requests.post', return_value=reply()):
            page = self.client.post(reverse('core:notification_test'), {'channel': 'telegram'}, follow=True)
        self.assertContains(page, 'Teste enviado. Confira o Telegram.')
        self.assertContains(page, 'data-telegram-card')
        self.client.post(reverse('core:notification_settings'), {'telegram': 'unlink'})
        self.assertFalse(pref_of(self.alice).telegram_enabled)
        self.assertIsNone(pref_of(self.alice).telegram_chat_id)
        denied = self.client.post(reverse('core:notification_test'), {'channel': 'telegram'}, follow=True)
        self.assertContains(denied, 'Ative este canal antes de testar.')

    @override_settings(TELEGRAM_BOT_TOKEN='', TELEGRAM_BOT_USERNAME='', TELEGRAM_WEBHOOK_SECRET='')
    def test_the_card_is_hidden_when_the_server_is_not_set_up(self):
        self.client.force_login(self.alice)
        self.assertNotContains(self.client.get(reverse('core:notification_settings')), 'data-telegram-card')
        self.client.post(reverse('core:notification_settings'), {'telegram': 'link'})
        self.assertEqual(pref_of(self.alice).telegram_code, '')


@override_settings(**CONFIG)
class SetWebhookCommandTests(BaseTestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command('telegram_set_webhook', *args, stdout=out)
        return out.getvalue()

    def test_sets_the_webhook_with_the_secret(self):
        with mock.patch('core.telegram.requests.post', return_value=reply()) as sent:
            self.assertIn('https://treino.example.com/telegram/webhook/', self.run_command())
        payload = sent.call_args.kwargs['json']
        self.assertEqual((payload['secret_token'], payload['allowed_updates']), ('hook-secret', ['message']))
        self.assertTrue(sent.call_args.args[0].endswith('/setWebhook'))

    def test_delete(self):
        with mock.patch('core.telegram.requests.post', return_value=reply()) as sent:
            self.run_command('--delete')
        self.assertTrue(sent.call_args.args[0].endswith('/deleteWebhook'))

    @override_settings(SITE_URL='http://localhost')
    def test_needs_https(self):
        with self.assertRaises(CommandError):
            self.run_command()

    @override_settings(TELEGRAM_BOT_TOKEN='')
    def test_needs_the_variables(self):
        with self.assertRaises(CommandError):
            self.run_command()

    def test_a_refusal_never_prints_the_token(self):
        with mock.patch('core.telegram.requests.post', return_value=reply(401, {'ok': False, 'description': f'bad {TOKEN}'})):
            with self.assertRaises(CommandError) as raised:
                self.run_command()
        self.assertNotIn(TOKEN, str(raised.exception))
