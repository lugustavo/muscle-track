"""What the camera screen needs to help frame a progress photo: which guide (silhouette) fits each position, and the
previous photo of that position to lay over the live image, so every photo is taken with the same framing.

A position is free text (the trainer renames and adds them), so the guide is picked from the key/name of the position
by simple words; a position nobody recognises still gets the frame and the grid, only without a silhouette."""
import re

from django.urls import reverse

from . import photo_positions

# (guide, words) in order of priority: "lado esquerdo" is a left side, not a front just because it says "lado"
_RULES = (
    ('side-left', r'esquerd|left'),
    ('side-right', r'direit|right'),
    ('back', r'costas|back|posterior|dorsal'),
    ('front', r'frente|frontal|front|anterior'),
    ('side-right', r'lado|perfil|side|profile'),
)
FRAME_RATIO = (3, 4)   # width x height of every photo taken with the guides (portrait)


def guide_for(key, name=''):
    """'front', 'back', 'side-left', 'side-right' or '' (no silhouette) for a position."""
    text = f'{key} {name}'.lower()
    for guide, words in _RULES:
        if re.search(words, text):
            return guide
    return ''


def previous_photos(person, per_position=2):
    """{position key: the person's latest photos of it (newest first)}. Two are kept so the page can skip the one that
    belongs to the assessment being photographed now (a new photo replaces that one)."""
    found = {}
    for photo in person.progress_photos.exclude(angle=photo_positions.OTHER).order_by('-date', '-created_at'):
        photos = found.setdefault(photo.angle, [])
        if len(photos) < per_position:
            photos.append(photo)
    return found


def config(person):
    """The data the page hands to the camera script (as JSON): the frame and, per position, its guide and its last photos.

    Only the person's own photos, through the same private URLs the evolution page already uses (trainer or student)."""
    previous = previous_photos(person)
    positions = {}
    for position in photo_positions.active_positions():
        positions[position.value] = {
            'label': str(position.label),
            'guide': guide_for(position.value, position.label),
            'ghosts': [{'url': reverse('core:photo_thumb', kwargs={'pk': photo.pk}), 'date': photo.date.isoformat(),
                        'assessment': photo.assessment_id} for photo in previous.get(position.value, [])],
        }
    positions[photo_positions.OTHER] = {'label': str(photo_positions.labels()[photo_positions.OTHER]), 'guide': '', 'ghosts': []}
    return {'ratio': list(FRAME_RATIO), 'positions': positions}
