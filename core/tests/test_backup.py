import json
import os
import shutil
import stat
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import backup_status

from .base import BaseTestCase

SCRIPT = Path(settings.BASE_DIR) / 'backup' / 'backup.sh'

FAKE_PG_DUMP = '''#!/bin/sh
[ -n "${FAKE_PG_FAIL:-}" ] && { echo "connection refused" >&2; exit 1; }
for a in "$@"; do case "$a" in --file=*) f=${a#--file=};; esac; done
echo "dump" > "$f"
'''
FAKE_PG_RESTORE = '''#!/bin/sh
[ -n "${FAKE_PG_BAD_DUMP:-}" ] && { echo "unreadable" >&2; exit 1; }
exit 0
'''


def iso(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%S%z')


class BackupScriptTests(SimpleTestCase):
    """backup/backup.sh with stand-ins for pg_dump / pg_restore (the real ones were exercised by hand against PostgreSQL 16)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        for name, body in (('pg_dump', FAKE_PG_DUMP), ('pg_restore', FAKE_PG_RESTORE)):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        self.bin = bin_dir
        self.media = self.root / 'media'
        (self.media / 'photos').mkdir(parents=True)
        (self.media / 'photos' / 'a.jpg').write_text('photo')
        self.dir = self.root / 'backups'
        self.env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', BACKUP_DIR=str(self.dir), MEDIA_DIR=str(self.media),
                        PGDATABASE='mt')
        for key in ('FAKE_PG_FAIL', 'FAKE_PG_BAD_DUMP', 'BACKUP_RCLONE_REMOTE'):
            self.env.pop(key, None)

    def run_script(self, *args, **extra):
        return subprocess.run(['sh', str(SCRIPT), *args], env=dict(self.env, **extra), capture_output=True, text=True, timeout=60)

    def names(self):
        return sorted(p.name for p in self.dir.iterdir() if p.name[:2] == '20')

    def status(self):
        return json.loads((self.dir / 'status.json').read_text())

    def old_backups(self, days):
        """Pretend backups made on these dates (YYYY-MM-DD) at 03:00."""
        for day in days:
            (self.dir / f'{day}_030000').mkdir(parents=True, exist_ok=True)

    def test_a_backup_has_the_database_the_media_and_checksums(self):
        result = self.run_script('once')
        self.assertEqual(result.returncode, 0, result.stderr)
        [name] = self.names()
        self.assertEqual(sorted(p.name for p in (self.dir / name).iterdir()), ['SHA256SUMS', 'db.dump', 'media.tgz'])
        listing = subprocess.run(['tar', 'tzf', str(self.dir / name / 'media.tgz')], capture_output=True, text=True).stdout
        self.assertIn('photos/a.jpg', listing)
        checked = subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=self.dir / name, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        status = self.status()
        self.assertTrue(status['ok'])
        self.assertEqual(status['name'], name)
        self.assertEqual(status['offsite'], 'off')

    def test_the_backup_files_are_private(self):
        self.run_script('once')
        [name] = self.names()
        for path in (self.dir / name / 'db.dump', self.dir / name / 'media.tgz'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0, path)

    def test_a_failing_dump_leaves_no_half_backup_and_says_so(self):
        self.run_script('once')
        failed = self.run_script('once', FAKE_PG_FAIL='1')
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(len(self.names()), 1)                        # only the good one
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.startswith('.tmp')], [])
        status = self.status()
        self.assertFalse(status['ok'])
        self.assertIn('connection refused', status['error'])
        self.assertTrue(status['last_ok'])                            # the last good one is still remembered

    def test_an_unreadable_dump_is_a_failure_not_a_backup(self):
        result = self.run_script('once', FAKE_PG_BAD_DUMP='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.names(), [])
        self.assertFalse(self.status()['ok'])

    def test_retention_keeps_recent_days_and_the_last_sundays(self):
        from datetime import datetime
        today = datetime.now().date()          # the script runs in the same time zone
        older = [today - timedelta(days=n) for n in range(1, 41)]
        self.old_backups([d.isoformat() for d in older])
        self.run_script('once', BACKUP_KEEP_DAILY='7', BACKUP_KEEP_WEEKLY='4')
        kept = {n.split('_')[0] for n in self.names()}
        days = [today] + older                                  # newest first; today's is the backup just made
        expected = {d.isoformat() for d in days[:7]}            # the last 7 days
        sundays = [d for d in days if d.weekday() == 6]
        expected |= {d.isoformat() for d in sundays[:4]}        # the last 4 Sundays
        self.assertEqual(kept, expected)

    def test_two_backups_on_one_day_keep_only_the_newest(self):
        self.old_backups(['2026-10-17'])
        (self.dir / '2026-10-17_090000').mkdir()
        self.run_script('once')
        kept = [n for n in self.names() if n.startswith('2026-10-17')]
        self.assertEqual(kept, ['2026-10-17_090000'])

    def test_list_and_restore_refuse_what_does_not_exist(self):
        self.assertEqual(self.run_script('list').returncode, 0)
        result = self.run_script('restore', 'latest')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no such backup', result.stderr)

    def test_restore_checks_the_checksums_first(self):
        self.run_script('once')
        [name] = self.names()
        (self.dir / name / 'db.dump').write_text('tampered')
        result = self.run_script('restore', 'latest')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('corrupted', result.stderr)

    def test_restore_puts_the_media_back(self):
        self.run_script('once')
        shutil.rmtree(self.media)
        result = self.run_script('restore', 'latest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.media / 'photos' / 'a.jpg').read_text(), 'photo')

    def test_a_failed_off_server_copy_is_reported_but_the_backup_stays(self):
        (self.bin / 'rclone').write_text('#!/bin/sh\necho "no route" >&2\nexit 1\n')
        (self.bin / 'rclone').chmod(0o755)
        result = self.run_script('once', BACKUP_RCLONE_REMOTE='offsite:bucket')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.names()), 1)
        status = self.status()
        self.assertTrue(status['ok'])
        self.assertEqual(status['offsite'], 'failed')

    def test_an_off_server_copy_gets_the_whole_backup(self):
        log = self.root / 'rclone.log'
        (self.bin / 'rclone').write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 0\n')
        (self.bin / 'rclone').chmod(0o755)
        self.run_script('once', BACKUP_RCLONE_REMOTE='offsite:bucket')
        [name] = self.names()
        self.assertIn(f'copy {self.dir}/{name} offsite:bucket/{name}', log.read_text())
        self.assertEqual(self.status()['offsite'], 'ok')


@override_settings(BACKUP_MAX_AGE_HOURS=36)
class BackupStatusTests(SimpleTestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / 'status.json'

    def write(self, **fields):
        self.path.write_text(json.dumps(fields))
        return override_settings(BACKUP_STATUS_FILE=str(self.path))

    def test_nothing_to_say_when_it_is_not_configured(self):
        with override_settings(BACKUP_STATUS_FILE=''):
            self.assertIsNone(backup_status.problem())

    def test_a_recent_good_backup_is_silent(self):
        with self.write(ok=True, last_ok=iso(timezone.now() - timedelta(hours=5)), offsite='off'):
            self.assertIsNone(backup_status.problem())

    def test_an_old_backup_is_a_problem(self):
        with self.write(ok=True, last_ok=iso(timezone.now() - timedelta(hours=40)), offsite='off'):
            problem = backup_status.problem()
        self.assertEqual(problem['level'], 'bad')
        self.assertIn('36', problem['text'])

    def test_a_failed_run_says_when_the_last_good_one_was(self):
        with self.write(ok=False, last_ok=iso(timezone.now() - timedelta(hours=2)), error='x'):
            problem = backup_status.problem()
        self.assertEqual(problem['level'], 'bad')
        self.assertIn('falhou', problem['text'])
        self.assertIn('último que deu certo', problem['text'])

    def test_a_failure_with_no_good_backup_ever(self):
        with self.write(ok=False, last_ok='', error='x'):
            self.assertIn('nenhum backup bem-sucedido', backup_status.problem()['text'])

    def test_a_failed_copy_off_the_server_is_a_warning(self):
        with self.write(ok=True, last_ok=iso(timezone.now()), offsite='failed'):
            self.assertEqual(backup_status.problem()['level'], 'warn')

    def test_no_file_yet_and_a_broken_file_are_warnings(self):
        with override_settings(BACKUP_STATUS_FILE=str(self.path)):
            self.assertEqual(backup_status.problem()['level'], 'warn')
        self.path.write_text('not json')
        with override_settings(BACKUP_STATUS_FILE=str(self.path)):
            self.assertEqual(backup_status.problem()['level'], 'warn')


class BackupWarningOnHomeTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / 'status.json'
        self.path.write_text(json.dumps({'ok': False, 'last_ok': '', 'error': 'x'}))

    def test_the_trainer_sees_the_warning_and_the_student_does_not(self):
        with override_settings(BACKUP_STATUS_FILE=str(self.path)):
            self.login(self.trainer)
            self.assertContains(self.client.get(reverse('core:home')), 'data-backup-problem')
            self.client.logout()
            self.login(self.alice)
            self.assertNotContains(self.client.get(reverse('core:home')), 'data-backup-problem')

    def test_no_warning_when_backups_are_fine_or_not_configured(self):
        self.login(self.trainer)
        self.assertNotContains(self.client.get(reverse('core:home')), 'data-backup-problem')
        self.path.write_text(json.dumps({'ok': True, 'last_ok': iso(timezone.now()), 'offsite': 'off'}))
        with override_settings(BACKUP_STATUS_FILE=str(self.path)):
            self.assertNotContains(self.client.get(reverse('core:home')), 'data-backup-problem')

    def test_the_compose_file_wires_the_backup_service(self):
        compose = (Path(settings.BASE_DIR) / 'docker-compose.yml').read_text()
        for line in ('  backup:', 'build: ./backup', 'BACKUP_STATUS_FILE: /backups/status.json', ':/backups:ro'):
            self.assertIn(line, compose)
