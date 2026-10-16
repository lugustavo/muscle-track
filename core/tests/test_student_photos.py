from datetime import date, timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.consent import record_consent
from core.forms import MyPhotoForm
from core.models import ConsentRecord, ProgressPhoto

from .base import BaseTestCase
from .test_assessment_photos import MEDIA, picture


@override_settings(MEDIA_ROOT=MEDIA)
class StudentPhotoTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.url = reverse('core:my_photo_upload')
        self.login(self.alice)

    def send(self, **extra):
        data = {'angle': 'front', 'image': picture(), **extra}
        return self.client.post(self.url, data)

    def test_photo_is_saved_loose_and_marked_as_sent_by_the_student(self):
        response = self.send(caption='Semana 3')
        self.assertRedirects(response, reverse('core:my_evolution'))
        photo = ProgressPhoto.objects.get()
        self.assertEqual((photo.person, photo.assessment, photo.angle, photo.date, photo.caption, photo.uploaded_by_student),
                         (self.person, None, 'front', timezone.localdate(), 'Semana 3', True))

    def test_day_can_be_a_past_one_but_not_the_future(self):
        self.send(date='2026-01-05')
        self.assertEqual(ProgressPhoto.objects.get().date, date(2026, 1, 5))
        response = self.send(date=(timezone.localdate() + timedelta(days=2)).isoformat())
        self.assertContains(response, 'não pode ser no futuro')
        self.assertEqual(ProgressPhoto.objects.count(), 1)

    def test_without_photo_consent_nothing_is_saved_and_the_page_offers_the_consent(self):
        record_consent(self.person, ConsentRecord.Kind.PHOTOS, False)
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-photos-consent-needed', html)
        self.send()
        self.assertFalse(ProgressPhoto.objects.exists())

    def test_consent_button_comes_back_to_the_upload_page(self):
        record_consent(self.person, ConsentRecord.Kind.PHOTOS, False)
        response = self.client.post(reverse('core:my_consent_photos') + '?next=' + self.url, {'granted': '1'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertNotIn('data-photos-consent-needed', self.client.get(self.url).content.decode())

    def test_consent_redirect_ignores_an_outside_next(self):
        response = self.client.post(reverse('core:my_consent_photos') + '?next=https://evil.example/', {'granted': '1'})
        self.assertRedirects(response, reverse('core:my_privacy'), fetch_redirect_response=False)

    def test_a_non_image_is_refused(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.send(image=SimpleUploadedFile('x.jpg', b'not an image', content_type='image/jpeg'))
        self.assertFalse(ProgressPhoto.objects.exists())

    def test_limit_of_photos_per_student(self):
        for _ in range(MyPhotoForm.MAX_PHOTOS):
            ProgressPhoto.objects.create(person=self.person, image=picture(size=(40, 40)), uploaded_by_student=True)
        response = self.send()
        self.assertContains(response, 'remova algumas')
        self.assertEqual(ProgressPhoto.objects.count(), MyPhotoForm.MAX_PHOTOS)

    def test_trainer_cannot_use_the_student_page(self):
        self.login(self.trainer)
        self.assertRedirects(self.client.get(self.url), reverse('core:home'), fetch_redirect_response=False)

    def test_visitor_is_sent_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)


@override_settings(MEDIA_ROOT=MEDIA)
class StudentPhotoShownTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.mine = ProgressPhoto.objects.create(person=self.person, image=picture(size=(60, 60)), uploaded_by_student=True)
        self.theirs = ProgressPhoto.objects.create(person=self.person, image=picture(size=(60, 60)))

    def test_student_sees_both_but_only_deletes_her_own(self):
        self.login(self.alice)
        html = self.client.get(reverse('core:my_evolution')).content.decode()
        self.assertIn('data-add-photo', html)
        self.assertIn(f'data-loose-photo="{self.mine.pk}"', html)
        self.assertIn(reverse('core:my_photo_delete', kwargs={'pk': self.mine.pk}), html)
        self.assertNotIn(reverse('core:my_photo_delete', kwargs={'pk': self.theirs.pk}), html)

    def test_student_deletes_her_own_photo(self):
        self.login(self.alice)
        response = self.client.post(reverse('core:my_photo_delete', kwargs={'pk': self.mine.pk}))
        self.assertRedirects(response, reverse('core:my_evolution'))
        self.assertFalse(ProgressPhoto.objects.filter(pk=self.mine.pk).exists())

    def test_student_cannot_delete_the_trainers_photo_or_someone_elses(self):
        self.login(self.alice)
        self.assertEqual(self.client.post(reverse('core:my_photo_delete', kwargs={'pk': self.theirs.pk})).status_code, 404)
        other = ProgressPhoto.objects.create(person=self.bob.person, image=picture(size=(60, 60)), uploaded_by_student=True)
        self.assertEqual(self.client.post(reverse('core:my_photo_delete', kwargs={'pk': other.pk})).status_code, 404)
        self.assertEqual(ProgressPhoto.objects.count(), 3)

    def test_delete_needs_post(self):
        self.login(self.alice)
        self.assertEqual(self.client.get(reverse('core:my_photo_delete', kwargs={'pk': self.mine.pk})).status_code, 405)

    def test_trainer_sees_the_student_badge_in_the_file(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).content.decode()
        self.assertEqual(html.count('data-by-student'), 1)

    def test_trainer_can_still_delete_it(self):
        self.login(self.trainer)
        self.client.post(reverse('core:photo_delete', kwargs={'pk': self.mine.pk}))
        self.assertFalse(ProgressPhoto.objects.filter(pk=self.mine.pk).exists())

    def test_the_photo_file_stays_private(self):
        self.login(self.bob)
        self.assertEqual(self.client.get(reverse('core:photo_file', kwargs={'pk': self.mine.pk})).status_code, 403)
