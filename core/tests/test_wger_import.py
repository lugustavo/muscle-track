import io
import shutil
import tempfile
from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import translation
from PIL import Image

from core import wger_import
from core.models import Exercise, MuscleGroup

from .base import BaseTestCase

BASE = 'https://wger.de/media/exercise-images'


def row(pk, exercise, author='Everkinetic', license=1, ai=False, url=None):
    url = url or f'{BASE}/{exercise}/pose-{pk}.png'
    return {'id': pk, 'exercise': exercise, 'image': url, 'thumbnails': {'medium': url + '.400x400_q85.png'},
            'license': license, 'license_author': author, 'is_ai_generated': ai}


def info(exercise, name, category='Chest', muscles=('Pectoralis major',), translations=None):
    translations = translations if translations is not None else [{'language': 2, 'name': name}]
    return {'id': exercise, 'category': {'name': category}, 'muscles': [{'name': m} for m in muscles], 'translations': translations}


def png(color, size=(120, 60), alpha=255):
    out = io.BytesIO()
    Image.new('RGBA', size, color + (alpha,)).save(out, 'PNG')
    return out.getvalue()


class PortugueseMixin:
    """The messages are asserted in Portuguese: a test run earlier must not leave another language active."""
    def setUp(self):
        super().setUp()
        translation.activate('pt-br')
        self.addCleanup(translation.deactivate)


class SelectionTests(PortugueseMixin, SimpleTestCase):
    def pairs(self, *rows):
        return wger_import.trusted_pairs(list(rows))

    def test_a_trusted_author_with_two_pictures_is_a_pair(self):
        self.assertEqual(list(self.pairs(row(1, 10), row(2, 10))), [10])

    def test_the_first_two_by_id_are_the_pair(self):
        pair = self.pairs(row(9, 10), row(2, 10), row(5, 10))[10]
        self.assertEqual([r['id'] for r in pair], [2, 5])

    def test_everything_else_is_left_out(self):
        rows = [
            row(1, 11), row(2, 11, author='Franpol'),                    # a nickname: nothing proves the photo is theirs
            row(3, 12), row(4, 12, author=''),                           # no author: CC asks for one
            row(5, 13), row(6, 13, ai=True),                             # made by AI
            row(7, 14), row(8, 14, license=3),                           # a licence that is not CC BY-SA
            row(9, 15),                                                  # one picture is no animation
            row(10, 16), row(11, 16, url='https://evil.example/media/x.png'),   # not on wger.de
        ]
        self.assertEqual(self.pairs(*rows), {})

    def test_the_muscle_group_follows_wgers_category(self):
        self.assertEqual(wger_import.group_name(info(1, 'x', 'Abs')), 'Abdômen')
        self.assertEqual(wger_import.group_name(info(1, 'x', 'Back')), 'Costas')
        self.assertEqual(wger_import.group_name(info(1, 'x', 'Legs')), 'Pernas')
        self.assertEqual(wger_import.group_name(info(1, 'x', 'Arms', ('Biceps brachii',))), 'Bíceps')
        self.assertEqual(wger_import.group_name(info(1, 'x', 'Arms', ('Triceps brachii',))), 'Tríceps')
        self.assertEqual(wger_import.group_name(info(1, 'x', 'Unknown')), '')

    def test_the_portuguese_name_wins_and_english_is_the_fallback(self):
        both = info(1, '', translations=[{'language': 2, 'name': 'Bench Press'}, {'language': 7, 'name': 'Supino reto'}])
        self.assertEqual(wger_import._name(both), ('Supino reto', 'Bench Press'))
        self.assertEqual(wger_import._name(info(1, 'Bench Press')), ('Bench Press', 'Bench Press'))
        self.assertEqual(wger_import._name(info(1, '', translations=[{'language': 1, 'name': 'Bankdrücken'}])), ('Bankdrücken', 'Bankdrücken'))

    def test_names_are_compared_without_accents_or_punctuation(self):
        self.assertEqual(wger_import.normalize('Rosca Direta!'), wger_import.normalize('rosca  direta'))
        self.assertEqual(wger_import.normalize('Abdômen'), 'abdomen')


class FakeResponse:
    def __init__(self, body=b'', status=200, json=None):
        self.status_code = status
        self.body = body
        self._json = json

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]

    def json(self):
        if self._json is None:
            raise ValueError('not json')
        return self._json

    def close(self):
        pass


