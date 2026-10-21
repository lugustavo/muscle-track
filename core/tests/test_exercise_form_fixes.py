import io
import os
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from core.models import Exercise

from .base import BaseTestCase


def gif(color=(10, 120, 200)):
    frames = [Image.new('RGB', (200, 150), color), Image.new('RGB', (200, 150), (255, 255, 255))]
    out = io.BytesIO()
    frames[0].save(out, 'GIF', save_all=True, append_images=frames[1:], duration=500, loop=0)
    return out.getvalue()


class MuscleGroupOnEditTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)

    def test_the_edit_page_selects_the_exercises_muscle_group(self):
        html = self.client.get(reverse('core:exercise_edit', kwargs={'pk': self.supino.pk})).content.decode()
        self.assertIn(f'<option value="{self.group.pk}" selected>', html)

    def test_saving_without_touching_it_keeps_the_group(self):
        url = reverse('core:exercise_edit', kwargs={'pk': self.supino.pk})
        response = self.client.post(url, {'exercise_name': 'Supino reto', 'muscle_group': self.group.pk, 'default_reps': 10,
                                          'default_sets': 4, 'media_type': 'image', 'is_active': 'on'})
        self.assertEqual(response.status_code, 302)
        self.supino.refresh_from_db()
        self.assertEqual((self.supino.exercise_name, self.supino.muscle_group), ('Supino reto', self.group))

    def test_a_failed_save_keeps_the_choice_on_screen(self):
        url = reverse('core:exercise_edit', kwargs={'pk': self.supino.pk})
        html = self.client.post(url, {'exercise_name': '', 'muscle_group': self.group.pk, 'default_reps': 10,
                                      'default_sets': 4, 'media_type': 'image'}).content.decode()
        self.assertIn(f'<option value="{self.group.pk}" selected>', html)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='mt-media-'))
class RemoveMediaTests(BaseTestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.login(self.trainer)
        self.supino.media_file = SimpleUploadedFile('s.gif', gif(), 'image/gif')
        self.supino.media_url = 'https://example.org/old.png'
        self.supino.media_credit, self.supino.media_license = 'Ana', 'own'
        self.supino.save()
        self.supino.refresh_from_db()
        self.url = reverse('core:exercise_edit', kwargs={'pk': self.supino.pk})

    def data(self, **extra):
        return {'exercise_name': 'Supino', 'muscle_group': self.group.pk, 'default_reps': 12, 'default_sets': 3,
                'media_type': 'image', 'media_url': 'https://example.org/old.png', 'media_credit': 'Ana', 'media_license': 'own',
                'is_active': 'on', **extra}

    def paths(self):
        return self.supino.media_file.path, self.supino.thumbnail.path

    def test_the_form_offers_to_remove_only_when_there_is_media(self):
        self.assertContains(self.client.get(self.url), 'data-remove-media')
        self.assertNotContains(self.client.get(reverse('core:exercise_edit', kwargs={'pk': self.crucifixo.pk})), 'data-remove-media')

    def test_removing_clears_the_file_thumbnail_link_and_credit_and_frees_the_disk(self):
        media, thumb = self.paths()
        self.assertTrue(os.path.exists(media) and os.path.exists(thumb))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(self.url, self.data(remove_media='on')).status_code, 302)
        self.supino.refresh_from_db()
        self.assertFalse(self.supino.media_file)
        self.assertFalse(self.supino.thumbnail)
        self.assertEqual((self.supino.media_url, self.supino.media_credit, self.supino.media_license), ('', '', ''))
        self.assertIsNone(self.supino.media_info)
        self.assertFalse(os.path.exists(media))
        self.assertFalse(os.path.exists(thumb))

    def test_without_the_box_nothing_is_removed(self):
        self.client.post(self.url, self.data())
        self.supino.refresh_from_db()
        self.assertTrue(self.supino.media_file and self.supino.thumbnail)
        self.assertEqual(self.supino.media_url, 'https://example.org/old.png')

    def test_a_file_sent_with_the_box_is_a_replacement(self):
        old_media, _ = self.paths()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self.data(remove_media='on', media_file=SimpleUploadedFile('novo.gif', gif((200, 30, 30)), 'image/gif')))
        self.supino.refresh_from_db()
        self.assertTrue(self.supino.media_file)
        self.assertIn('novo', self.supino.media_file.name)
        self.assertTrue(self.supino.thumbnail)                 # a new automatic thumbnail from the new file
        self.assertEqual(self.supino.media_url, '')            # the old link did go
        self.assertFalse(os.path.exists(old_media))

    def test_replacing_a_file_deletes_the_old_one(self):
        old_media, old_thumb = self.paths()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self.data(media_file=SimpleUploadedFile('outro.gif', gif((5, 5, 5)), 'image/gif')))
        self.assertFalse(os.path.exists(old_media))
        self.assertFalse(os.path.exists(old_thumb))
        self.supino.refresh_from_db()
        self.assertTrue(os.path.exists(self.supino.media_file.path))
        self.assertTrue(os.path.exists(self.supino.thumbnail.path))

    def test_deleting_the_exercise_deletes_its_files(self):
        lonely = Exercise.objects.create(exercise_name='Sozinho', muscle_group=self.group,
                                         media_file=SimpleUploadedFile('l.gif', gif(), 'image/gif'))
        media, thumb = lonely.media_file.path, lonely.thumbnail.path
        self.assertTrue(os.path.exists(media) and os.path.exists(thumb))
        with self.captureOnCommitCallbacks(execute=True):
            lonely.delete()
        self.assertFalse(os.path.exists(media))
        self.assertFalse(os.path.exists(thumb))
