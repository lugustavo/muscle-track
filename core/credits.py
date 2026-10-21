"""Credits of the exercise pictures and videos: who made them and under what licence.

Free media usually asks for attribution (CC BY, CC BY-SA); the trainer's own recordings need none but can be marked as such.
The credit is shown next to the media and listed on the public page /credits/."""
from django.utils.translation import gettext as _

from .models import Exercise


def label(exercise):
    """'Author - CC BY-SA' (what is known), or '' when the exercise has no credit."""
    parts = [exercise.media_credit.strip()] if exercise.media_credit.strip() else []
    if exercise.media_license:
        parts.append(exercise.get_media_license_display())
    return ' - '.join(parts)


def credited():
    """The active exercises that have a credit, by name (for the credits page)."""
    rows = []
    for exercise in Exercise.objects.filter(is_active=True).order_by('exercise_name'):
        if exercise.has_credit:
            rows.append({'name': exercise.exercise_name, 'credit': label(exercise), 'url': exercise.media_source_url})
    return rows


def sheet_credit(exercise):
    """The short credit shown under a picture on the workout sheet."""
    text = label(exercise)
    return (_('Mídia: %(credit)s') % {'credit': text}) if text else ''
