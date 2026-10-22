"""What an exercise's picture / animation / video is and how to show it.

The trainer may upload a file (a GIF, an animated WebP, a JPG, an MP4...) and / or paste a link. The file wins when both
exist (a link left over from before must not hide the file that was uploaded later). The kind is read from the extension,
not from the "media type" field, which is easy to leave at its default: a GIF saved as "image" is still a GIF.

  image  - shown inline (a GIF or animated WebP moves by itself)
  video  - shown inline with controls (muted, so it can loop on a phone)
  link   - anything else (YouTube, Vimeo, a page): offered as a link that opens in a new tab"""
import io
import uuid
from os.path import splitext
from urllib.parse import urlparse

IMAGE_EXTENSIONS = {'.gif', '.jpg', '.jpeg', '.png', '.webp', '.avif', '.svg'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.m4v', '.ogv'}
THUMBNAIL_SIZE = (400, 300)
AUTO_PREFIX = 'auto_'   # name prefix of a thumbnail made from the media file, so it can be told from one the trainer uploaded


def _extension(path):
    return splitext(urlparse(path or '').path.lower())[1]


def kind_of(path):
    extension = _extension(path)
    if extension in IMAGE_EXTENSIONS:
        return 'image'
    if extension in VIDEO_EXTENSIONS:
        return 'video'
    return 'link'


def web_url(value):
    """The value when it is a real web address (the link field is free text), else ''."""
    return value if urlparse(value or '').scheme in ('http', 'https') else ''


def describe(exercise):
    """{'kind': 'image'|'video'|'link', 'url': str} of the exercise's media, or None when it has none."""
    if exercise.media_file:
        return {'kind': kind_of(exercise.media_file.name), 'url': exercise.media_file.url}
    url = web_url(exercise.media_url)
    if url:
        return {'kind': kind_of(url), 'url': url}
    return None


def first_frame_thumbnail(media_file):
    """JPEG bytes of the first frame of an image / GIF / WebP file, or None when it cannot be read.

    Works on a file just uploaded (not saved yet: it is rewound, never closed, so it can still be stored) and on one
    already in storage."""
    from PIL import Image

    committed = getattr(media_file, '_committed', True)
    try:
        media_file.open('rb')
        media_file.seek(0)
        with Image.open(media_file) as image:
            image.seek(0)
            frame = image.convert('RGB')
        frame.thumbnail(THUMBNAIL_SIZE)
        out = io.BytesIO()
        frame.save(out, 'JPEG', quality=82, optimize=True)
        return out.getvalue()
    except Exception:   # not an image Pillow can read (a broken upload): no thumbnail, nothing breaks
        return None
    finally:
        try:
            if committed:
                media_file.close()
            else:
                media_file.seek(0)
        except Exception:
            pass


STILL_MAX_SIDE = 1600           # a picture of an exercise is shown on a phone: more pixels only cost data


def clean_still_image(media_file):
    """A new JPG/PNG upload re-encoded without its metadata (EXIF: GPS position, camera, date), turned upright and at most
    STILL_MAX_SIDE px on its longest side. Returns (name, ContentFile) or None when the file is not a still JPG/PNG
    (GIFs, WebP, videos and everything else are kept exactly as sent: re-encoding would stop an animation)."""
    from django.core.files.base import ContentFile
    from PIL import Image, ImageOps

    extension = _extension(media_file.name)
    if extension not in ('.jpg', '.jpeg', '.png'):
        return None
    try:
        media_file.seek(0)
        with Image.open(media_file) as image:
            if getattr(image, 'is_animated', False):
                return None
            image = ImageOps.exif_transpose(image)
            image.thumbnail((STILL_MAX_SIDE, STILL_MAX_SIDE))
            buffer = io.BytesIO()
            if extension == '.png':
                image.save(buffer, 'PNG', optimize=True)
                name = f'{uuid.uuid4().hex}.png'
            else:
                image.convert('RGB').save(buffer, 'JPEG', quality=88, optimize=True)
                name = f'{uuid.uuid4().hex}.jpg'
    except Exception:   # a broken file: keep it as it came, the form already accepted it
        media_file.seek(0)
        return None
    return name, ContentFile(buffer.getvalue())
