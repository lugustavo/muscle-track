"""An exercise animation made from two photos: the starting position and the finishing position.

The trainer takes both from the same spot with the guided camera (static/js/exercise_capture.js) or picks two pictures;
this joins them into one looping animated image (WebP, or GIF where WebP animation is not available) that the app shows
like any uploaded GIF. The two poses alternate (start, end, start...), each held for a moment, optionally with a few
cross-fade frames between them for a smoother look."""
import io

from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps

MAX_SIDE = 800                      # the animation is for a phone screen: small files, fast to load
SPEEDS = {'slow': 1200, 'normal': 800, 'fast': 500}   # how long each pose is held, in milliseconds
SPEED_CHOICES = [('slow', _('Lenta')), ('normal', _('Normal')), ('fast', _('Rápida'))]
FADE_STEPS = 3                      # cross-fade frames each way when "smooth" is on
FADE_MS = 90


def _open(file):
    """An RGB image from an upload, turned upright by its EXIF orientation."""
    if hasattr(file, 'seek'):
        file.seek(0)
    with Image.open(file) as image:
        return ImageOps.exif_transpose(image).convert('RGB')


def _fit(image, size):
    """`image` cropped from the centre to the aspect of `size` and scaled to it (the second photo may differ slightly)."""
    return ImageOps.fit(image, size, Image.LANCZOS, centering=(0.5, 0.5))


def frames(start, end, speed='normal', smooth=False):
    """([PIL images], [duration in ms of each]) of one loop."""
    first = _open(start)
    last = _open(end)
    scale = min(1, MAX_SIDE / max(first.size))
    size = (max(1, round(first.width * scale)), max(1, round(first.height * scale)))
    first = first.resize(size, Image.LANCZOS)
    last = _fit(last, size)
    hold = SPEEDS.get(speed, SPEEDS['normal'])
    images, durations = [first], [hold]
    if smooth:
        for step in range(1, FADE_STEPS + 1):
            images.append(Image.blend(first, last, step / (FADE_STEPS + 1)))
            durations.append(FADE_MS)
    images.append(last)
    durations.append(hold)
    if smooth:
        for step in range(FADE_STEPS, 0, -1):
            images.append(Image.blend(first, last, step / (FADE_STEPS + 1)))
            durations.append(FADE_MS)
    return images, durations


def build(start, end, speed='normal', smooth=False):
    """(bytes, extension) of the looping animation: animated WebP, or a GIF when WebP animation cannot be written."""
    images, durations = frames(start, end, speed, smooth)
    out = io.BytesIO()
    try:
        images[0].save(out, 'WEBP', save_all=True, append_images=images[1:], duration=durations, loop=0, quality=80, method=4)
        return out.getvalue(), 'webp'
    except (OSError, ValueError, KeyError):   # a Pillow build without WebP animation
        out = io.BytesIO()
        images[0].save(out, 'GIF', save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True)
        return out.getvalue(), 'gif'
