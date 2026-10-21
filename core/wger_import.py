"""Importing exercise animations from wger (https://wger.de), an open exercise database.

Only what can be used safely comes in: the line drawings made by the Everkinetic project, which wger publishes under
CC BY-SA, as pairs (start and end of the movement). Everything else wger holds is left out on purpose: photos sent by
anyone (the author is just a nickname and nothing checks that the picture is theirs), AI-made pictures, and pictures
without an author (every CC licence asks for one).

Nothing is stored until the trainer confirms in the preview. The two pictures of a pair are downloaded then and joined
into one looping animation (core/exercise_animation.py); the credit, the licence and the link back to wger go with it
(core/credits.py shows them). The text (names are shown as a suggestion; descriptions are not imported) stays the
trainer's own.

Network: the app calls wger from the server. Every address is checked to be https://wger.de before it is fetched, sizes
and times are limited, and a failure never breaks the page: it turns into a WgerError the view explains."""
import io
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils.translation import gettext as _
from PIL import Image

from . import exercise_animation
from .models import Exercise

logger = logging.getLogger(__name__)

HOST = 'wger.de'
API = f'https://{HOST}/api/v2'
PAGE_URL = f'https://{HOST}/en/exercise/%d/view/'
TRUSTED_AUTHORS = {'Everkinetic'}
CREDIT_AUTHOR = 'Everkinetic (Greg Priday)'
LICENSE_IDS = {1, 2}            # wger's CC BY-SA 3 and CC BY-SA 4 (the Exercise licence choice is 'cc-by-sa')
ENGLISH, PORTUGUESE = 2, 7      # wger language ids
TIMEOUT = (5, 20)               # connect, read: seconds
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_IMPORT_AT_ONCE = 15
CACHE_KEY = 'wger_import_catalog'
CACHE_SECONDS = 24 * 3600
WORKERS = 6

# wger's category -> the app's standard muscle group (the trainer can change it in the preview)
CATEGORY_GROUPS = {
    'Abs': 'Abdômen', 'Arms': 'Bíceps', 'Back': 'Costas', 'Calves': 'Panturrilha',
    'Cardio': 'Cardio', 'Chest': 'Peito', 'Legs': 'Pernas', 'Shoulders': 'Ombros',
}
TRICEPS = 'Triceps brachii'


class WgerError(Exception):
    """wger could not be reached or answered something unusable; the message is safe to show the trainer."""


def _allowed(url, prefix):
    parts = urlparse(url or '')
    return parts.scheme == 'https' and parts.hostname == HOST and parts.port in (None, 443) and parts.path.startswith(prefix)


def _get(url, prefix, stream=False):
    if not _allowed(url, prefix):
        raise WgerError(_('Endereço fora do wger.de: não foi acessado.'))
    try:
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=False, stream=stream)
    except requests.RequestException as error:
        logger.warning('wger request failed: %s', error.__class__.__name__)
        raise WgerError(_('Não foi possível falar com o wger.de agora. Verifique a internet do servidor e tente de novo.'))
    if response.status_code != 200:
        response.close()
        raise WgerError(_('O wger.de respondeu com erro (%(code)s). Tente de novo mais tarde.') % {'code': response.status_code})
    return response


def _json(url):
    response = _get(url, '/api/v2/')
    try:
        return response.json()
    except ValueError:
        raise WgerError(_('O wger.de respondeu algo que não foi entendido.'))


def download(url):
    """The bytes of an image on wger.de (at most MAX_IMAGE_BYTES, and it must really be an image)."""
    response = _get(url, '/media/', stream=True)
    data = b''
    try:
        for chunk in response.iter_content(64 * 1024):
            data += chunk
            if len(data) > MAX_IMAGE_BYTES:
                raise WgerError(_('Uma imagem do wger.de é grande demais e foi ignorada.'))
    except requests.RequestException:
        raise WgerError(_('Não foi possível baixar a imagem do wger.de.'))
    finally:
        response.close()
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception:
        raise WgerError(_('Uma imagem do wger.de não pôde ser lida.'))
    return data


def _images():
    """Every image wger lists, as dicts (all pages)."""
    rows = []
    url = f'{API}/exerciseimage/?format=json&limit=100'
    for _page in range(20):    # 383 images today; the cap only guards against a loop
        body = _json(url)
        rows.extend(body.get('results') or [])
        url = body.get('next')
        if not url:
            break
    return rows


