"""A picture uploaded for an exercise loses its metadata (GPS position, camera) and is shrunk; animations and videos are
kept exactly as sent."""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from core.models import Exercise

from .base import BaseTestCase
from .test_exercise_media import gif_bytes


def jpeg_with_exif(size=(3000, 2000), orientation=1):
    exif = Image.Exif()
    exif[0x010F] = 'TestPhone'      # make
    exif[0x0112] = orientation
    gps = exif.get_ifd(0x8825)      # GPS block
    gps[1], gps[2] = 'S', (23.0, 33.0, 0.0)
    out = io.BytesIO()
    Image.new('RGB', size, 'blue').save(out, 'JPEG', exif=exif)
    return out.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='mt-clean-'))
class CleanExerciseImageTests(BaseTestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def make(self, upload):
        return Exercise.objects.create(exercise_name='Agachamento', muscle_group=self.group, media_file=upload)

    def stored(self, exercise):
        exercise.refresh_from_db()
        with exercise.media_file.open('rb') as handle:
            return Image.open(io.BytesIO(handle.read()))

    def test_the_source_really_carries_a_gps_block(self):
        self.assertTrue(Image.open(io.BytesIO(jpeg_with_exif())).getexif().get_ifd(0x8825))

    def test_a_jpeg_loses_its_metadata_and_gets_smaller(self):
        exercise = self.make(SimpleUploadedFile('foto.jpg', jpeg_with_exif(), 'image/jpeg'))
        image = self.stored(exercise)
        self.assertEqual(len(image.getexif()), 0)
        self.assertEqual(image.getexif().get_ifd(0x8825), {})
        self.assertEqual(max(image.size), 1600)
        self.assertNotIn('foto', exercise.media_file.name)           # the name of the phone's file does not stay either

    def test_the_rotation_is_applied_before_the_tag_goes(self):
        exercise = self.make(SimpleUploadedFile('foto.jpg', jpeg_with_exif((1200, 800), orientation=6), 'image/jpeg'))
        self.assertEqual(self.stored(exercise).size, (800, 1200))

    def test_a_small_picture_is_not_enlarged(self):
        exercise = self.make(SimpleUploadedFile('p.jpg', jpeg_with_exif((600, 400)), 'image/jpeg'))
        self.assertEqual(self.stored(exercise).size, (600, 400))

    def test_a_png_stays_a_png_with_its_transparency(self):
        out = io.BytesIO()
        Image.new('RGBA', (2400, 1200), (255, 0, 0, 0)).save(out, 'PNG')
        exercise = self.make(SimpleUploadedFile('desenho.png', out.getvalue(), 'image/png'))
        image = self.stored(exercise)
        self.assertTrue(exercise.media_file.name.endswith('.png'))
        self.assertEqual((image.mode, max(image.size)), ('RGBA', 1600))

    def test_a_gif_is_kept_exactly_as_sent(self):
        data = gif_bytes()
        exercise = self.make(SimpleUploadedFile('mov.gif', data, 'image/gif'))
        with exercise.media_file.open('rb') as handle:
            self.assertEqual(handle.read(), data)

    def test_a_video_is_kept_exactly_as_sent(self):
        data = b'\x00\x00\x00 ftypmp42' + b'x' * 50
        exercise = self.make(SimpleUploadedFile('v.mp4', data, 'video/mp4'))
        with exercise.media_file.open('rb') as handle:
            self.assertEqual(handle.read(), data)

    def test_a_broken_jpeg_is_kept_as_it_came(self):
        exercise = self.make(SimpleUploadedFile('quebrada.jpg', b'not really a jpeg', 'image/jpeg'))
        with exercise.media_file.open('rb') as handle:
            self.assertEqual(handle.read(), b'not really a jpeg')

    def test_saving_again_does_not_re_encode_the_stored_file(self):
        exercise = self.make(SimpleUploadedFile('foto.jpg', jpeg_with_exif(), 'image/jpeg'))
        name = Exercise.objects.get(pk=exercise.pk).media_file.name
        again = Exercise.objects.get(pk=exercise.pk)
        again.exercise_name = 'Outro nome'
        again.save()
        self.assertEqual(Exercise.objects.get(pk=exercise.pk).media_file.name, name)

    def test_the_thumbnail_comes_from_the_cleaned_picture(self):
        exercise = self.make(SimpleUploadedFile('foto.jpg', jpeg_with_exif(), 'image/jpeg'))
        exercise.refresh_from_db()
        self.assertTrue(exercise.thumbnail)
