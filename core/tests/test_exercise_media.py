import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from PIL import Image

from core import exercise_media
from core.models import Exercise, SheetLink, WorkoutSession

from .base import BaseTestCase


def gif_bytes(frames=2, size=(600, 450)):
    images = [Image.new('RGB', size, (40 * i + 20, 80, 160)) for i in range(frames)]
    out = io.BytesIO()
    images[0].save(out, 'GIF', save_all=True, append_images=images[1:], duration=600, loop=0)
    return out.getvalue()


class KindTests(SimpleTestCase):
    def test_the_kind_comes_from_the_extension(self):
        for name, kind in (('a.gif', 'image'), ('A.JPG', 'image'), ('x.webp', 'image'), ('c.mp4', 'video'), ('c.WEBM', 'video'),
                           ('https://youtu.be/abc', 'link'), ('https://example.org/page', 'link'), ('', 'link')):
            self.assertEqual(exercise_media.kind_of(name), kind, name)

    def test_a_query_string_does_not_hide_the_extension(self):
        self.assertEqual(exercise_media.kind_of('https://cdn.example.org/a.gif?v=3'), 'image')

    def test_only_web_addresses_are_links(self):
        self.assertEqual(exercise_media.web_url('https://example.org/x'), 'https://example.org/x')
        for bad in ('javascript:alert(1)', 'ftp://example.org/x', 'data:text/html,hi', '', None):
            self.assertEqual(exercise_media.web_url(bad), '', bad)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='mt-media-'))
class ExerciseMediaTests(BaseTestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def make(self, **extra):
        extra.setdefault('exercise_name', 'Prancha')
        return Exercise.objects.create(muscle_group=self.group, **extra)

    def test_the_uploaded_file_wins_over_an_old_link(self):
        # the case of an exercise that still has a placeholder link when the trainer later uploads a GIF
        exercise = self.make(media_url='https://via.placeholder.com/400x300.png',
                             media_file=SimpleUploadedFile('prancha.gif', gif_bytes(), 'image/gif'))
        info = exercise.media_info
        self.assertEqual(info['kind'], 'image')
        self.assertTrue(info['url'].endswith('.gif'))
        self.assertNotIn('placeholder', info['url'])

    def test_a_gif_saved_as_image_type_is_still_a_gif_and_a_link_alone_is_used(self):
        self.assertEqual(self.make(media_url='https://example.org/a.gif').media_info,
                         {'kind': 'image', 'url': 'https://example.org/a.gif'})
        self.assertIsNone(self.make(media_url='javascript:alert(1)').media_info)
        self.assertIsNone(self.make().media_info)

    def test_a_thumbnail_is_made_from_the_first_frame(self):
        exercise = self.make(media_file=SimpleUploadedFile('prancha.gif', gif_bytes(), 'image/gif'))
        self.assertTrue(exercise.thumbnail)
        self.assertTrue(exercise.thumbnail.name.rsplit('/', 1)[-1].startswith('auto_'))
        with Image.open(exercise.thumbnail.path) as thumb:
            self.assertEqual(thumb.format, 'JPEG')
            self.assertLessEqual(thumb.width, 400)
            self.assertLessEqual(thumb.height, 300)
        self.assertTrue(exercise.media_file.size > 0)            # the GIF itself was stored whole (not closed too early)

    def test_a_thumbnail_the_trainer_uploaded_is_never_replaced(self):
        mine = SimpleUploadedFile('mine.jpg', self._jpeg(), 'image/jpeg')
        exercise = self.make(thumbnail=mine, media_file=SimpleUploadedFile('p.gif', gif_bytes(), 'image/gif'))
        self.assertIn('mine', exercise.thumbnail.name)
        exercise.media_file = SimpleUploadedFile('q.gif', gif_bytes(3), 'image/gif')
        exercise.save()
        exercise.refresh_from_db()
        self.assertIn('mine', exercise.thumbnail.name)

    def test_an_automatic_thumbnail_follows_a_replaced_file_but_not_a_plain_save(self):
        exercise = self.make(media_file=SimpleUploadedFile('p.gif', gif_bytes(), 'image/gif'))
        first = exercise.thumbnail.name
        exercise = Exercise.objects.get(pk=exercise.pk)
        exercise.exercise_name = 'Prancha lateral'
        exercise.save()
        self.assertEqual(Exercise.objects.get(pk=exercise.pk).thumbnail.name, first)       # nothing changed: kept
        exercise.media_file = SimpleUploadedFile('novo.gif', gif_bytes(2, (300, 300)), 'image/gif')
        exercise.save()
        self.assertNotEqual(Exercise.objects.get(pk=exercise.pk).thumbnail.name, first)    # new file: new thumbnail

    def test_a_video_or_a_broken_image_stores_the_file_and_makes_no_thumbnail(self):
        video = self.make(media_file=SimpleUploadedFile('v.mp4', b'\x00\x00\x00 ftypmp42', 'video/mp4'))
        self.assertFalse(video.thumbnail)
        self.assertEqual(video.media_info['kind'], 'video')
        broken = self.make(exercise_name='Quebrado', media_file=SimpleUploadedFile('b.gif', b'not really a gif', 'image/gif'))
        self.assertFalse(broken.thumbnail)
        self.assertTrue(broken.media_file.size > 0)

    @staticmethod
    def _jpeg():
        out = io.BytesIO()
        Image.new('RGB', (50, 50), 'red').save(out, 'JPEG')
        return out.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='mt-media-'))
