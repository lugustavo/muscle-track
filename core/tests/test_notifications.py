import json
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from pywebpush import WebPushException

from core import class_requests, notifications
from core.models import Appointment, NotificationLog, NotificationPreference, PushSubscription, StudentEvent

from .base import BaseTestCase

CONFIG = dict(NOTIFICATIONS_ASYNC=False, VAPID_PUBLIC_KEY='pub', VAPID_PRIVATE_KEY='priv',
              SITE_URL='https://treino.example.com')


def future(days=3, hour=9):
    return timezone.localtime(timezone.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def turn_on(user, email=False, push=False):
    NotificationPreference.objects.update_or_create(user=user, defaults={'email_enabled': email, 'push_enabled': push})


def subscribe(user, endpoint='https://push.example.com/abc'):
    return PushSubscription.objects.create(user=user, endpoint=endpoint, p256dh='k', auth='a')


@override_settings(**CONFIG)
class SettingsPageTests(BaseTestCase):
    def test_visitor_is_sent_to_login(self):
        self.assertEqual(self.client.get(reverse('core:notification_settings')).status_code, 302)

    def test_everything_starts_off_for_student_and_trainer(self):
        for user in (self.alice, self.trainer):
            self.client.force_login(user)
            page = self.client.get(reverse('core:notification_settings'))
            self.assertEqual(page.status_code, 200)
            self.assertFalse(page.context['pref'].email_enabled)
            self.assertFalse(page.context['pref'].push_enabled)
            self.assertNotContains(page, 'name="email_enabled" checked')
            self.assertNotContains(page, 'name="push_enabled" checked')

    def test_student_needs_consent_first(self):
        self.alice.person.consents.all().delete()
        self.client.force_login(self.alice)
        self.assertIn(reverse('core:consent'), self.client.get(reverse('core:notification_settings'))['Location'])

    def test_turning_on_email_records_when_and_history(self):
        self.client.force_login(self.alice)
        self.client.post(reverse('core:notification_settings'), {'email_enabled': 'on'})
        pref = NotificationPreference.objects.get(user=self.alice)
        self.assertTrue(pref.email_enabled)
        self.assertIsNotNone(pref.email_at)
        self.assertTrue(StudentEvent.objects.filter(person=self.alice.person, summary__contains='Ativou avisos por e-mail').exists())
        self.client.post(reverse('core:notification_settings'), {})
        pref.refresh_from_db()
        self.assertEqual((pref.email_enabled, pref.email_at), (False, None))
        self.assertTrue(StudentEvent.objects.filter(person=self.alice.person, summary__contains='Desativou avisos por e-mail').exists())

    def test_email_needs_an_address(self):
        self.alice.email = ''
        self.alice.save()
        self.client.force_login(self.alice)
        self.client.post(reverse('core:notification_settings'), {'email_enabled': 'on'})
        self.assertFalse(NotificationPreference.objects.get(user=self.alice).email_enabled)

    @override_settings(VAPID_PUBLIC_KEY='', VAPID_PRIVATE_KEY='')
    def test_push_off_until_server_is_configured(self):
        self.client.force_login(self.alice)
        self.client.post(reverse('core:notification_settings'), {'push_enabled': 'on'})
        self.assertFalse(NotificationPreference.objects.get(user=self.alice).push_enabled)

    def test_trainer_has_no_history_event(self):
        self.client.force_login(self.trainer)
        self.client.post(reverse('core:notification_settings'), {'email_enabled': 'on'})
        self.assertTrue(NotificationPreference.objects.get(user=self.trainer).email_enabled)
        self.assertFalse(StudentEvent.objects.filter(summary__contains='avisos').exists())


@override_settings(**CONFIG)
class PushSubscriptionTests(BaseTestCase):
    def post(self, body, user=None):
        self.client.force_login(user or self.alice)
        return self.client.post(reverse('core:push_subscribe'), json.dumps(body), content_type='application/json')

    GOOD = {'endpoint': 'https://push.example.com/abc', 'keys': {'p256dh': 'key', 'auth': 'secret'}}

    def test_subscribe_stores_device_and_turns_push_on(self):
        self.assertEqual(self.post(self.GOOD).json(), {'ok': True})
        sub = PushSubscription.objects.get()
        self.assertEqual((sub.user, sub.p256dh, sub.auth), (self.alice, 'key', 'secret'))
        self.assertTrue(NotificationPreference.objects.get(user=self.alice).push_enabled)

    def test_same_device_for_another_account_moves_over(self):
        self.post(self.GOOD)
        self.post(self.GOOD, user=self.bob)
        self.assertEqual(PushSubscription.objects.get().user, self.bob)

    def test_invalid_subscriptions_are_refused(self):
        for body in ({}, {'endpoint': 'http://insecure.example.com', 'keys': {'p256dh': 'a', 'auth': 'b'}},
                     {'endpoint': 'https://x.example.com', 'keys': {}}, {'endpoint': 5, 'keys': {'p256dh': 'a', 'auth': 'b'}}):
            self.assertEqual(self.post(body).status_code, 400, body)
        self.assertFalse(PushSubscription.objects.exists())

    def test_visitor_and_get_are_refused(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse('core:push_subscribe'), '{}', content_type='application/json').status_code, 302)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('core:push_subscribe')).status_code, 405)

    @override_settings(VAPID_PUBLIC_KEY='', VAPID_PRIVATE_KEY='')
    def test_refused_when_not_configured(self):
        self.assertEqual(self.post(self.GOOD).status_code, 400)

    def test_remove_only_my_own_device(self):
        mine, theirs = subscribe(self.alice, 'https://p.example.com/1'), subscribe(self.bob, 'https://p.example.com/2')
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(reverse('core:push_unsubscribe', kwargs={'pk': theirs.pk})).status_code, 404)
        self.client.post(reverse('core:push_unsubscribe', kwargs={'pk': mine.pk}))
        self.assertEqual(list(PushSubscription.objects.values_list('pk', flat=True)), [theirs.pk])

    def test_turning_push_off_forgets_the_devices(self):
        self.post(self.GOOD)
        self.client.post(reverse('core:notification_settings'), {})
        self.assertFalse(PushSubscription.objects.exists())


