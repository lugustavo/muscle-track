import io
import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from core import evolution
from core.models import Assessment, ProgressPhoto, StudentNote

from .base import BaseTestCase

MEDIA = tempfile.mkdtemp(prefix='mt-test-media-')


def picture():
    buffer = io.BytesIO()
    Image.new('RGB', (600, 800), 'green').save(buffer, 'JPEG')
    return SimpleUploadedFile('p.jpg', buffer.getvalue(), content_type='image/jpeg')


@override_settings(MEDIA_ROOT=MEDIA)
class EvolutionTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.first = Assessment.objects.create(person=self.person, date=date(2026, 1, 10), weight_kg=Decimal('70'),
                                               waist_cm=Decimal('80'), notes='PRIVATE-OBSERVATION')
        self.second = Assessment.objects.create(person=self.person, date=date(2026, 4, 10), weight_kg=Decimal('66.5'),
                                                waist_cm=Decimal('76'))

    def photo(self, assessment, angle='front'):
        return ProgressPhoto.objects.create(person=self.person, assessment=assessment, angle=angle, image=picture())

    def test_comparable_angles_need_two_assessments(self):
        self.photo(self.first)
        self.photo(self.second, 'back')
        self.assertEqual(evolution.comparable_angles(evolution.assessments_with_photos(self.person)), [])
        self.photo(self.second, 'front')
        angles = evolution.comparable_angles(evolution.assessments_with_photos(self.person))
        self.assertEqual([a.value for a in angles], ['front'])

    def test_compare_picks_oldest_and_newest_and_reports_changes(self):
        a, b = self.photo(self.first), self.photo(self.second)
        data = evolution.compare(self.person)
        self.assertEqual((data['before'], data['after']), (self.first, self.second))
        self.assertEqual((data['before_photo'], data['after_photo']), (a, b))
        self.assertEqual(data['days'], 90)
        changes = {c['label']: c['delta'] for c in data['changes']}
        self.assertEqual(changes, {'Peso': Decimal('-3.5'), 'Cintura': Decimal('-4')})

    def test_older_is_always_on_the_left(self):
        self.photo(self.first), self.photo(self.second)
        data = evolution.compare(self.person, 'front', self.second.pk, self.first.pk)
        self.assertEqual((data['before'], data['after']), (self.first, self.second))

    def test_bad_parameters_fall_back(self):
        self.photo(self.first), self.photo(self.second)
        data = evolution.compare(self.person, 'nonsense', 'x', '999999')
        self.assertEqual((data['angle'], data['before'], data['after']), ('front', self.first, self.second))

    def loose(self, day, angle='front', by_student=True, person=None):
        return ProgressPhoto.objects.create(person=person or self.person, angle=angle, date=day, image=picture(),
                                            uploaded_by_student=by_student)

    def test_loose_photos_can_be_compared(self):
        mine = self.loose(date(2026, 2, 1))
        self.assertEqual(evolution.comparable_angles(evolution.assessments_with_photos(self.person)), [])
        self.photo(self.first)
        data = evolution.compare(self.person)
        self.assertEqual((data['before_photo'], data['after_photo']), (self.photo_of(self.first), mine))
        self.assertEqual((data['before'], data['after']), (self.first, None))   # a loose photo has no assessment
        self.assertEqual(data['changes'], [])
        self.assertEqual(data['days'], 22)

    def photo_of(self, assessment):
        return assessment.photos.get()

    def test_two_loose_photos_and_the_choice_by_key(self):
        a, b, c = self.loose(date(2026, 3, 1)), self.loose(date(2026, 3, 20)), self.loose(date(2026, 5, 2), by_student=False)
        data = evolution.compare(self.person, 'front', f'p{b.pk}', f'p{c.pk}')
        self.assertEqual((data['before_photo'], data['after_photo']), (b, c))
        self.assertEqual([e.key for e in data['having']], [f'p{c.pk}', f'p{b.pk}', f'p{a.pk}'])   # newest first
        self.assertEqual([e.source for e in data['having']], ['Foto avulsa', 'Enviada pelo aluno', 'Enviada pelo aluno'])

    def test_measure_changes_need_two_assessments(self):
        self.photo(self.first), self.photo(self.second)
        self.loose(date(2026, 6, 1))
        both = evolution.compare(self.person, 'front', self.first.pk, self.second.pk)
        self.assertTrue(both['changes'])
        mixed = evolution.compare(self.person, 'front', self.second.pk, f'p{ProgressPhoto.objects.get(assessment=None).pk}')
        self.assertEqual(mixed['changes'], [])

    def test_other_position_photos_are_not_comparable(self):
        self.loose(date(2026, 3, 1), 'other'), self.loose(date(2026, 3, 2), 'other')
        self.assertEqual(evolution.comparable_angles([], evolution.comparable_loose(self.person.progress_photos.all())), [])

    def test_another_students_loose_photo_never_appears(self):
        mine = self.loose(date(2026, 3, 1))
        theirs = self.loose(date(2026, 3, 2), person=self.bob.person)
        data = evolution.compare(self.person, 'front', f'p{theirs.pk}', None)
        self.assertEqual(data['angles'], [])   # one photo of her own is not enough, and his does not count
        self.assertEqual([e.photo for e in data['having']], [mine])
        self.assertEqual(data['before_photo'], mine)   # the other student's key fell back to her own photo

    def test_compare_pages_offer_the_loose_photos(self):
        mine, other = self.loose(date(2026, 2, 1)), self.loose(date(2026, 4, 1))
        for who, url in ((self.alice, reverse('core:my_evolution_compare')),
                         (self.trainer, reverse('core:student_compare', kwargs={'pk': self.person.pk}))):
            self.client.force_login(who)
            page = self.client.get(url)
            self.assertContains(page, 'data-compare')
            self.assertContains(page, f'value="p{mine.pk}"')
            self.assertContains(page, '01/02/2026 · Enviada pelo aluno')
            self.assertNotContains(page, 'O que mudou')
        self.client.force_login(self.alice)
        self.assertContains(self.client.get(reverse('core:my_evolution')), reverse('core:my_evolution_compare'))
        self.client.force_login(self.bob)
        self.assertContains(self.client.get(reverse('core:my_evolution_compare')), 'duas fotos da mesma posição')
        self.assertTrue(other.pk)

    def link_url(self, photo):
        return reverse('core:photo_link', kwargs={'pk': photo.pk})

    def test_trainer_links_a_loose_photo_to_an_assessment(self):
        loose = self.loose(date(2026, 3, 1), 'back')
        self.client.force_login(self.trainer)
        page = self.client.get(self.link_url(loose))
        self.assertContains(page, 'data-photo-link')
        self.assertContains(page, 'selected')   # the nearest assessment is pre-selected
        done = self.client.post(self.link_url(loose), {'assessment': self.second.pk, 'angle': 'front'})
        self.assertRedirects(done, reverse('core:student_detail', kwargs={'pk': self.person.pk}) + '#tab-avaliacoes',
                             fetch_redirect_response=False)
        loose.refresh_from_db()
        self.assertEqual((loose.assessment, loose.angle, loose.date), (self.second, 'front', self.second.date))
        self.assertTrue(loose.uploaded_by_student)   # still marked as sent by the student

    def test_linking_replaces_the_photo_of_that_position(self):
        old = self.photo(self.first)
        loose = self.loose(date(2026, 2, 2))
        self.client.force_login(self.trainer)
        self.client.post(self.link_url(loose), {'assessment': self.first.pk, 'angle': 'front'})
        self.assertFalse(ProgressPhoto.objects.filter(pk=old.pk).exists())
        self.assertEqual(self.first.photos.get(), loose)

    def test_extra_position_keeps_the_other_photos(self):
        kept = self.photo(self.first)
        loose = self.loose(date(2026, 2, 2), 'other')
        self.client.force_login(self.trainer)
        self.client.post(self.link_url(loose), {'assessment': self.first.pk, 'angle': 'other'})
        self.assertEqual(self.first.photos.count(), 2)
        self.assertTrue(ProgressPhoto.objects.filter(pk=kept.pk).exists())

    def test_cannot_link_to_another_students_assessment(self):
        theirs = Assessment.objects.create(person=self.bob.person, date=date(2026, 5, 5))
        loose = self.loose(date(2026, 3, 1))
        self.client.force_login(self.trainer)
        page = self.client.post(self.link_url(loose), {'assessment': theirs.pk, 'angle': 'front'})
        self.assertEqual(page.status_code, 200)   # the form is shown again with an error
        loose.refresh_from_db()
        self.assertIsNone(loose.assessment)

    def test_link_needs_an_assessment_and_a_loose_photo(self):
        Assessment.objects.filter(person=self.person).delete()
        loose = self.loose(date(2026, 3, 1))
        self.client.force_login(self.trainer)
        self.assertEqual(self.client.get(self.link_url(loose)).status_code, 302)
        a = Assessment.objects.create(person=self.person, date=date(2026, 3, 2))
        inside = self.photo(a)
        self.assertEqual(self.client.get(self.link_url(inside)).status_code, 302)   # already inside an assessment

    def test_only_the_trainer_can_link(self):
        loose = self.loose(date(2026, 3, 1))
        self.assertEqual(Client().get(self.link_url(loose)).status_code, 302)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(self.link_url(loose)).status_code, 403)
        self.assertEqual(self.client.post(self.link_url(loose), {'assessment': self.first.pk, 'angle': 'front'}).status_code, 403)

    def test_the_loose_photo_card_offers_the_link_only_with_assessments(self):
        self.loose(date(2026, 3, 1))
        self.client.force_login(self.trainer)
        detail = reverse('core:student_detail', kwargs={'pk': self.person.pk})
        self.assertContains(self.client.get(detail), 'data-link-photo')
        Assessment.objects.filter(person=self.person).delete()
        self.assertNotContains(self.client.get(detail), 'data-link-photo')

    def test_trainer_compare_page(self):
        self.photo(self.first), self.photo(self.second)
        self.client.force_login(self.trainer)
        page = self.client.get(reverse('core:student_compare', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'data-compare')
        self.assertContains(page, 'O que mudou em 90 dias')
        self.assertContains(page, '-3,50')
        detail = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(detail, reverse('core:student_compare', kwargs={'pk': self.person.pk}))

    def test_compare_without_enough_photos_explains(self):
        self.photo(self.first)
        self.client.force_login(self.trainer)
        page = self.client.get(reverse('core:student_compare', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'duas fotos da mesma posição')
        detail = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertNotContains(detail, reverse('core:student_compare', kwargs={'pk': self.person.pk}))

    def test_student_sees_own_evolution_without_private_notes(self):
        self.photo(self.first)
        StudentNote.objects.create(person=self.person, text='SECRET-NOTE')
        self.client.force_login(self.alice)
        page = self.client.get(reverse('core:my_evolution'))
        self.assertContains(page, '10/01/2026')
        self.assertContains(page, 'data-my-assessment')
        self.assertNotContains(page, 'PRIVATE-OBSERVATION')
        self.assertNotContains(page, 'SECRET-NOTE')
        self.assertContains(page, 'id="assessmentChart"')
        for script in ('chart.umd.js', 'chart-theme.js', 'assessment_chart.js'):   # the chart code needs all three
            self.assertContains(page, script)

    def test_student_never_sees_another_students_data(self):
        Assessment.objects.create(person=self.bob.person, date=date(2026, 2, 2), weight_kg=Decimal('99.9'))
        self.client.force_login(self.alice)
        self.assertNotContains(self.client.get(reverse('core:my_evolution')), '99.9')

    def test_student_compare_uses_only_own_photos(self):
        self.photo(self.first), self.photo(self.second)
        self.client.force_login(self.alice)
        page = self.client.get(reverse('core:my_evolution_compare'))
        self.assertContains(page, 'data-compare')
        self.client.force_login(self.bob)
        self.assertContains(self.client.get(reverse('core:my_evolution_compare')), 'duas fotos da mesma posição')

    def test_access_rules(self):
        urls = [reverse('core:my_evolution'), reverse('core:my_evolution_compare')]
        for url in urls:
            self.assertEqual(Client().get(url).status_code, 302)
            self.client.force_login(self.trainer)
            self.assertNotEqual(self.client.get(url).status_code, 200)   # student area only
        staff = reverse('core:student_compare', kwargs={'pk': self.person.pk})
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(staff).status_code, 403)

    def test_menu_links(self):
        self.client.force_login(self.alice)
        self.assertContains(self.client.get(reverse('core:my_workouts')), reverse('core:my_evolution'))
        self.assertContains(self.client.get(reverse('core:my_profile')), reverse('core:my_evolution'))

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)
