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
        self.assertContains(page, 'pelo menos duas avaliações')
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
        self.assertContains(self.client.get(reverse('core:my_evolution_compare')), 'pelo menos duas avaliações')

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
