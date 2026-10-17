"""The positions of the photo sequence (front, back...) the trainer chose, and how photos name theirs.

A position is a `PhotoPosition` row; photos store its `key`. "Other" is always available for extra photos."""
from collections import namedtuple

from django.utils.translation import gettext as _

from .models import PhotoPosition, ProgressPhoto

Position = namedtuple('Position', 'value label')   # `value` is the key a photo stores
OTHER = ProgressPhoto.Angle.OTHER.value


def active_positions():
    """The positions asked for in every assessment, in the trainer's order."""
    return [Position(p.key, p.label) for p in PhotoPosition.objects.filter(is_active=True)]


def labels():
    """Name of every key a photo may hold (inactive positions keep their name; "other" is always there)."""
    found = {p.key: p.label for p in PhotoPosition.objects.all()}
    found[OTHER] = _('Outra')
    return found


def choices(positions=None):
    """(key, name) for a select: the active positions and then "Outra"."""
    positions = active_positions() if positions is None else positions
    return [(p.value, p.label) for p in positions] + [(OTHER, _('Outra'))]


def name_photos(photos, names=None):
    """Give each photo its position name in one go (so a list does not ask the database once per photo)."""
    names = labels() if names is None else names
    for photo in photos:
        photo.angle_name = names.get(photo.angle, photo.angle)
    return photos