class WhereTheMediaShowsTests(BaseTestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.supino.media_url = 'https://via.placeholder.com/400x300.png?text=Supino'
        self.supino.media_file = SimpleUploadedFile('supino.gif', gif_bytes(), 'image/gif')
        self.supino.save()
        self.gif_url = self.supino.media_file.url

    def execute(self):
        self.login(self.alice)
        WorkoutSession.objects.create(person_routine=self.alice_routine)
        return self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk})).content.decode()

    def test_the_students_workout_opens_the_gif_inline_not_the_old_link(self):
        html = self.execute()
        self.assertIn('data-exercise-media', html)
        self.assertIn(f'<img src="{self.gif_url}"', html)
        self.assertNotIn('placeholder.com', html)
        self.assertNotIn('data-exercise-media-link', html)

    def test_a_video_is_played_inline_muted_and_not_preloaded(self):
        self.supino.media_file = SimpleUploadedFile('supino.mp4', b'\x00\x00\x00 ftypmp42', 'video/mp4')
        self.supino.save()
        html = self.execute()
        self.assertIn(f'<video src="{self.supino.media_file.url}"', html)
        self.assertIn('preload="none"', html)

    def test_a_page_link_stays_a_link(self):
        self.supino.media_file = None
        self.supino.media_url = 'https://www.youtube.com/watch?v=abc123'
        self.supino.save()
        html = self.execute()
        self.assertIn('data-exercise-media-link', html)
        self.assertIn('href="https://www.youtube.com/watch?v=abc123"', html)
        self.assertNotIn('<details class="exercise-media"', html)

    def test_a_non_web_link_is_not_offered(self):
        self.supino.media_file = None
        self.supino.media_url = 'javascript:alert(1)'
        self.supino.save()
        html = self.execute()
        self.assertNotIn('javascript:alert(1)', html)

    def test_the_trainers_exercise_page_shows_the_animation(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:exercise_detail', kwargs={'pk': self.supino.pk})).content.decode()
        self.assertIn(f'<img src="{self.gif_url}"', html)
        self.assertNotIn('placeholder.com', html)

    def test_the_exercise_list_uses_the_automatic_thumbnail(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:exercise_list')).content.decode()
        self.assertIn(self.supino.thumbnail.url, html)

    def test_the_public_sheet_links_to_the_uploaded_file(self):
        SheetLink.objects.create(person=self.alice.person, token='tok-media')
        html = self.client.get(reverse('core:public_sheet', kwargs={'token': 'tok-media'})).content.decode()
        self.assertIn(f'href="{self.gif_url}"', html)
        self.assertNotIn('placeholder.com', html)
