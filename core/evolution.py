"""The student's physical evolution: assessments with their photos, the chart, and the side-by-side photo comparison.

Shared by the trainer's file (Avaliações tab) and the student's own page, so both read the same numbers."""
from .models import ProgressPhoto

SEQUENCE_VALUES = {angle.value for angle in ProgressPhoto.SEQUENCE}
# (label, field) of the measures compared between two assessments
MEASURES = (
    ('Peso', 'weight_kg', 'kg'), ('% de gordura', 'body_fat_pct', '%'), ('Cintura', 'waist_cm', 'cm'),
    ('Quadril', 'hip_cm', 'cm'), ('Peitoral', 'chest_cm', 'cm'), ('Braço', 'arm_cm', 'cm'), ('Coxa', 'thigh_cm', 'cm'),
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
        'date': a.date.strftime('%d/%m/%y'),
        'weight': float(a.weight_kg) if a.weight_kg is not None else None,
        'body_fat': float(a.body_fat_pct) if a.body_fat_pct is not None else None,
        'waist': float(a.waist_cm) if a.waist_cm is not None else None,
    } for a in reversed(assessments)]
    return points if len(points) >= 2 else []


def comparable_angles(assessments):
    """Positions that have a photo in at least two assessments, in the standard order (the ones worth comparing)."""
    result = []
    for angle in ProgressPhoto.SEQUENCE:
        if sum(1 for a in assessments if any(s['angle'] == angle.value and s['photo'] for s in a.photo_slots)) >= 2:
            result.append(angle)
    return result


def compare(person, angle=None, before_id=None, after_id=None):
    """Two photos of the same position side by side: `before` (older) and `after` (newer), with the measure changes.

    Defaults to the first comparable position, the oldest and the newest assessment that have it."""
    assessments = assessments_with_photos(person)
    angles = comparable_angles(assessments)
    if angle not in SEQUENCE_VALUES:
        angle = angles[0].value if angles else ProgressPhoto.Angle.FRONT.value
    having = [a for a in reversed(assessments)   # oldest first
              if any(s['angle'] == angle and s['photo'] for s in a.photo_slots)]
    by_id = {a.pk: a for a in having}
    before = by_id.get(_int(before_id)) or (having[0] if having else None)
    after = by_id.get(_int(after_id)) or (having[-1] if having else None)
    if before and after and before.date > after.date:   # always older on the left
        before, after = after, before
    photo = lambda a: next((s['photo'] for s in a.photo_slots if s['angle'] == angle), None) if a else None  # noqa: E731
    changes = []
    if before and after and before.pk != after.pk:
        for label, field, unit in MEASURES:
            old, new = getattr(before, field), getattr(after, field)
            if old is not None and new is not None:
                changes.append({'label': label, 'old': old, 'new': new, 'delta': new - old, 'unit': unit})
    return {
        'angle': angle, 'angles': angles, 'having': list(reversed(having)), 'before': before, 'after': after,
        'before_photo': photo(before), 'after_photo': photo(after), 'changes': changes,
        'days': (after.date - before.date).days if before and after else 0,
    }


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