class NetworkSafetyTests(PortugueseMixin, SimpleTestCase):
    def test_only_wger_addresses_are_fetched(self):
        with mock.patch('core.wger_import.requests.get') as get:
            for url in ('https://evil.example/media/x.png', 'http://wger.de/media/x.png', 'https://wger.de.evil.example/media/x.png',
                        'https://wger.de:8443/media/x.png', 'https://wger.de/other/x.png', 'file:///etc/passwd'):
                with self.assertRaises(wger_import.WgerError, msg=url):
                    wger_import.download(url)
            get.assert_not_called()

    def test_an_api_address_must_stay_under_the_api(self):
        with mock.patch('core.wger_import.requests.get') as get:
            with self.assertRaises(wger_import.WgerError):
                wger_import._json('https://wger.de/media/x.png')
            get.assert_not_called()

    def test_redirects_are_not_followed(self):
        with mock.patch('core.wger_import.requests.get', return_value=FakeResponse(status=302)) as get:
            with self.assertRaises(wger_import.WgerError):
                wger_import.download(f'{BASE}/1/a.png')
        self.assertIs(get.call_args.kwargs['allow_redirects'], False)

    def test_a_down_site_is_a_clear_error(self):
        with mock.patch('core.wger_import.requests.get', side_effect=requests.ConnectionError('boom')):
            with self.assertRaisesMessage(wger_import.WgerError, 'Não foi possível falar com o wger.de'):
                wger_import._json(f'{wger_import.API}/exerciseimage/')

    def test_an_error_status_is_a_clear_error(self):
        with mock.patch('core.wger_import.requests.get', return_value=FakeResponse(status=503)):
            with self.assertRaisesMessage(wger_import.WgerError, '503'):
                wger_import._json(f'{wger_import.API}/exerciseimage/')

    def test_a_picture_is_downloaded_and_checked(self):
        data = png((0, 0, 0))
        with mock.patch('core.wger_import.requests.get', return_value=FakeResponse(data)):
            self.assertEqual(wger_import.download(f'{BASE}/1/a.png'), data)

    def test_something_that_is_not_a_picture_is_refused(self):
        with mock.patch('core.wger_import.requests.get', return_value=FakeResponse(b'<html>not an image</html>')):
            with self.assertRaisesMessage(wger_import.WgerError, 'não pôde ser lida'):
                wger_import.download(f'{BASE}/1/a.png')

    def test_a_huge_download_is_cut_off(self):
        with mock.patch('core.wger_import.requests.get', return_value=FakeResponse(b'x' * (wger_import.MAX_IMAGE_BYTES + 10))):
            with self.assertRaisesMessage(wger_import.WgerError, 'grande demais'):
                wger_import.download(f'{BASE}/1/a.png')

    def test_transparent_drawings_are_laid_over_white(self):
        flat = Image.open(wger_import._on_white(png((0, 0, 0), alpha=0)))
        self.assertEqual(flat.mode, 'RGB')
        self.assertEqual(flat.getpixel((3, 3)), (255, 255, 255))


class FakeWger:
    """Stands in for wger.de: the image list, the exercise details and the pictures."""
    def __init__(self):
        self.rows = [row(1, 100), row(2, 100), row(3, 200), row(4, 200), row(5, 300), row(6, 300, author='Franpol')]
        self.infos = {
            100: info(100, 'Bench Press', 'Chest'),
            200: info(200, '', 'Arms', ('Biceps brachii',),
                      translations=[{'language': 2, 'name': 'Biceps Curls'}, {'language': 7, 'name': 'Rosca direta'}]),
            300: info(300, 'Never Imported', 'Legs'),
        }
        self.broken = set()
        self.calls = 0

    def json(self, url):
        self.calls += 1
        if '/exerciseimage/' in url:
            return {'results': self.rows, 'next': None}
        return self.infos[int(url.split('/exerciseinfo/')[1].split('/')[0])]

    def download(self, url):
        if url in self.broken:
            raise wger_import.WgerError('picture failed')
        return png((10, 10, 10) if url.endswith('-1.png') or url.endswith('-3.png') else (200, 50, 50))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='mt-media-'))
