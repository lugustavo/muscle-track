import io
import shutil
import tempfile
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from core.models import Assessment, ProgressPhoto

from .base import BaseTestCase

MEDIA = tempfile.mkdtemp(prefix='mt-test-media-')


def picture(name='shot.jpg', size=(900, 1200), exif=False):
    buffer = io.BytesIO()
    image = Image.new('RGB', size, 'blue')
    if exif:
        data = Image.Exif()
        data[0x010F] = 'TestPhone'      # make
        data[0x0112] = 6                # rotated 90 degrees
        image.save(buffer, 'JPEG', exif=data)
    else:
        image.save(buffer, 'JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


@override_settings(MEDIA_ROOT=MEDIA)
class AssessmentPhotoTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(self.trainer)
        self.person = self.alice.person
        self.assessment = Assessment.objects.create(person=self.person, date=date(2026, 3, 1), weight_kg=60)
        self.url = reverse('core:photo_upload', kwargs={'pk': self.person.pk})

    def post(self, **extra):
        data = {'assessment': self.assessment.pk, 'angle': 'front', 'image': picture(), **extra}
        return self.client.post(self.url, data)

    def test_photo_belongs_to_the_assessment_and_takes_its_date(self):
        self.post(date='2030-01-01')
        photo = ProgressPhoto.objects.get()
        self.assertEqual((photo.assessment, photo.angle, photo.date), (self.assessment, 'front', date(2026, 3, 1)))

    def test_one_photo_per_position_and_the_new_one_replaces_the_old(self):
        self.post()
        old = ProgressPhoto.objects.get()
        self.post()
        self.assertEqual(ProgressPhoto.objects.count(), 1)
        self.assertNotEqual(ProgressPhoto.objects.get().pk, old.pk)
        self.post(angle='left')
        self.post(angle='other')
        self.post(angle='other')
        self.assertEqual(ProgressPhoto.objects.filter(angle='other').count(), 2)   # extras are not replaced

    def test_loose_photo_uses_its_own_date(self):
        self.client.post(self.url, {'angle': 'other', 'image': picture(), 'date': '2026-02-02'})
        photo = ProgressPhoto.objects.get()
        self.assertEqual((photo.assessment, photo.date), (None, date(2026, 2, 2)))
        self.client.post(self.url, {'angle': 'other', 'image': picture()})
        self.assertEqual(ProgressPhoto.objects.count(), 2)   # no date typed: today

    def test_cannot_attach_to_another_students_assessment(self):
        other = Assessment.objects.create(person=self.bob.person, date=date(2026, 3, 1))
        response = self.post(assessment=other.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProgressPhoto.objects.exists())

    def test_save_and_next_goes_to_the_next_empty_position(self):
        response = self.post(save_next='1')
        self.assertRedirects(response, f'{self.url}?assessment={self.assessment.pk}&angle=left', fetch_redirect_response=False)
        response = self.post(angle='left', save_next='1')
        self.assertIn('angle=right', response['Location'])
        self.post(angle='right')
        response = self.post(angle='back', save_next='1')   # all four done: back to the file
        self.assertIn('#tab-avaliacoes', response['Location'])

    def test_save_next_keeps_where_the_trainer_came_from(self):
        response = self.client.post(self.url + '?next=/students/', {
            'assessment': self.assessment.pk, 'angle': 'front', 'image': picture(), 'save_next': '1'})
        self.assertIn('next=/students/', response['Location'])

    def test_form_is_preselected_from_the_query(self):
        page = self.client.get(f'{self.url}?assessment={self.assessment.pk}&angle=back')
        html = page.content.decode()
        self.assertIn(f'<option value="{self.assessment.pk}" selected>', html)
        self.assertIn('<option value="back" selected>', html)
        self.assertIn('data-phone-camera', html)   # the camera/gallery controls

    def test_metadata_is_stripped_and_rotation_applied(self):
        self.post(image=picture(exif=True))
        photo = ProgressPhoto.objects.get()
        with Image.open(photo.image.path) as saved:
            self.assertEqual(saved.format, 'JPEG')
            self.assertEqual(len(saved.getexif()), 0)
            self.assertEqual(saved.size, (1200, 900))   # the 90 degree flag was applied, not kept
        self.assertTrue(photo.image.name.endswith('.jpg'))

    def test_large_photos_are_shrunk(self):
        self.post(image=picture(size=(4000, 3000)))
        with Image.open(ProgressPhoto.objects.get().image.path) as saved:
            self.assertLessEqual(max(saved.size), 2000)

    def test_assessment_tab_shows_the_four_positions_and_the_photos(self):
        self.post()
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        html = page.content.decode()
        self.assertEqual(html.count(f'data-photo-strip="{self.assessment.pk}"'), 1)
        for label in ('Frente', 'Lado esquerdo', 'Lado direito', 'Costas'):
            self.assertIn(label, html)
        photo = ProgressPhoto.objects.get()
        self.assertIn(reverse('core:photo_thumb', kwargs={'pk': photo.pk}), html)
        self.assertIn(f'assessment={self.assessment.pk}&amp;angle=left', html)   # an empty slot links to its upload
        self.assertNotIn('id="tab-fotos"', html)

    def test_deleting_the_assessment_keeps_the_photo_as_loose(self):
        self.post()
        self.client.post(reverse('core:assessment_delete', kwargs={'pk': self.assessment.pk}))
        photo = ProgressPhoto.objects.get()
        self.assertIsNone(photo.assessment)
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'data-loose-photos')

    def test_deleting_a_photo_returns_to_the_assessments_tab(self):
        self.post()
        response = self.client.post(reverse('core:photo_delete', kwargs={'pk': ProgressPhoto.objects.get().pk}))
        self.assertIn('#tab-avaliacoes', response['Location'])

    def test_old_photos_are_linked_by_date_when_migrating(self):
        from importlib import import_module
        from django.apps import apps
        migration = import_module('core.migrations.0026_photo_assessment_angle')
        self.post(assessment='', angle='other', date='2026-03-01')
        ProgressPhoto.objects.update(assessment=None)
        migration.link_photos(apps, None)
        self.assertEqual(ProgressPhoto.objects.get().assessment, self.assessment)

    def test_students_can_open_their_photos_but_not_upload(self):
        self.post()
        photo = ProgressPhoto.objects.get()
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('core:photo_thumb', kwargs={'pk': photo.pk})).status_code, 200)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)
