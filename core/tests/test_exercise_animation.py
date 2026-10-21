import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from PIL import Image

from core import exercise_animation
from core.models import Exercise

from .base import BaseTestCase


def photo(color, size=(640, 480), fmt='JPEG', orientation=None):
    image = Image.new('RGB', size, color)
    out = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        image.save(out, fmt, exif=exif)
    else:
        image.save(out, fmt)
    return out.getvalue()


def upload(name, data):
    return SimpleUploadedFile(name, data, 'image/jpeg')


class BuilderTests(SimpleTestCase):
    def test_two_photos_make_a_looping_two_frame_animation(self):
        data, extension = exercise_animation.build(io.BytesIO(photo('red')), io.BytesIO(photo('blue')))
        with Image.open(io.BytesIO(data)) as animation:
            self.assertEqual(extension, 'webp')
            self.assertEqual(animation.format, 'WEBP')
            self.assertEqual(animation.n_frames, 2)
            self.assertEqual(animation.info.get('loop'), 0)

    def test_the_poses_are_held_for_the_chosen_speed(self):
        for speed, ms in (('slow', 1200), ('normal', 800), ('fast', 500), ('bogus', 800)):
            _, durations = exercise_animation.frames(io.BytesIO(photo('red')), io.BytesIO(photo('blue')), speed)
            self.assertEqual(durations, [ms, ms], speed)

    def test_smooth_adds_cross_fade_frames_that_start_and_end_on_the_poses(self):
        images, durations = exercise_animation.frames(io.BytesIO(photo((255, 0, 0))), io.BytesIO(photo((0, 0, 255))), smooth=True)
        self.assertEqual(len(images), 2 + 2 * exercise_animation.FADE_STEPS)
        self.assertEqual(durations[0], 800)
        self.assertEqual(durations[exercise_animation.FADE_STEPS + 1], 800)
        self.assertGreater(images[0].getpixel((5, 5))[0], 250)                                  # red (resampling may shave a unit)
        self.assertGreater(images[exercise_animation.FADE_STEPS + 1].getpixel((5, 5))[2], 250)  # blue
        middle = images[1].getpixel((5, 5))
        self.assertTrue(0 < middle[2] < 255)                      # a blend, not either pose

    def test_it_is_shrunk_and_the_second_photo_follows_the_first_ones_shape(self):
        images, _ = exercise_animation.frames(io.BytesIO(photo('red', (2000, 1500))), io.BytesIO(photo('blue', (900, 900))))
        self.assertEqual(images[0].size, (800, 600))
        self.assertEqual(images[1].size, (800, 600))

    def test_the_exif_orientation_is_applied(self):
        # a 100 x 50 picture marked "rotate 90" is really 50 x 100
        images, _ = exercise_animation.frames(io.BytesIO(photo('red', (100, 50), orientation=6)), io.BytesIO(photo('blue', (50, 100))))
        self.assertEqual(images[0].size, (50, 100))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='mt-media-'))
class AnimationFormTests(BaseTestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.login(self.trainer)

    def data(self, **extra):
        return {'exercise_name': 'Prancha', 'muscle_group': self.group.pk, 'default_reps': 1, 'default_sets': 3,
                'media_type': 'image', 'is_active': 'on', **extra}

    def create(self, **extra):
        return self.client.post(reverse('core:exercise_create'), self.data(**extra))

    def test_the_two_photos_become_the_exercises_animation(self):
        response = self.create(anim_start=upload('a.jpg', photo('red')), anim_end=upload('b.jpg', photo('blue')), anim_speed='slow')
        self.assertEqual(response.status_code, 302)
        exercise = Exercise.objects.get(exercise_name='Prancha')
        self.assertTrue(exercise.media_file.name.endswith('.webp'))
        self.assertEqual(exercise.media_type, 'gif')
        self.assertEqual(exercise.media_license, 'own')                 # recorded by the trainer
        self.assertEqual(exercise.media_info['kind'], 'image')
        with Image.open(exercise.media_file.path) as animation:
            self.assertEqual(animation.n_frames, 2)
        self.assertTrue(exercise.thumbnail)                             # from the first frame
        self.assertEqual(Image.open(exercise.thumbnail.path).format, 'JPEG')

    def test_a_licence_already_chosen_is_kept(self):
        self.create(anim_start=upload('a.jpg', photo('red')), anim_end=upload('b.jpg', photo('blue')), media_license='permission')
        self.assertEqual(Exercise.objects.get(exercise_name='Prancha').media_license, 'permission')

    def test_one_photo_alone_is_refused(self):
        for only in ('anim_start', 'anim_end'):
            response = self.create(**{only: upload('a.jpg', photo('red'))})
            self.assertEqual(response.status_code, 200, only)
            self.assertContains(response, 'Envie as duas fotos')
        self.assertFalse(Exercise.objects.filter(exercise_name='Prancha').exists())

    def test_a_media_file_and_an_animation_together_are_refused(self):
        response = self.create(anim_start=upload('a.jpg', photo('red')), anim_end=upload('b.jpg', photo('blue')),
                               media_file=SimpleUploadedFile('x.gif', b'GIF89a', 'image/gif'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escolha só uma')

    def test_something_that_is_not_an_image_is_refused(self):
        response = self.create(anim_start=upload('a.jpg', b'not a picture'), anim_end=upload('b.jpg', photo('blue')))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(exercise_name='Prancha').exists())

    def test_the_animation_replaces_the_current_media_and_the_old_file_goes(self):
        self.create(media_file=SimpleUploadedFile('old.gif', photo('green', fmt='GIF'), 'image/gif'))
        exercise = Exercise.objects.get(exercise_name='Prancha')
        old = exercise.media_file.path
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('core:exercise_edit', kwargs={'pk': exercise.pk}), self.data(
                anim_start=upload('a.jpg', photo('red')), anim_end=upload('b.jpg', photo('blue'))))
        exercise.refresh_from_db()
        self.assertTrue(exercise.media_file.name.endswith('.webp'))
        import os
        self.assertFalse(os.path.exists(old))

    def test_the_form_has_the_guided_camera_and_the_two_fields(self):
        html = self.client.get(reverse('core:exercise_create')).content.decode()
        for marker in ('data-anim-start', 'data-anim-end', 'data-anim-open', 'data-anim-live', 'js/exercise_capture.js',
                       'js/photo_guides.js', 'name="anim_speed"', 'name="anim_smooth"'):
            self.assertIn(marker, html)
