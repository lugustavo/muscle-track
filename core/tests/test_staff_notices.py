"""What the trainer is told in the notice centre about things that used to show only on the dashboard: a student asking
for their data to be erased, an invite accepted, a photo sent by a student, and backups that are not happening."""
import json
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import notifications, reminders
from core.models import Notice, NotificationPreference, Person, ProgressPhoto

from .base import BaseTestCase
from .test_assessment_photos import MEDIA, picture


def trainer_notices(trainer, kind):
    return list(Notice.objects.filter(user=trainer, kind=kind))


class ErasureRequestNoticeTests(BaseTestCase):
    def test_the_trainer_is_told_once_and_the_notice_leads_to_the_file(self):
        self.login(self.alice)
        self.client.post(reverse('core:my_erasure_request'))
        self.client.post(reverse('core:my_erasure_request'))    # asking twice does not tell twice
        [notice] = trainer_notices(self.trainer, 'erasure_request')
        self.assertEqual(notice.path, reverse('core:student_detail', kwargs={'pk': self.alice.person.pk}))
        self.assertIn(self.alice.person.person_name, notice.body)
        self.assertEqual(Notice.objects.filter(user=self.alice).count(), 0)       # nothing goes to the student

    def test_it_has_no_switch_so_it_arrives_even_with_everything_off(self):
        NotificationPreference.objects.create(user=self.trainer, notify_changes=False, notify_renewals=False, notify_activity=False)
        self.login(self.alice)
        self.client.post(reverse('core:my_erasure_request'))
        self.assertEqual(len(trainer_notices(self.trainer, 'erasure_request')), 1)

    def test_the_text_has_no_health_data(self):
        message = notifications.erasure_requested(self.alice.person)
        self.assertNotIn('saúde', f'{message.title} {message.body}'.lower())


class InviteAcceptedNoticeTests(BaseTestCase):
    def accept(self):
        from core.invites import build_invite_path
        user = User.objects.create_user('nova@example.com', 'nova@example.com')
        user.set_unusable_password()
        user.save()
        Person.objects.create(user=user, person_name='Nova Aluna')
        self.client.logout()
        self.client.post(build_invite_path(User.objects.get(pk=user.pk)),
                         {'new_password1': 'Sup3r-segura-123', 'new_password2': 'Sup3r-segura-123'})
        return user.person

    def test_the_trainer_hears_when_a_student_creates_the_password(self):
        person = self.accept()
        [notice] = trainer_notices(self.trainer, 'invite_accepted')
        self.assertIn('Nova Aluna', notice.body)
        self.assertEqual(notice.path, reverse('core:student_detail', kwargs={'pk': person.pk}))

    def test_the_switch_silences_it(self):
        NotificationPreference.objects.create(user=self.trainer, notify_activity=False)
        self.accept()
        self.assertEqual(trainer_notices(self.trainer, 'invite_accepted'), [])


@override_settings(MEDIA_ROOT=MEDIA)
class StudentPhotoNoticeTests(BaseTestCase):
    def setUp(self):
        self.login(self.alice)

    def send(self):
        return self.client.post(reverse('core:my_photo_upload'), {'angle': 'front', 'image': picture()})

    def test_one_notice_for_a_burst_of_photos(self):
        self.send()
        self.send()
        self.send()
        self.assertEqual(ProgressPhoto.objects.count(), 3)
        [notice] = trainer_notices(self.trainer, 'student_photo')
        self.assertTrue(notice.path.endswith('#tab-avaliacoes'))

    def test_a_new_one_after_an_hour(self):
        self.send()
        Notice.objects.filter(kind='student_photo').update(created_at=timezone.now() - timedelta(hours=2))
        self.send()
        self.assertEqual(len(trainer_notices(self.trainer, 'student_photo')), 2)

    def test_another_students_photo_is_a_notice_of_its_own(self):
        self.send()
        self.login(self.bob)
        self.send()
        self.assertEqual(len(trainer_notices(self.trainer, 'student_photo')), 2)

    def test_the_switch_silences_it(self):
        NotificationPreference.objects.create(user=self.trainer, notify_activity=False)
        self.send()
        self.assertEqual(trainer_notices(self.trainer, 'student_photo'), [])


