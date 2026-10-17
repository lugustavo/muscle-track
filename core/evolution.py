"""The student's physical evolution: assessments with their photos, the chart, and the side-by-side photo comparison.

Shared by the trainer's file (Avaliações tab) and the student's own page, so both read the same numbers."""
from django.utils import dateformat
from django.utils.translation import gettext as _, gettext_lazy as _l

from .models import ProgressPhoto

SEQUENCE_VALUES = {angle.value for angle in ProgressPhoto.SEQUENCE}
# (label, field) of the measures compared between two assessments
MEASURES = (
    (_l('Peso'), 'weight_kg', 'kg'),
    # xgettext:no-python-format
    (_l('% de gordura'), 'body_fat_pct', '%'),
    (_l('Cintura'), 'waist_cm', 'cm'),
    (_l('Quadril'), 'hip_cm', 'cm'), (_l('Peitoral'), 'chest_cm', 'cm'), (_l('Braço'), 'arm_cm', 'cm'), (_l('Coxa'), 'thigh_cm', 'cm'),
)


def assessments_with_photos(person):
    """The person's assessments (newest first), each with `photo_slots` (one per standard position) and `other_photos`."""
    assessments = list(person.assessments.prefetch_related('photos'))
    for a in assessments:
        photos = list(a.photos.all())
        by_angle = {p.angle: p for p in photos}
        a.photo_slots = [{'angle': angle.value, 'label': angle.label, 'photo': by_angle.get(angle.value)}
                         for angle in ProgressPhoto.SEQUENCE]
        a.other_photos = [p for p in photos if p.angle not in SEQUENCE_VALUES]
        a.shown_measures = [(label, getattr(a, field), unit) for label, field, unit in MEASURES
                            if getattr(a, field) is not None]
    return assessments


def chart_points(assessments):
    """Weight, body fat and waist over time (oldest first) for the evolution chart; empty with fewer than 2 points."""
    points = [{
        'date': dateformat.format(a.date, _('d/m/y')),
        'weight': float(a.weight_kg) if a.weight_kg is not None else None,
        'body_fat': float(a.body_fat_pct) if a.body_fat_pct is not None else None,
        'waist': float(a.waist_cm) if a.waist_cm is not None else None,
    } for a in reversed(assessments)]
    return points if len(points) >= 2 else []


class Entry:
    """One photo that can sit on a side of the comparison: from an assessment, or a loose one (sent by the student or the trainer)."""

    def __init__(self, photo, assessment=None):
        self.photo, self.assessment = photo, assessment
        self.date = assessment.date if assessment else photo.date
        self.key = str(assessment.pk) if assessment else f'p{photo.pk}'
        if assessment:
            self.source = _('Avaliação')
        elif photo.uploaded_by_student:
            self.source = _('Enviada pelo aluno')
        else:
            self.source = _('Foto avulsa')

    @property
    def label(self):
        return f'{dateformat.format(self.date, _("d/m/Y"))} · {self.source}'


def comparable_loose(photos):
    """Of these photos with no assessment, the ones with a standard position (the ones a comparison can use)."""
    return [p for p in photos if p.angle in SEQUENCE_VALUES]


def entries_for(angle, assessments, loose=()):
    """The photos of one position, oldest first: those inside assessments and the loose ones."""
    found = [Entry(s['photo'], a) for a in assessments for s in a.photo_slots if s['angle'] == angle and s['photo']]
    found += [Entry(p) for p in loose if p.angle == angle]
    return sorted(found, key=lambda e: (e.date, e.photo.created_at))


def comparable_angles(assessments, loose=()):
    """Positions with at least two photos (in assessments or loose), in the standard order (the ones worth comparing)."""
    return [angle for angle in ProgressPhoto.SEQUENCE if len(entries_for(angle.value, assessments, loose)) >= 2]


def compare(person, angle=None, before_id=None, after_id=None):
    """Two photos of the same position side by side: `before` (older) and `after` (newer), with the measure changes.

    A side is an assessment (`before_id` is its id) or a loose photo (`p` + the photo id). Defaults to the first
    comparable position, the oldest and the newest photo of it. The measure changes need two assessments."""
    assessments = assessments_with_photos(person)
    loose = comparable_loose(person.progress_photos.filter(assessment__isnull=True))
    angles = comparable_angles(assessments, loose)
    if angle not in SEQUENCE_VALUES:
        angle = angles[0].value if angles else ProgressPhoto.Angle.FRONT.value
    having = entries_for(angle, assessments, loose)
    by_key = {e.key: e for e in having}
    before = by_key.get(str(before_id)) or (having[0] if having else None)
    after = by_key.get(str(after_id)) or (having[-1] if having else None)
    if before and after and (before.date, before.photo.created_at) > (after.date, after.photo.created_at):   # older on the left
        before, after = after, before
    changes = []
    if before and after and before.assessment and after.assessment and before.assessment.pk != after.assessment.pk:
        for label, field, unit in MEASURES:
            old, new = getattr(before.assessment, field), getattr(after.assessment, field)
            if old is not None and new is not None:
                changes.append({'label': label, 'old': old, 'new': new, 'delta': new - old, 'unit': unit})
    return {
        'angle': angle, 'angles': angles, 'having': list(reversed(having)), 'before_entry': before, 'after_entry': after,
        'before': before.assessment if before else None, 'after': after.assessment if after else None,
        'before_photo': before.photo if before else None, 'after_photo': after.photo if after else None,
        'changes': changes,
        'days': (after.date - before.date).days if before and after else 0,
    }


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