@override_settings(**CONFIG)
class DeliveryTests(BaseTestCase):
    def message(self):
        return notifications.Message('class_moved', 'Aula remarcada', 'Sua aula foi remarcada.', '/my-agenda/')

    def test_nothing_is_sent_when_everything_is_off(self):
        notifications.notify([self.alice], self.message())
        notifications.notify([self.alice, None], self.message())
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(NotificationLog.objects.exists())

    def test_email_goes_only_to_those_who_enabled_it(self):
        turn_on(self.alice, email=True)
        notifications.notify([self.alice, self.bob], self.message())
        self.assertEqual([m.to for m in mail.outbox], [[self.alice.email]])
        body = mail.outbox[0].body
        self.assertIn('Sua aula foi remarcada.', body)
        self.assertIn('https://treino.example.com/my-agenda/', body)
        self.assertIn('https://treino.example.com/notifications/', body)
        log = NotificationLog.objects.get()
        self.assertEqual((log.user, log.channel, log.status, log.kind), (self.alice, 'email', 'sent', 'class_moved'))

    def test_disabled_account_gets_nothing(self):
        turn_on(self.alice, email=True)
        self.alice.is_active = False
        self.alice.save()
        notifications.notify([self.alice], self.message())
        self.assertEqual(len(mail.outbox), 0)

    def test_email_failure_is_retried_once_and_logged_without_raising(self):
        turn_on(self.alice, email=True)
        with mock.patch('core.notifications.send_mail', side_effect=OSError('smtp down')) as sender:
            notifications.notify([self.alice], self.message())
        self.assertEqual(sender.call_count, 2)
        log = NotificationLog.objects.get()
        self.assertEqual(log.status, 'failed')
        self.assertIn('não foi possível conectar ao servidor de e-mail', log.detail)

    def test_no_address_is_skipped(self):
        turn_on(self.alice, email=True)
        self.alice.email = ''
        self.alice.save()
        notifications.notify([self.alice], self.message())
        self.assertEqual(NotificationLog.objects.get().status, 'skipped')

    def test_push_sends_payload_to_each_device(self):
        turn_on(self.alice, push=True)
        subscribe(self.alice, 'https://p.example.com/1')
        subscribe(self.alice, 'https://p.example.com/2')
        with mock.patch('pywebpush.webpush') as sender:
            notifications.notify([self.alice], self.message())
        self.assertEqual(sender.call_count, 2)
        _, kwargs = sender.call_args
        self.assertEqual(json.loads(kwargs['data']), {'title': 'Aula remarcada', 'body': 'Sua aula foi remarcada.',
                                                      'url': '/my-agenda/', 'tag': 'class_moved'})
        self.assertEqual(kwargs['vapid_private_key'], 'priv')
        self.assertEqual(NotificationLog.objects.filter(channel='push', status='sent').count(), 2)
        self.assertEqual(len(mail.outbox), 0)

    def test_expired_subscription_is_dropped(self):
        turn_on(self.alice, push=True)
        subscribe(self.alice)
        gone = WebPushException('gone', response=mock.Mock(status_code=410))
        with mock.patch('pywebpush.webpush', side_effect=gone):
            notifications.notify([self.alice], self.message())
        self.assertFalse(PushSubscription.objects.exists())
        self.assertEqual(NotificationLog.objects.get().status, 'skipped')

    def test_other_push_errors_keep_the_device_and_are_logged(self):
        turn_on(self.alice, push=True)
        subscribe(self.alice)
        with mock.patch('pywebpush.webpush', side_effect=WebPushException('boom', response=mock.Mock(status_code=500))):
            notifications.notify([self.alice], self.message())
        self.assertTrue(PushSubscription.objects.exists())
        self.assertEqual(NotificationLog.objects.get().status, 'failed')

    def test_push_connection_error_is_friendly_and_never_leaks_the_endpoint(self):
        import requests
        turn_on(self.alice, push=True)
        subscribe(self.alice, 'https://fcm.googleapis.com/fcm/send/SECRET-TOKEN-123')
        boom = requests.ConnectionError("HTTPSConnectionPool(host='fcm.googleapis.com', port=443): url: /fcm/send/SECRET-TOKEN-123")
        with mock.patch('pywebpush.webpush', side_effect=boom):
            notifications.notify([self.alice], self.message())
        log = NotificationLog.objects.get()
        self.assertEqual(log.status, 'failed')
        self.assertIn('fcm.googleapis.com', log.detail)
        self.assertNotIn('SECRET-TOKEN', log.detail)
        self.client.force_login(self.alice)
        mail.outbox.clear()
        self.client.post(reverse('core:notification_test'), {'channel': 'push'}, follow=True)
        page = self.client.get(reverse('core:notification_settings'))
        self.assertNotContains(page, 'SECRET-TOKEN')

    def test_other_push_errors_are_scrubbed(self):
        turn_on(self.alice, push=True)
        subscribe(self.alice, 'https://push.example.com/SECRET')
        with mock.patch('pywebpush.webpush', side_effect=RuntimeError('failed at https://push.example.com/SECRET now')):
            notifications.notify([self.alice], self.message())
        self.assertNotIn('SECRET', NotificationLog.objects.get().detail)

    def test_push_has_a_timeout(self):
        turn_on(self.alice, push=True)
        subscribe(self.alice)
        with mock.patch('pywebpush.webpush') as sender:
            notifications.notify([self.alice], self.message())
        self.assertEqual(sender.call_args.kwargs['timeout'], 10)

    def test_email_login_error_points_at_the_app_password(self):
        import smtplib
        turn_on(self.alice, email=True)
        with mock.patch('core.notifications.send_mail', side_effect=smtplib.SMTPAuthenticationError(535, b'bad')):
            notifications.notify([self.alice], self.message())
        self.assertIn('senha de app', NotificationLog.objects.get().detail)

    def test_device_names(self):
        chrome_win = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
        edge = chrome_win + ' Edg/120.0'
        iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
        self.assertEqual(notifications.device_name(chrome_win), 'Chrome no Windows')
        self.assertEqual(notifications.device_name(edge), 'Edge no Windows')
        self.assertEqual(notifications.device_name(iphone), 'Safari no iPhone')
        self.assertEqual(notifications.device_name(''), 'Navegador')

    def test_push_without_devices_or_server_keys_is_skipped(self):
        turn_on(self.alice, push=True)
        notifications.notify([self.alice], self.message())
        self.assertEqual(NotificationLog.objects.get().detail, 'nenhum aparelho inscrito')
        subscribe(self.alice)
        with override_settings(VAPID_PRIVATE_KEY=''):
            notifications.notify([self.alice], self.message())
        self.assertEqual(NotificationLog.objects.first().detail, 'web push não configurado no servidor')

    @override_settings(NOTIFICATIONS_ASYNC=True)
    def test_async_runs_in_a_thread(self):
        turn_on(self.alice, email=True)
        with mock.patch('core.notifications.threading.Thread') as thread:
            notifications.notify([self.alice], self.message())
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs['daemon'])
        thread.return_value.start.assert_called_once()
        self.assertEqual(len(mail.outbox), 0)   # nothing sent inline

    def test_test_button(self):
        self.client.force_login(self.alice)
        self.client.post(reverse('core:notification_test'), {'channel': 'email'})   # off: refused
        self.assertEqual(len(mail.outbox), 0)
        turn_on(self.alice, email=True)
        self.client.post(reverse('core:notification_test'), {'channel': 'email'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Teste', mail.outbox[0].subject)
        self.client.post(reverse('core:notification_test'), {'channel': 'sms'})
        self.assertEqual(len(mail.outbox), 1)


@override_settings(**CONFIG)
class TriggerTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.person = self.alice.person
        self.appt = Appointment.objects.create(person=self.person, starts_at=future(3))
        turn_on(self.alice, email=True)
        turn_on(self.trainer, email=True)

    def subjects(self, to=None):
        return [m.subject for m in mail.outbox if to is None or to in m.to]

    def test_student_request_notifies_the_trainer(self):
        self.client.force_login(self.alice)
        new = future(5, 10)
        self.client.post(reverse('core:my_agenda_request', kwargs={'pk': self.appt.pk}), {
            'kind': 'reschedule', 'date': new.date().isoformat(), 'time': '10:00'})
        self.assertEqual(self.subjects(self.trainer.email), [f'Novo pedido de {self.person.person_name}'])
        self.assertEqual(self.subjects(self.alice.email), [])
        self.assertIn('remarcar', mail.outbox[0].body)

    def test_answer_notifies_the_student_once(self):
        asked = class_requests.create_request(self.person, self.appt, 'reschedule', future(5, 10))
        self.client.force_login(self.trainer)
        self.client.post(reverse('core:appointment_request_decide', kwargs={'pk': asked.pk}), {'decision': 'approve'})
        self.assertEqual(self.subjects(), ['Pedido aprovado'])   # the move itself must not send a second message
        self.assertEqual(mail.outbox[0].to, [self.alice.email])

    def test_decline_carries_the_reason(self):
        asked = class_requests.create_request(self.person, self.appt, 'cancel')
        self.client.force_login(self.trainer)
        self.client.post(reverse('core:appointment_request_decide', kwargs={'pk': asked.pk}),
                         {'decision': 'decline', 'note': 'Sem vaga'})
        self.assertEqual(self.subjects(), ['Pedido recusado'])
        self.assertIn('Sem vaga', mail.outbox[0].body)

    def test_drag_move_and_undo_notify_the_student(self):
        self.client.force_login(self.trainer)
        new = future(5, 9)
        body = self.client.post(reverse('core:appointment_move', kwargs={'pk': self.appt.pk}), {'date': new.date().isoformat()}).json()
        self.assertEqual(self.subjects(self.alice.email), ['Aula remarcada'])
        self.client.post(body['undo_url'])
        self.assertEqual(self.subjects(self.alice.email), ['Aula remarcada', 'Aula mantida'])

    def test_failed_move_sends_nothing(self):
        self.client.force_login(self.trainer)
        self.client.post(reverse('core:appointment_move', kwargs={'pk': self.appt.pk}), {'date': 'x'})
        self.assertEqual(len(mail.outbox), 0)

    def test_reschedule_form_notifies(self):
        self.client.force_login(self.trainer)
        new = future(5, 10)
        self.client.post(reverse('core:appointment_reschedule', kwargs={'pk': self.appt.pk}), {
            'person': self.person.pk, 'date': new.date().isoformat(), 'time': '10:00', 'duration_minutes': 60,
            'kind': 'regular', 'notes': ''})
        self.assertEqual(self.subjects(self.alice.email), ['Aula remarcada'])

    def test_cancelling_notifies_once_and_only_for_future_classes(self):
        self.client.force_login(self.trainer)
        url = reverse('core:appointment_status', kwargs={'pk': self.appt.pk})
        self.client.post(url, {'status': 'cancelled'})
        self.client.post(url, {'status': 'cancelled'})
        self.assertEqual(self.subjects(), ['Aula cancelada'])
        mail.outbox.clear()
        past = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=2))
        self.client.post(reverse('core:appointment_status', kwargs={'pk': past.pk}), {'status': 'cancelled'})
        self.client.post(reverse('core:appointment_status', kwargs={'pk': self.appt.pk}), {'status': 'attended'})
        self.assertEqual(len(mail.outbox), 0)

    def test_student_with_notifications_off_hears_nothing(self):
        turn_on(self.alice)
        self.client.force_login(self.trainer)
        self.client.post(reverse('core:appointment_status', kwargs={'pk': self.appt.pk}), {'status': 'cancelled'})
        self.assertEqual(len(mail.outbox), 0)


class SetupTests(BaseTestCase):
    def test_vapid_command_prints_a_working_pair(self):
        out = StringIO()
        call_command('generate_vapid_keys', stdout=out)
        lines = dict(line.split('=', 1) for line in out.getvalue().splitlines() if line.startswith('VAPID_'))
        self.assertEqual(len(lines['VAPID_PUBLIC_KEY']), 87)
        from py_vapid import Vapid
        Vapid.from_string(lines['VAPID_PRIVATE_KEY'])

    def test_service_worker_handles_push(self):
        self.client.force_login(self.alice)
        js = self.client.get('/sw.js').content.decode()
        self.assertIn("addEventListener('push'", js)
        self.assertIn("addEventListener('notificationclick'", js)


@override_settings(**CONFIG)
class DeviceLabelTests(BaseTestCase):
    def test_old_rows_with_a_raw_user_agent_show_a_friendly_label(self):
        sub = subscribe(self.alice)
        sub.device = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
        sub.save()
        self.client.force_login(self.alice)
        page = self.client.get(reverse('core:notification_settings'))
        self.assertContains(page, 'Chrome no Windows')
        self.assertNotContains(page, 'Mozilla/5.0')
