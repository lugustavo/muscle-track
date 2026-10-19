import json
import re
from datetime import date
from pathlib import Path

from django.test import override_settings
from django.urls import reverse

from core import photo_guides
from core.models import Assessment, PhotoPosition, ProgressPhoto

from .base import BaseTestCase
from .test_assessment_photos import MEDIA, picture

ROOT = Path(__file__).resolve().parents[2]


def guides_of(response):
    """The JSON the page hands to the camera script."""
    match = re.search(r'<script id="camera-guides" type="application/json">(.*?)</script>', response.content.decode(), re.S)
    return json.loads(match.group(1))


class GuideKindTests(BaseTestCase):
    def test_standard_positions_get_their_silhouette(self):
        found = {p.key: photo_guides.guide_for(p.key, p.name) for p in PhotoPosition.objects.all()}
        self.assertEqual(found, {'front': 'front', 'left': 'side-left', 'right': 'side-right', 'back': 'back'})

    def test_names_the_trainer_typed(self):
        guide = photo_guides.guide_for
        self.assertEqual(guide('lado-esquerdo', 'Lado esquerdo'), 'side-left')     # "lado" must not turn it into a plain side
        self.assertEqual(guide('perfil', 'Perfil'), 'side-right')
        self.assertEqual(guide('posicao', 'Visão posterior'), 'back')
        self.assertEqual(guide('frontal', 'Frontal relaxado'), 'front')
        self.assertEqual(guide('45-graus', '45 graus'), '')                         # unknown: frame and grid only


@override_settings(MEDIA_ROOT=MEDIA)
class GuideConfigTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.old = Assessment.objects.create(person=self.person, date=date(2026, 1, 10), weight_kg=70)
        self.new = Assessment.objects.create(person=self.person, date=date(2026, 3, 10), weight_kg=68)

    def photo(self, assessment, angle, person=None):
        photo = ProgressPhoto(person=person or self.person, assessment=assessment, angle=angle, date=assessment.date, image=picture())
        photo.save()
        return photo

    def test_the_previous_photo_of_each_position_is_offered_newest_first_two_at_most(self):
        first, second = self.photo(self.old, 'front'), self.photo(self.new, 'front')
        third = ProgressPhoto(person=self.person, angle='front', date=date(2026, 5, 1), image=picture())
        third.save()
        front = photo_guides.config(self.person)['positions']['front']
        self.assertEqual([g['assessment'] for g in front['ghosts']], [None, self.new.pk])    # the loose one is the newest
        self.assertEqual(front['ghosts'][0]['url'], reverse('core:photo_thumb', kwargs={'pk': third.pk}))
        self.assertNotIn(first.pk, [int(g['url'].split('/')[2]) for g in front['ghosts']])  # only two are kept
        self.assertEqual(photo_guides.config(self.person)['positions']['back']['ghosts'], [])
        self.assertEqual(photo_guides.config(self.person)['ratio'], [3, 4])
        self.assertEqual(second.angle, 'front')

    def test_never_offers_another_students_photo(self):
        other = Assessment.objects.create(person=self.bob.person, date=date(2026, 2, 1))
        secret = ProgressPhoto(person=self.bob.person, assessment=other, angle='front', date=other.date, image=picture())
        secret.save()
        front = photo_guides.config(self.person)['positions']['front']
        self.assertEqual(front['ghosts'], [])
        self.assertNotIn(str(secret.pk), json.dumps(photo_guides.config(self.person)))

    def test_the_guide_of_each_position_is_in_the_config(self):
        positions = photo_guides.config(self.person)['positions']
        self.assertEqual({k: v['guide'] for k, v in positions.items()},
                         {'front': 'front', 'left': 'side-left', 'right': 'side-right', 'back': 'back', 'other': ''})


@override_settings(MEDIA_ROOT=MEDIA)
class CameraPagesTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        assessment = Assessment.objects.create(person=self.person, date=date(2026, 1, 10))
        self.photo = ProgressPhoto(person=self.person, assessment=assessment, angle='front', date=assessment.date, image=picture())
        self.photo.save()

    def check(self, html):
        for marker in ('data-live', 'data-shoot', 'data-timer', 'data-toggle-guides', 'data-toggle-ghost', 'data-flip',
                       'css/camera_guides.css', 'js/photo_guides.js', 'js/photo_capture.js', 'data-native-camera'):
            self.assertIn(marker, html)

    def test_trainer_form_has_the_camera_with_guides(self):
        self.client.force_login(self.trainer)
        page = self.client.get(reverse('core:photo_upload', kwargs={'pk': self.person.pk}))
        self.check(page.content.decode())
        self.assertEqual(guides_of(page)['positions']['front']['ghosts'][0]['url'], reverse('core:photo_thumb', kwargs={'pk': self.photo.pk}))

    def test_student_form_has_the_camera_with_guides_and_only_her_photos(self):
        self.client.force_login(self.alice)
        page = self.client.get(reverse('core:my_photo_upload'))
        self.check(page.content.decode())
        self.assertEqual(len(guides_of(page)['positions']['front']['ghosts']), 1)
        self.client.force_login(self.bob)
        self.assertEqual(guides_of(self.client.get(reverse('core:my_photo_upload')))['positions']['front']['ghosts'], [])

    def test_the_overlay_is_texts_from_the_template_not_hardcoded_in_the_script(self):
        template = (ROOT / 'templates/core/_camera_live.html').read_text()
        for attribute in ('data-text-no-timer', 'data-text-previous', 'data-text-guides-tip'):
            self.assertIn(attribute, template)
        css = (ROOT / 'static/css/camera_guides.css').read_text()
        self.assertNotRegex(css, r'#[0-9a-fA-F]{3,8}\b')           # theme variables only
