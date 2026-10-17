import shutil
from datetime import date

from django.test import Client, override_settings
from django.urls import reverse
from django.utils import translation

from core import evolution, photo_positions
from core.models import Assessment, PhotoPosition, ProgressPhoto

from .base import BaseTestCase
from .test_evolution import MEDIA, picture


@override_settings(MEDIA_ROOT=MEDIA)
class PhotoPositionTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.assessment = Assessment.objects.create(person=self.person, date=date(2026, 3, 1))

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def photo(self, key, assessment=None, day=date(2026, 3, 1)):
        return ProgressPhoto.objects.create(person=self.person, assessment=assessment, angle=key, date=day, image=picture())

    def url(self, pk, action):
        return reverse('core:photo_position_action', kwargs={'pk': pk, 'action': action})

    def test_the_four_standard_positions_exist_with_the_keys_old_photos_hold(self):
        self.assertEqual([(p.key, p.name) for p in PhotoPosition.objects.all()],
                         [('front', 'Frente'), ('left', 'Lado esquerdo'), ('right', 'Lado direito'), ('back', 'Costas')])
        self.assertEqual([p.value for p in photo_positions.active_positions()], ['front', 'left', 'right', 'back'])

    def test_standard_names_follow_the_language_and_custom_ones_show_as_typed(self):
        custom = PhotoPosition.objects.create(name='Perfil 45°')
        with translation.override('en'):
            self.assertEqual([p.label for p in PhotoPosition.objects.all()][:2], ['Front', 'Left side'])
            self.assertEqual(custom.label, 'Perfil 45°')
        with translation.override('pt-br'):
            self.assertEqual(PhotoPosition.objects.get(key='back').label, 'Costas')

    def test_a_new_position_gets_a_unique_key_and_goes_last(self):
        a = PhotoPosition.objects.create(name='Perfil 45°')
        b = PhotoPosition.objects.create(name='Perfil 45 ')
        self.assertEqual(a.key, 'perfil-45')
        self.assertNotEqual(a.key, b.key)
        self.assertGreater(b.order, a.order)
        self.assertNotEqual(PhotoPosition.objects.create(name='Other').key, 'other')   # "other" is reserved for extras

    def test_assessment_slots_follow_the_trainers_sequence(self):
        extra = PhotoPosition.objects.create(name='Perfil 45°')
        PhotoPosition.objects.filter(key='right').update(is_active=False)
        mine = self.photo(extra.key, self.assessment)
        legacy = self.photo('right', self.assessment)   # a photo of a position that was turned off
        a = evolution.assessments_with_photos(self.person)[0]
        self.assertEqual([s['angle'] for s in a.photo_slots], ['front', 'left', 'back', extra.key])
        self.assertEqual([s['photo'] for s in a.photo_slots][-1], mine)
        self.assertEqual(a.other_photos, [legacy])    # still kept, shown among the extras
        self.assertEqual(legacy.angle_label, 'Lado direito')

    def test_comparison_uses_the_new_positions_and_ignores_turned_off_ones(self):
        extra = PhotoPosition.objects.create(name='Perfil 45°')
        self.photo(extra.key, day=date(2026, 3, 1)), self.photo(extra.key, day=date(2026, 5, 1))
        data = evolution.compare(self.person, extra.key)
        self.assertEqual([a.value for a in data['angles']], [extra.key])
        self.assertEqual(len(data['having']), 2)
        extra.is_active = False
        extra.save()
        self.assertEqual(evolution.compare(self.person, extra.key)['angles'], [])

    def test_forms_offer_the_active_positions_and_other(self):
        extra = PhotoPosition.objects.create(name='Perfil 45°')
        PhotoPosition.objects.filter(key='left').update(is_active=False)
        self.client.force_login(self.trainer)
        page = self.client.get(reverse('core:photo_upload', kwargs={'pk': self.person.pk}))
        self.assertContains(page, f'value="{extra.key}"')
        self.assertNotContains(page, 'value="left"')
        self.assertContains(page, 'value="other"')
        bad = self.client.post(reverse('core:photo_upload', kwargs={'pk': self.person.pk}),
                               {'angle': 'left', 'image': picture()})
        self.assertEqual(bad.status_code, 200)   # a turned-off position is no longer a valid choice
        self.client.force_login(self.alice)
        mine = self.client.get(reverse('core:my_photo_upload'))
        self.assertContains(mine, f'value="{extra.key}"')

    def test_save_and_next_walks_the_new_sequence(self):
        extra = PhotoPosition.objects.create(name='Perfil 45°')
        for key in ('front', 'left', 'right'):
            self.photo(key, self.assessment)
        self.client.force_login(self.trainer)
        done = self.client.post(reverse('core:photo_upload', kwargs={'pk': self.person.pk}),
                                {'assessment': self.assessment.pk, 'angle': 'back', 'image': picture(), 'save_next': '1'})
        self.assertEqual(done.status_code, 302)
        self.assertIn(f'angle={extra.key}', done['Location'])

    def test_management_page_adds_renames_reorders_toggles_and_deletes(self):
        self.client.force_login(self.trainer)
        page = reverse('core:photo_positions')
        self.assertContains(self.client.get(page), 'data-position-list')
        self.client.post(page, {'new-name': 'Perfil 45°'})
        new = PhotoPosition.objects.get(name='Perfil 45°')
        front = PhotoPosition.objects.get(key='front')
        self.client.post(self.url(new.pk, 'up'))
        self.assertEqual([p.key for p in PhotoPosition.objects.all()][-2:], [new.key, 'back'])
        self.client.post(self.url(front.pk, 'rename'), {f'p{front.pk}-name': 'Frontal'})
        front.refresh_from_db()
        self.assertEqual((front.name, front.key), ('Frontal', 'front'))   # the key (and so the photos) stay
        self.client.post(self.url(front.pk, 'toggle'))
        front.refresh_from_db()
        self.assertFalse(front.is_active)
        self.client.post(self.url(new.pk, 'delete'))
        self.assertFalse(PhotoPosition.objects.filter(pk=new.pk).exists())

    def test_a_position_with_photos_cannot_be_deleted(self):
        front = PhotoPosition.objects.get(key='front')
        self.photo('front', self.assessment)
        self.client.force_login(self.trainer)
        self.client.post(self.url(front.pk, 'delete'))
        self.assertTrue(PhotoPosition.objects.filter(pk=front.pk).exists())

    def test_names_must_be_unique_and_not_other(self):
        self.client.force_login(self.trainer)
        page = reverse('core:photo_positions')
        for name in ('costas', 'Outra', 'Other', '   '):
            self.client.post(page, {'new-name': name})
        self.assertEqual(PhotoPosition.objects.count(), 4)

    def test_edge_cases_of_the_actions(self):
        self.client.force_login(self.trainer)
        first = PhotoPosition.objects.first()
        self.client.post(self.url(first.pk, 'up'))   # nothing above it: no change, no error
        self.assertEqual(PhotoPosition.objects.first(), first)
        self.assertEqual(self.client.post(self.url(first.pk, 'explode')).status_code, 404)
        self.assertEqual(self.client.get(self.url(first.pk, 'toggle')).status_code, 405)

    def test_only_the_trainer_manages_positions(self):
        page = reverse('core:photo_positions')
        self.assertEqual(Client().get(page).status_code, 302)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(page).status_code, 403)
        self.assertEqual(self.client.post(self.url(PhotoPosition.objects.first().pk, 'toggle')).status_code, 403)

    def test_the_students_file_links_to_the_page(self):
        self.client.force_login(self.trainer)
        detail = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(detail, 'data-photo-positions-link')
