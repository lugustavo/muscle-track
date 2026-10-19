from datetime import timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import inbox, notifications
from core.models import Notice, NotificationPreference

from .base import BaseTestCase


def message(kind='class_cancelled', title='Aula cancelada', body='Sua aula foi cancelada.', path='/my-agenda/'):
    return notifications.Message(kind, title, body, path)


class KeepingNoticesTests(BaseTestCase):
    def test_a_notice_is_kept_even_with_no_channel_on(self):
        notifications.notify([self.alice], message())
        [notice] = self.alice.notices.all()
        self.assertEqual((notice.kind, notice.title, notice.body, notice.path),
                         ('class_cancelled', 'Aula cancelada', 'Sua aula foi cancelada.', '/my-agenda/'))
        self.assertIsNone(notice.read_at)
        self.assertEqual(self.bob.notices.count(), 0)

    def test_the_per_type_switch_is_respected(self):
        NotificationPreference.objects.create(user=self.alice, notify_changes=False)
        notifications.notify([self.alice], message())
        self.assertEqual(self.alice.notices.count(), 0)
        notifications.notify([self.alice], message(kind='class_reminder', title='Lembrete de aula'))
        self.assertEqual(self.alice.notices.count(), 1)            # another kind, still wanted

    def test_kinds_without_a_switch_always_arrive(self):
        NotificationPreference.objects.create(user=self.alice, notify_changes=False, notify_class_reminder=False)
        notifications.notify([self.alice], message(kind='test', title='Teste'))
        self.assertEqual(self.alice.notices.count(), 1)

    def test_inactive_users_and_none_are_skipped(self):
        self.bob.is_active = False
        self.bob.save()
        notifications.notify([self.bob, None, self.alice], message())
        self.assertEqual(Notice.objects.count(), 1)

    def test_it_is_written_in_the_language_of_the_person(self):
        NotificationPreference.objects.create(user=self.alice, language='en')
        title = notifications.class_cancelled(self._appointment()).title
        notifications.notify([self.alice], notifications.Message('class_cancelled', title, title, '/my-agenda/'))
        self.assertEqual(self.alice.notices.get().title, 'Class canceled')

    def test_a_failure_to_keep_a_notice_does_not_break_the_caller(self):
        from unittest import mock
        with mock.patch('core.inbox.add', side_effect=RuntimeError('boom')):
            notifications.notify([self.alice], message())          # must not raise

    def _appointment(self):
        from core.models import Appointment
        return Appointment.objects.create(person=self.alice.person, starts_at=timezone.now() + timedelta(days=2))

    def test_real_events_leave_a_notice(self):
        appointment = self._appointment()
        notifications.notify([self.alice], notifications.class_cancelled(appointment))
        self.assertEqual(self.alice.notices.get().kind, 'class_cancelled')
        self.assertIn('Sua aula de', self.alice.notices.get().body)


class PruneTests(BaseTestCase):
    def test_old_notices_and_the_excess_are_dropped(self):
        old = Notice.objects.create(user=self.alice, kind='x', title='old')
        Notice.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=inbox.KEEP_DAYS + 1))
        for n in range(inbox.KEEP_LAST + 5):
            Notice.objects.create(user=self.alice, kind='x', title=f'n{n}')
        Notice.objects.create(user=self.bob, kind='x', title='bob')
        inbox.prune()
        self.assertEqual(self.alice.notices.count(), inbox.KEEP_LAST)
        self.assertFalse(self.alice.notices.filter(title='old').exists())
        self.assertTrue(self.alice.notices.filter(title=f'n{inbox.KEEP_LAST + 4}').exists())   # the newest stay
        self.assertEqual(self.bob.notices.count(), 1)


class NoticeCentreTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.first = Notice.objects.create(user=self.alice, kind='class_cancelled', title='Aula cancelada',
                                           body='Sua aula foi cancelada.', path='/my-agenda/')
        self.read = Notice.objects.create(user=self.alice, kind='class_reminder', title='Lembrete de aula', body='Amanhã',
                                          path='/my-agenda/', read_at=timezone.now())
        self.others = Notice.objects.create(user=self.bob, kind='class_moved', title='Aula do Bob', path='/my-agenda/')
        self.login(self.alice)

    def test_visitors_are_sent_to_the_login(self):
        self.client.logout()
        for url in (reverse('core:notices'), reverse('core:notice_open', args=[self.first.pk])):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(reverse('core:notices_read_all')).status_code, 302)

    def test_the_page_lists_only_my_notices_with_the_unread_marked(self):
        html = self.client.get(reverse('core:notices')).content.decode()
        self.assertIn('Aula cancelada', html)
        self.assertIn('Lembrete de aula', html)
        self.assertNotIn('Aula do Bob', html)
        self.assertEqual(html.count('data-unread'), 1)

    def test_an_empty_page_says_so(self):
        self.client.logout()
        self.login(self.trainer)
        self.assertContains(self.client.get(reverse('core:notices')), 'data-notices-empty')

    def test_the_text_is_escaped(self):
        Notice.objects.create(user=self.alice, kind='request_new', title='<script>alert(1)</script>', body='<img src=x onerror=1>')
        html = self.client.get(reverse('core:notices')).content.decode()
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;script&gt;', html)

    def test_opening_marks_it_read_and_goes_there(self):
        response = self.client.get(reverse('core:notice_open', args=[self.first.pk]))
        self.assertRedirects(response, '/my-agenda/', fetch_redirect_response=False)
        self.first.refresh_from_db()
        self.assertIsNotNone(self.first.read_at)

    def test_it_never_leads_outside_the_app(self):
        for path in ('https://evil.example/', '//evil.example/', 'javascript:alert(1)', ''):
            Notice.objects.filter(pk=self.first.pk).update(path=path, read_at=None)
            response = self.client.get(reverse('core:notice_open', args=[self.first.pk]))
            self.assertRedirects(response, reverse('core:notices'), fetch_redirect_response=False, msg_prefix=path)

    def test_someone_elses_notice_cannot_be_opened_or_marked(self):
        self.assertEqual(self.client.get(reverse('core:notice_open', args=[self.others.pk])).status_code, 404)
        self.others.refresh_from_db()
        self.assertIsNone(self.others.read_at)

    def test_mark_all_read_is_post_only_and_only_mine(self):
        self.assertEqual(self.client.get(reverse('core:notices_read_all')).status_code, 405)
        self.client.post(reverse('core:notices_read_all'))
        self.assertEqual(self.alice.notices.filter(read_at__isnull=True).count(), 0)
        self.others.refresh_from_db()
        self.assertIsNone(self.others.read_at)

    def test_the_bell_shows_the_unread_count_to_students_and_the_trainer(self):
        html = self.client.get(reverse('core:my_workouts')).content.decode()
        self.assertIn('data-notice-bell', html)
        self.assertIn('notice-count', html)
        self.client.post(reverse('core:notices_read_all'))
        self.assertNotIn('notice-count', self.client.get(reverse('core:my_workouts')).content.decode())
        self.client.logout()
        Notice.objects.create(user=self.trainer, kind='request_new', title='Pedido')
        self.login(self.trainer)
        self.assertIn('notice-count', self.client.get(reverse('core:home')).content.decode())

    def test_visitors_get_no_bell(self):
        self.client.logout()
        self.assertNotIn('data-notice-bell', self.client.get(reverse('account_login')).content.decode())

    def test_the_bell_costs_one_query_whatever_the_number_of_notices(self):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('core:my_workouts'))
            return len([q for q in ctx.captured_queries if 'core_notice' in q['sql']])
        before = queries()
        for n in range(30):
            Notice.objects.create(user=self.alice, kind='x', title=f'n{n}')
        self.assertEqual(queries(), before)

    def test_deleting_the_user_deletes_the_notices(self):
        User.objects.filter(pk=self.alice.pk).delete()
        self.assertEqual(Notice.objects.filter(title__in=['Aula cancelada', 'Lembrete de aula']).count(), 0)


class NoticesInTheDataExportTests(BaseTestCase):
    def test_the_students_export_includes_their_notices_and_only_theirs(self):
        from core.exports import build_data
        Notice.objects.create(user=self.alice, kind='x', title='Para a Alice', body='texto')
        Notice.objects.create(user=self.bob, kind='x', title='Para o Bob')
        data = build_data(self.alice.person, include_internal_notes=False)
        self.assertEqual([n['title'] for n in data['notices']], ['Para a Alice'])