class ImportViewTests(BaseTestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.wger = FakeWger()
        self.real_json = wger_import._json
        for target, fake in (('core.wger_import._json', self.wger.json), ('core.wger_import.download', self.wger.download)):
            patcher = mock.patch(target, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.login(self.trainer)
        self.url = reverse('core:exercise_import')
        self.chest = MuscleGroup.objects.get(slug='peito')

    def post(self, **fields):
        return self.client.post(self.url, fields)

    # ---- who may use it ----

    def test_students_and_visitors_cannot_open_it(self):
        self.login(self.alice)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post(pick='100').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    # ---- the preview ----

    def test_the_preview_lists_the_pairs_and_saves_nothing(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Bench Press')
        self.assertContains(response, 'Rosca direta')           # the Portuguese name when there is one
        self.assertNotContains(response, 'Never Imported')      # its second picture is by a nickname
        self.assertContains(response, 'Everkinetic')
        self.assertContains(response, 'CC BY-SA')
        self.assertContains(response, f'{BASE}/100/pose-1.png.400x400_q85.png')
        self.assertEqual(Exercise.objects.count(), 2)           # only the two from the base fixtures

    def test_the_catalog_is_kept_for_a_while(self):
        self.client.get(self.url)
        calls = self.wger.calls
        self.client.get(self.url)
        self.assertEqual(self.wger.calls, calls)
        self.client.get(self.url + '?refresh=1')
        self.assertGreater(self.wger.calls, calls)

    def test_an_exercise_of_mine_with_the_same_name_and_no_picture_is_suggested(self):
        Exercise.objects.create(exercise_name='Rosca Direta', muscle_group=self.chest)
        suggested = Exercise.objects.get(exercise_name='Rosca Direta')
        rows = {r['id']: r for r in self.client.get(self.url).context['rows']}
        self.assertEqual(rows[200]['match'], suggested.pk)
        self.assertIsNone(rows[100]['match'])

    def test_the_wger_muscle_group_is_preselected(self):
        rows = {r['id']: r for r in self.client.get(self.url).context['rows']}
        self.assertEqual(rows[200]['group_pk'], MuscleGroup.objects.get(name='Bíceps').pk)
        self.assertEqual(rows[100]['group_pk'], self.chest.pk)

    def test_an_exercise_that_already_has_a_picture_is_not_offered_as_a_target(self):
        self.supino.media_url = 'https://example.com/supino.gif'
        self.supino.save()
        targets = [e.pk for e in self.client.get(self.url).context['targets']]
        self.assertNotIn(self.supino.pk, targets)
        self.assertIn(self.crucifixo.pk, targets)

    def test_wger_being_down_shows_a_message_not_an_error(self):
        with mock.patch('core.wger_import._json', side_effect=wger_import.WgerError('Sem internet no servidor.')):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sem internet no servidor.')
        self.assertContains(response, 'Tentar de novo')

    def test_the_exercise_list_links_to_the_import(self):
        self.assertContains(self.client.get(reverse('core:exercise_list')), reverse('core:exercise_import'))

    # ---- importing ----

    def import_new(self, pick='100', name='Supino reto', group=None):
        return self.post(pick=pick, **{f'name_{pick}': name, f'target_{pick}': 'new', f'group_{pick}': (group or self.chest).pk})

    def test_a_new_exercise_comes_with_the_animation_and_the_credit(self):
        response = self.import_new()
        self.assertRedirects(response, reverse('core:exercise_list'), fetch_redirect_response=False)
        exercise = Exercise.objects.get(exercise_name='Supino reto')
        self.assertEqual(exercise.muscle_group, self.chest)
        self.assertEqual(exercise.media_type, 'gif')
        self.assertEqual(exercise.media_license, 'cc-by-sa')
        self.assertIn('Everkinetic (Greg Priday)', exercise.media_credit)
        self.assertIn('wger.de', exercise.media_credit)
        self.assertEqual(exercise.media_source_url, 'https://wger.de/en/exercise/100/view/')
        self.assertEqual(exercise.media_info['kind'], 'image')
        with Image.open(exercise.media_file.path) as animation:
            self.assertEqual(animation.n_frames, 2 + 2 * 3)             # two poses and the cross-fade
        self.assertTrue(exercise.thumbnail)

    def test_the_credit_reaches_the_public_credits_page(self):
        self.import_new()
        self.client.logout()
        page = self.client.get(reverse('core:credits'))
        self.assertContains(page, 'Supino reto')
        self.assertContains(page, 'Everkinetic (Greg Priday)')
        self.assertContains(page, 'https://wger.de/en/exercise/100/view/')

    def test_the_animation_can_go_to_an_exercise_of_mine_without_changing_it(self):
        self.supino.description = 'Minha descrição'
        self.supino.save()
        response = self.post(pick='100', name_100='Outro nome', target_100=str(self.supino.pk))
        self.assertRedirects(response, reverse('core:exercise_list'))
        self.supino.refresh_from_db()
        self.assertEqual(self.supino.exercise_name, 'Supino')
        self.assertEqual(self.supino.description, 'Minha descrição')
        self.assertTrue(self.supino.media_file.name.endswith('.webp'))
        self.assertEqual(self.supino.media_source_url, 'https://wger.de/en/exercise/100/view/')
        self.assertEqual(Exercise.objects.count(), 2)

    def test_an_exercise_that_has_a_picture_is_never_overwritten(self):
        self.supino.media_url = 'https://example.com/supino.gif'
        self.supino.save()
        response = self.post(pick='100', name_100='x', target_100=str(self.supino.pk))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.supino.refresh_from_db()
        self.assertFalse(self.supino.media_file)
        self.assertEqual(self.supino.media_url, 'https://example.com/supino.gif')
        self.assertContains(self.client.get(self.url), 'não está mais disponível')

    def test_the_same_exercise_is_not_imported_twice(self):
        self.import_new()
        self.import_new(name='Supino reto 2')
        self.assertEqual(Exercise.objects.filter(media_source_url='https://wger.de/en/exercise/100/view/').count(), 1)
        self.assertContains(self.client.get(self.url), 'Já importado')

    def test_a_name_that_exists_is_refused(self):
        response = self.import_new(name='supino')
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(Exercise.objects.filter(exercise_name__iexact='supino').count(), 1)
        self.assertContains(self.client.get(self.url), 'já existe um exercício com esse nome')

    def test_a_new_exercise_needs_a_name_and_a_real_group(self):
        self.import_new(name='   ')
        self.post(pick='100', name_100='Supino reto', target_100='new', group_100='99999')
        self.assertFalse(Exercise.objects.filter(media_source_url__contains='/100/').exists())

    def test_one_failure_does_not_stop_the_others(self):
        self.wger.broken.add(f'{BASE}/100/pose-1.png')
        response = self.post(pick=['100', '200'], name_100='Supino reto', target_100='new', group_100=self.chest.pk,
                             name_200='Rosca direta', target_200='new', group_200=self.chest.pk)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertFalse(Exercise.objects.filter(exercise_name='Supino reto').exists())
        self.assertTrue(Exercise.objects.filter(exercise_name='Rosca direta').exists())
        page = self.client.get(self.url)
        self.assertContains(page, '1 animação importada')
        self.assertContains(page, 'picture failed')

    def test_choosing_nothing_says_so(self):
        response = self.post(name_100='x')
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertContains(self.client.get(self.url), 'Nada foi escolhido')

    def test_only_what_is_listed_can_be_picked(self):
        # an id wger never offered (a nickname's photo pair, or made up) cannot be forced through the form
        self.post(pick=['300', '999'], name_300='x', target_300='new', group_300=self.chest.pk)
        self.assertEqual(Exercise.objects.count(), 2)

    def test_at_most_a_few_are_imported_at_once(self):
        self.wger.rows = [row(i * 2, 1000 + i) for i in range(1, 21)] + [row(i * 2 + 1, 1000 + i) for i in range(1, 21)]
        self.wger.infos = {1000 + i: info(1000 + i, f'Exercise {i:02d}') for i in range(1, 21)}
        cache.clear()
        fields = {'pick': [str(1000 + i) for i in range(1, 21)]}
        for i in range(1, 21):
            fields.update({f'name_{1000 + i}': f'Exercise {i:02d}', f'target_{1000 + i}': 'new', f'group_{1000 + i}': self.chest.pk})
        self.post(**fields)
        self.assertEqual(Exercise.objects.filter(media_source_url__startswith='https://wger.de/').count(), wger_import.MAX_IMPORT_AT_ONCE)
        self.assertContains(self.client.get(self.url), 'no máximo')

    # ---- English ----

    def english(self):
        from django.conf import settings
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'

    def test_the_page_and_its_messages_are_in_english(self):
        from .test_i18n_english_pages import leaks, visible
        self.english()
        page = self.client.get(self.url)
        self.assertContains(page, 'Import animations')
        self.assertEqual(leaks(visible(page)), [])
        self.import_new(name='Bench')
        listing = self.client.get(reverse('core:exercise_list'))
        self.assertContains(listing, '1 animation imported.')
        self.assertEqual(Exercise.objects.get(exercise_name='Bench').media_credit,
                         'Everkinetic (Greg Priday), via wger.de; the two images were joined into one animation')

    def test_the_down_message_is_in_english(self):
        from .test_i18n_english_pages import leaks, visible
        self.english()
        with mock.patch('core.wger_import._json', side_effect=self.real_json), \
                mock.patch('core.wger_import.requests.get', side_effect=requests.ConnectionError('boom')):
            page = self.client.get(self.url)
        self.assertContains(page, 'Could not reach wger.de right now')
        self.assertEqual(leaks(visible(page)), [])