class BackupNoticeTests(BaseTestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / 'status.json'

    def write(self, **fields):
        self.path.write_text(json.dumps(fields))
        return override_settings(BACKUP_STATUS_FILE=str(self.path), BACKUP_MAX_AGE_HOURS=36)

    def stamp(self, hours_ago):
        return (timezone.now() - timedelta(hours=hours_ago)).strftime('%Y-%m-%dT%H:%M:%S%z')

    def test_a_problem_tells_the_trainer_and_not_the_students(self):
        with self.write(ok=False, last_ok=self.stamp(2), error='x'):
            self.assertEqual(reminders.send_backup_alert(timezone.now()), 1)
        [notice] = trainer_notices(self.trainer, 'backup_problem')
        self.assertIn('backup falhou', notice.body)
        self.assertEqual(notice.path, reverse('core:home'))
        self.assertEqual(Notice.objects.filter(user=self.alice).count(), 0)

    def test_it_is_repeated_a_day_later_not_every_run(self):
        with self.write(ok=False, last_ok=self.stamp(2), error='x'):
            reminders.send_backup_alert(timezone.now())
            self.assertEqual(reminders.send_backup_alert(timezone.now()), 0)
            Notice.objects.update(created_at=timezone.now() - timedelta(hours=25))
            self.assertEqual(reminders.send_backup_alert(timezone.now()), 1)
        self.assertEqual(len(trainer_notices(self.trainer, 'backup_problem')), 2)

    def test_nothing_when_backups_are_fine_or_not_configured(self):
        with self.write(ok=True, last_ok=self.stamp(3), offsite='off'):
            self.assertEqual(reminders.send_backup_alert(timezone.now()), 0)
        with override_settings(BACKUP_STATUS_FILE=''):
            self.assertEqual(reminders.send_backup_alert(timezone.now()), 0)
        self.assertEqual(Notice.objects.count(), 0)

    def test_the_reminders_command_does_it(self):
        with self.write(ok=False, last_ok=self.stamp(2), error='x'):
            call_command('send_reminders', stdout=open('/dev/null', 'w'))
        self.assertEqual(len(trainer_notices(self.trainer, 'backup_problem')), 1)


class SettingsAndBadgeTests(BaseTestCase):
    def test_the_trainer_has_the_activity_switch_and_it_saves(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:notification_settings')).content.decode()
        self.assertIn('id="notify_activity"', page)
        self.assertIn('sempre avisam', page)
        self.client.post(reverse('core:notification_settings'), {'types': '1', 'notify_changes': 'on'})
        self.assertFalse(NotificationPreference.objects.get(user=self.trainer).notify_activity)
        self.client.post(reverse('core:notification_settings'), {'types': '1', 'notify_activity': 'on'})
        self.assertTrue(NotificationPreference.objects.get(user=self.trainer).notify_activity)

    def test_students_do_not_see_it(self):
        self.login(self.alice)
        self.assertNotIn('id="notify_activity"', self.client.get(reverse('core:notification_settings')).content.decode())

    def test_the_bell_carries_the_count_for_the_app_icon(self):
        notifications.notify([self.alice], notifications.Message('test', 'Teste', 'x', '/'))
        notifications.notify([self.alice], notifications.Message('test', 'Teste 2', 'x', '/'))
        self.login(self.alice)
        page = self.client.get(reverse('core:my_workouts')).content.decode()
        self.assertIn('data-notice-bell data-notice-total="2"', page)

    def test_the_script_sets_and_clears_the_badge(self):
        from django.conf import settings
        script = (settings.BASE_DIR / 'static' / 'js' / 'pwa.js').read_text()
        self.assertIn('navigator.setAppBadge(unread)', script)
        self.assertIn('navigator.clearAppBadge()', script)
        worker = (settings.BASE_DIR / 'templates' / 'pwa' / 'sw.js').read_text()
        self.assertIn('setAppBadge', worker)