def trusted_pairs(rows):
    """{wger exercise id: [first image, second image]} for the exercises with two or more usable images.

    Usable = a trusted author, a CC BY-SA licence, not made by AI. The first two by id are the pair (the start and the
    end of the movement; a few exercises have extra variants after them)."""
    by_exercise = {}
    for row in sorted(rows, key=lambda r: r.get('id') or 0):
        if (row.get('license_author') or '').strip() not in TRUSTED_AUTHORS:
            continue
        if row.get('license') not in LICENSE_IDS or row.get('is_ai_generated'):
            continue
        if not _allowed(row.get('image'), '/media/'):
            continue
        by_exercise.setdefault(row.get('exercise'), []).append(row)
    return {exercise: images[:2] for exercise, images in by_exercise.items() if exercise and len(images) >= 2}


def _name(info):
    """(suggested name, English name): the Portuguese name when wger has one."""
    names = {t.get('language'): (t.get('name') or '').strip() for t in info.get('translations') or []}
    english = names.get(ENGLISH) or next((n for n in names.values() if n), '')
    return names.get(PORTUGUESE) or english, english


def group_name(info):
    """The app's standard muscle group for a wger exercise (by its category; arms by the main muscle)."""
    category = (info.get('category') or {}).get('name', '')
    if category == 'Arms' and any(m.get('name') == TRICEPS for m in info.get('muscles') or []):
        return 'Tríceps'
    return CATEGORY_GROUPS.get(category, '')


def _entry(exercise_id, images):
    info = _json(f'{API}/exerciseinfo/{exercise_id}/?format=json')
    name, english = _name(info)
    if not name:
        return None
    return {
        'id': exercise_id, 'name': name[:200], 'english': english, 'group': group_name(info),
        'page_url': PAGE_URL % exercise_id,
        'images': [{'url': i['image'], 'thumb': (i.get('thumbnails') or {}).get('medium') or i['image']} for i in images],
    }


def catalog(refresh=False):
    """The importable exercises, by name. Cached for a day (it takes a few seconds to build)."""
    if not refresh:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached
    pairs = trusted_pairs(_images())
    with ThreadPoolExecutor(WORKERS) as pool:
        entries = list(pool.map(lambda item: _entry(*item), pairs.items()))
    items = sorted((e for e in entries if e), key=lambda e: e['name'].casefold())
    cache.set(CACHE_KEY, items, CACHE_SECONDS)
    return items


def normalize(text):
    """Lower-case, no accents, no punctuation: to tell that two exercise names are the same."""
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()


def imported_urls():
    """The wger pages of the exercises already imported."""
    return set(Exercise.objects.filter(media_source_url__startswith=f'https://{HOST}/').values_list('media_source_url', flat=True))


def credit():
    return _('%(author)s, via wger.de; as duas imagens foram unidas em uma animação') % {'author': CREDIT_AUTHOR}


def _on_white(data):
    """The picture with any transparency laid over white (the drawings are black lines: on a dark page they would vanish)."""
    with Image.open(io.BytesIO(data)) as image:
        rgba = image.convert('RGBA')
    page = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
    out = io.BytesIO()
    Image.alpha_composite(page, rgba).convert('RGB').save(out, 'PNG')
    return io.BytesIO(out.getvalue())


def animation(entry):
    """(bytes, extension) of the animation of a catalog entry: its two pictures downloaded and joined."""
    start, end = (_on_white(download(image['url'])) for image in entry['images'])
    return exercise_animation.build(start, end, 'normal', smooth=True)


def build_all(entries):
    """[(entry, (bytes, extension)) | (entry, WgerError)]: the animations of the entries, made in parallel."""
    def one(entry):
        try:
            return entry, animation(entry)
        except WgerError as error:
            return entry, error
        except Exception:    # a picture Pillow cannot join: this one fails, the others go on
            logger.exception('wger animation failed for %s', entry['id'])
            return entry, WgerError(_('Não foi possível montar a animação.'))
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(one, entries))


def store(entry, data, extension, name, group, target=None):
    """Save the animation as a new exercise, or on `target` (an exercise with no media yet). Returns the exercise."""
    exercise = target or Exercise(exercise_name=name, muscle_group=group)
    exercise.media_type = 'gif'
    exercise.media_license = 'cc-by-sa'
    exercise.media_credit = credit()
    exercise.media_source_url = entry['page_url']
    exercise.media_file.save(f'wger_{entry["id"]}.{extension}', ContentFile(data), save=False)
    exercise.save()
    return exercise
